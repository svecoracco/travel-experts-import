"""Divers purchase-invoice import plugin — poort van
`travel-experts-backend/apps/main/app/plugins/divers/plugin.py`.

Pipeline
--------
1. Parse Excel-bestand (rijen gegroepeerd op Invoice number)
2. build_moves(): bulk-lookup partners/analytics, bouw gegroepeerde invoice-payloads
3. execute():
   a. Idempotentie-check via payment_reference
   b. Maak purchase invoices aan
   c. Post invoices (batch)

In tegenstelling tot Airplus maakt deze plugin GEEN misc/payout-entry aan en
voert ze GEEN reconciliatie uit.

Odoo-toegang herschreven naar `odoo_conn`.
"""

from __future__ import annotations

import logging
from collections import Counter
from pathlib import Path
from typing import Any

import pandas as pd

import odoo_conn
from plugins.base import (
    ExecutionResult,
    ImportPlugin,
    MovePayload,
    ParsedData,
    PluginMeta,
    ProgressCallback,
    ValidationResult,
)
from plugins.divers.excel_reader import (
    read_divers_excel,
    validate_divers_columns,
)
from plugins.divers.transform import (
    build_divers_config,
    build_invoice_payload,
    lookup_partners,
)
from shared.account_utils import (
    build_analytic_account_map,
    create_analytic_accounts,
    resolve_account_id,
    resolve_tax_id,
)
from shared.move_utils import post_moves

logger = logging.getLogger(__name__)

# Batch-groottes
IDEMPOTENCY_CHUNK = 200
POST_CHUNK = 50


def _safe_str(val: Any) -> str:
    """Retourneer val als gestripte string, lege string voor NaN/None."""
    if val is None:
        return ""
    try:
        if pd.isna(val):
            return ""
    except (TypeError, ValueError):
        pass
    return str(val).strip()


def _build_line_label(reference: str, file_number: str, traveller: str) -> str:
    """Bouw invoice-lijnnaam.

    Formaat: 'Ref: {Reference} - FN: {File Number} - Traveller: {Traveller}'
    Traveller-deel wordt weggelaten indien leeg (optionele kolom).
    """
    parts = []
    if reference:
        parts.append(f"Ref: {reference}")
    if file_number:
        parts.append(f"FN: {file_number}")
    if traveller:
        parts.append(f"Traveller: {traveller}")
    return " - ".join(parts) if parts else "Divers"


class DiversPlugin(ImportPlugin):
    # ------------------------------------------------------------------
    # Meta
    # ------------------------------------------------------------------

    def get_meta(self) -> PluginMeta:
        return PluginMeta(
            name="divers",
            display_name="Divers",
            accepted_extensions=[".xlsx", ".xls"],
            description=(
                "Reads Divers expense Excel exports and creates "
                "purchase invoices in Odoo. Rows are grouped by "
                "Invoice number."
            ),
        )

    # ------------------------------------------------------------------
    # Validate
    # ------------------------------------------------------------------

    def validate_file(self, file_path: Path) -> ValidationResult:
        if not file_path.exists():
            return ValidationResult(valid=False, errors=["File not found"])
        if file_path.suffix.lower() not in (".xlsx", ".xls"):
            return ValidationResult(valid=False, errors=["Expected .xlsx or .xls file"])

        ok, missing = validate_divers_columns(str(file_path))
        if not ok:
            return ValidationResult(
                valid=False,
                errors=[f"Missing required column(s): {', '.join(missing)}"],
            )

        try:
            df = read_divers_excel(str(file_path))
            return ValidationResult(valid=True, row_count=len(df))
        except Exception as exc:
            return ValidationResult(valid=False, errors=[str(exc)])

    # ------------------------------------------------------------------
    # Parse
    # ------------------------------------------------------------------

    def parse(self, file_path: Path, config: dict[str, Any]) -> ParsedData:
        df = read_divers_excel(str(file_path))
        return ParsedData(
            items=[df],
            metadata={"row_count": len(df)},
        )

    # ------------------------------------------------------------------
    # Build moves
    # ------------------------------------------------------------------

    def build_moves(
        self,
        parsed: ParsedData,
        odoo_client: Any,
        config: dict[str, Any],
        company_id: int,
        on_progress: ProgressCallback = None,
    ) -> list[MovePayload]:
        # Reset per-run state
        self._skip_rows: list[dict] = []
        self._needs_review_rows: list[dict] = []
        self._created_analytic_accounts: dict = {}
        self._analytic_create_error: str | None = None

        df: pd.DataFrame = parsed.items[0]
        if df.empty:
            return []

        cfg = build_divers_config(config, company_id)
        self._divers_cfg = cfg

        # Optionele boekingsdatum-override
        accounting_date_override: str | None = config.get("accounting_date") or None
        self._accounting_date_override = accounting_date_override

        # Optionele valuta-override
        currency_id: int | None = None
        divers_currency = config.get("divers_currency_id")
        if divers_currency:
            currency_id = int(divers_currency)

        account_cache: dict[int, int] = {}
        tax_cache: dict[str, int] = {}

        # --- Bulk partner-lookup (Supplier code → res.partner.ref) ---
        raw_codes = [
            _safe_str(v) for v in df.get("Supplier code", pd.Series(dtype=str)).tolist()
        ]
        unique_codes = sorted({c for c in raw_codes if c})
        partner_map = lookup_partners(odoo_client, unique_codes, company_id)

        # --- Bulk analytic-account-lookup ---
        raw_dossiers = [
            _safe_str(v) for v in df.get("File number", pd.Series(dtype=str)).tolist()
        ]
        unique_dossiers = sorted({d for d in raw_dossiers if d})
        analytic_map = build_analytic_account_map(
            odoo_client, unique_dossiers, company_id
        )

        # Auto-creëer ontbrekende analytic accounts
        missing_dossiers = [d for d in unique_dossiers if d not in analytic_map]
        if missing_dossiers:
            analytic_plan = str(
                config.get("divers_analytic_plan") or "File number"
            ).strip()
            try:
                newly_created = create_analytic_accounts(
                    odoo_client,
                    missing_dossiers,
                    company_id,
                    analytic_plan,
                )
                analytic_map.update(newly_created)
                self._created_analytic_accounts = newly_created
                logger.info(
                    "[divers] Auto-created %d analytic account(s): %s",
                    len(newly_created),
                    list(newly_created.keys()),
                )
            except Exception as exc_create:
                logger.error(
                    "[divers] Failed to auto-create analytic " "accounts: %s",
                    exc_create,
                )
                self._analytic_create_error = str(exc_create)

        # --- Groepeer rijen op Invoice number ---
        df["_invoice_number_str"] = df.get(
            "Invoice number", pd.Series(dtype=str)
        ).apply(_safe_str)
        groups = df.groupby("_invoice_number_str", sort=False)

        moves: list[MovePayload] = []
        total_groups = len(groups)

        for grp_idx, (inv_num, grp_df) in enumerate(groups):
            if on_progress and grp_idx % 10 == 0:
                on_progress(
                    "building",
                    grp_idx,
                    total_groups,
                    f"Building invoices: {grp_idx}/{total_groups}",
                )

            inv_num_str = str(inv_num).strip()
            if not inv_num_str:
                for _, row in grp_df.iterrows():
                    self._skip_rows.append(
                        self._make_skip_row(row, "Missing Invoice number")
                    )
                continue

            # Verzamel geldige lijnen voor deze groep
            invoice_lines: list[dict[str, Any]] = []
            net_total = 0.0
            first_row = None

            for _, row in grp_df.iterrows():
                if first_row is None:
                    first_row = row

                supplier_code = _safe_str(row.get("Supplier code"))
                reference = _safe_str(row.get("Reference"))
                traveller = _safe_str(row.get("Traveller"))

                dossier = _safe_str(row.get("File number"))
                btw_code = _safe_str(row.get("Vat code"))

                # --- Bedrag ---
                amount_raw = row.get("net amount")
                try:
                    amount = round(
                        (
                            float(amount_raw)
                            if amount_raw is not None and not pd.isna(amount_raw)
                            else 0.0
                        ),
                        2,
                    )
                except (TypeError, ValueError):
                    amount = 0.0

                # --- Datum ---
                inv_date = row.get("Invoice date")
                try:
                    date_is_null = pd.isnull(inv_date)
                except (TypeError, ValueError):
                    date_is_null = inv_date is None

                if date_is_null:
                    self._skip_rows.append(
                        self._make_skip_row(row, "Missing Invoice date")
                    )
                    continue

                # --- Nulbedrag ---
                if amount == 0.0:
                    self._skip_rows.append(self._make_skip_row(row, "Zero net amount"))
                    continue

                # --- Partner ---
                partner_id = partner_map.get(supplier_code)
                if not partner_id:
                    self._skip_rows.append(
                        self._make_skip_row(
                            row,
                            f"Partner not found for Supplier code "
                            f"'{supplier_code}'",
                        )
                    )
                    continue

                # --- GL-account ---
                gl_raw = row.get("ledger account")
                try:
                    gl_code = (
                        int(float(gl_raw))
                        if gl_raw is not None and not pd.isna(gl_raw)
                        else None
                    )
                except (TypeError, ValueError):
                    gl_code = None

                if not gl_code:
                    self._skip_rows.append(
                        self._make_skip_row(
                            row,
                            f"Invalid or missing ledger account " f"'{gl_raw}'",
                        )
                    )
                    continue

                try:
                    gl_account_id = resolve_account_id(
                        odoo_client,
                        gl_code,
                        company_id,
                        account_cache,
                    )
                except Exception as exc:
                    self._skip_rows.append(
                        self._make_skip_row(
                            row,
                            f"GL account {gl_code} not found " f"in Odoo: {exc}",
                        )
                    )
                    continue

                # --- BTW ---
                tax_id: int | None = None
                if btw_code:
                    try:
                        tax_id = resolve_tax_id(
                            odoo_client,
                            btw_code,
                            company_id,
                            tax_cache,
                        )
                    except Exception:
                        self._skip_rows.append(
                            self._make_skip_row(
                                row,
                                f"VAT code not found in Odoo: " f"'{btw_code}'",
                            )
                        )
                        continue

                # --- Analytic-verdeling ---
                analytic_id = analytic_map.get(dossier) if dossier else None
                analytic_distribution = (
                    {str(analytic_id): 100.0} if analytic_id else None
                )

                if dossier and not analytic_id:
                    self._needs_review_rows.append(
                        {
                            **self._make_skip_row(
                                row,
                                "No analytic account for " "File number",
                            ),
                            "File number": dossier,
                        }
                    )

                # --- Bouw lijn-dict ---
                label = _build_line_label(reference, dossier, traveller)

                line: dict[str, Any] = {
                    "account_id": gl_account_id,
                    "name": label,
                    "quantity": 1,
                    "price_unit": amount,  # hieronder aangepast voor refunds
                }
                if analytic_distribution:
                    line["analytic_distribution"] = analytic_distribution
                if tax_id is not None:
                    line["tax_ids"] = [(6, 0, [tax_id])]

                invoice_lines.append(line)
                net_total += amount

            # --- Sla groep over indien geen geldige lijnen ---
            if not invoice_lines:
                logger.info(
                    "[divers] Group '%s' skipped: no valid rows",
                    inv_num_str,
                )
                continue

            # --- Bepaal move_type en pas bedragen aan ---
            if net_total < 0:
                move_type = "in_refund"
                for line in invoice_lines:
                    line["price_unit"] = -line["price_unit"]
            else:
                move_type = "in_invoice"

            # --- Invoice-niveau-velden uit eerste rij ---
            invoice_date = pd.to_datetime(first_row["Invoice date"]).date().isoformat()
            ref_label = str(inv_num_str)
            payment_ref = str(inv_num_str)

            # Partner uit eerste rij (alle rijen in groep zouden delen)
            first_supplier_code = _safe_str(first_row.get("Supplier code"))
            first_partner_id = partner_map.get(first_supplier_code)
            if not first_partner_id:
                # Terugval: gebruik partner uit eerste geldige lijn
                first_partner_id = partner_map.get(
                    _safe_str(grp_df.iloc[0].get("Supplier code"))
                )

            payload = build_invoice_payload(
                cfg=cfg,
                partner_id=first_partner_id,
                invoice_date=invoice_date,
                accounting_date=accounting_date_override,
                ref=ref_label,
                payment_reference=payment_ref,
                lines=invoice_lines,
                move_type=move_type,
                currency_id=currency_id,
            )

            moves.append(
                MovePayload(
                    payload=payload,
                    move_type=move_type,
                    ref=payment_ref,
                    meta={
                        "partner_id": first_partner_id,
                        "invoice_number": inv_num_str,
                        "line_count": len(invoice_lines),
                        "invoice_date": invoice_date,
                        "net_total": round(net_total, 2),
                    },
                )
            )

        logger.info(
            "[divers] build_moves: %d invoices built, %d skipped, " "%d needs-review",
            len(moves),
            len(self._skip_rows),
            len(self._needs_review_rows),
        )
        return moves

    # ------------------------------------------------------------------
    # Execute
    # ------------------------------------------------------------------

    def execute(
        self,
        moves: list[MovePayload],
        odoo_client: Any,
        company_id: int,
        dry_run: bool = False,
        auto_post: bool = True,
        auto_reconcile: bool = True,
        on_progress: ProgressCallback = None,
    ) -> ExecutionResult:
        result = ExecutionResult()

        # Voeg skip/review-rijen toe uit build_moves
        result.skip_report_rows = list(getattr(self, "_skip_rows", []))
        result.skipped = len(result.skip_report_rows)
        needs_review_rows: list[dict] = list(getattr(self, "_needs_review_rows", []))

        # Log skip-reden-samenvatting
        if result.skip_report_rows:
            reason_counts = Counter(
                row.get("reason", "Unknown") for row in result.skip_report_rows
            )
            for reason, count in sorted(reason_counts.items(), key=lambda x: -x[1]):
                result.log_messages.append(f"Skipped {count}: {reason}")

        # Log auto-created analytic accounts
        created_analytics: dict = getattr(self, "_created_analytic_accounts", {})
        if created_analytics:
            fns = ", ".join(sorted(created_analytics.keys()))
            result.log_messages.append(
                f"Created {len(created_analytics)} analytic " f"account(s): {fns}"
            )
        analytic_create_error: str | None = getattr(
            self, "_analytic_create_error", None
        )
        if analytic_create_error:
            result.log_messages.append(
                "Warning: Failed to auto-create analytic "
                f"accounts — {analytic_create_error}"
            )

        if not moves:
            if needs_review_rows:
                result.extra_report_data["Needs Review"] = needs_review_rows
            return result

        total_moves = len(moves)

        # ----------------------------------------------------------
        # Idempotentie-check: batch-search op payment_reference,
        # vergelijk vervolgens ref + invoice_date + amount_total om te
        # beslissen of overgeslagen moet worden.
        # ----------------------------------------------------------
        all_refs = [m.ref for m in moves]
        # Set van (ref, invoice_date, amount)-tuples die al in Odoo staan
        existing_keys: set[tuple[str, str, float]] = set()
        for i in range(0, len(all_refs), IDEMPOTENCY_CHUNK):
            chunk = all_refs[i : i + IDEMPOTENCY_CHUNK]
            try:
                found = odoo_conn.search_read(
                    odoo_client,
                    "account.move",
                    [
                        ("payment_reference", "in", chunk),
                        ("company_id", "=", company_id),
                    ],
                    [
                        "payment_reference",
                        "ref",
                        "invoice_date",
                        "amount_total",
                    ],
                )
                for rec in found:
                    pr = str(rec.get("payment_reference") or "").strip()
                    inv_date = str(rec.get("invoice_date") or "").strip()
                    amt = round(float(rec.get("amount_total") or 0.0), 2)
                    if pr:
                        existing_keys.add((pr, inv_date, amt))
            except Exception as exc:
                logger.warning("[divers] Idempotency check failed: %s", exc)

        logger.info(
            "[divers] Idempotency: %d existing entries found " "for %d refs",
            len(existing_keys),
            len(all_refs),
        )

        # ----------------------------------------------------------
        # Maak invoices aan
        # ----------------------------------------------------------
        all_invoice_ids: list[int] = []
        error_rows: list[dict] = []
        for idx, m in enumerate(moves):
            if on_progress and idx % 10 == 0:
                on_progress(
                    "executing",
                    idx,
                    total_moves,
                    f"Creating invoices: {idx}/{total_moves}",
                )

            result.items_processed += 1

            # Bouw de idempotentiesleutel voor deze move
            m_date = str(m.meta.get("invoice_date", ""))
            m_amt = round(abs(m.meta.get("net_total", 0.0)), 2)
            idem_key = (m.ref, m_date, m_amt)

            if idem_key in existing_keys:
                result.skipped += 1
                result.log_messages.append(
                    f"Duplicate skipped: ref={m.ref} " f"date={m_date} amount={m_amt}"
                )
                continue

            if dry_run:
                result.created += 1
                continue

            try:
                move_id = odoo_conn.create(odoo_client, "account.move", m.payload)
                all_invoice_ids.append(move_id)
                result.created += 1
            except Exception as exc:
                result.errors += 1
                err_msg = str(exc)
                result.log_messages.append(f"Create error ref={m.ref}: {err_msg}")
                error_rows.append(
                    {
                        "reason": f"Create error: {err_msg}",
                        "Invoice number": m.meta.get("invoice_number", ""),
                        "invoice_date": m_date,
                        "net amount": m.meta.get("net_total", ""),
                        "payment_reference": m.ref,
                    }
                )

        # Vul Needs Review-tab
        if needs_review_rows:
            result.extra_report_data["Needs Review"] = needs_review_rows

        if dry_run:
            result.log_messages.append(
                f"[dry-run] Would create {result.created} " f"invoice(s)"
            )
            if needs_review_rows:
                result.log_messages.append(
                    f"[dry-run] {len(needs_review_rows)} "
                    "invoice line(s) would be created without "
                    "analytic distribution (Needs Review)"
                )
            return result

        if not all_invoice_ids:
            result.log_messages.append("No new invoices created — nothing to post.")
            if error_rows:
                result.extra_report_data["Errors"] = error_rows
            return result

        # ----------------------------------------------------------
        # Post invoices (batch)
        # ----------------------------------------------------------
        if auto_post:
            if on_progress:
                on_progress(
                    "posting",
                    0,
                    len(all_invoice_ids),
                    f"Posting {len(all_invoice_ids)} invoices...",
                )
            for i in range(0, len(all_invoice_ids), POST_CHUNK):
                chunk = all_invoice_ids[i : i + POST_CHUNK]
                try:
                    post_moves(odoo_client, chunk, company_id)
                except Exception as exc:
                    logger.warning(
                        "[divers] Batch post failed, falling " "back per-move: %s",
                        exc,
                    )
                    for mid in chunk:
                        try:
                            post_moves(odoo_client, [mid], company_id)
                        except Exception as exc2:
                            result.errors += 1
                            err_msg = str(exc2)
                            result.log_messages.append(
                                f"Post error move_id={mid}: " f"{err_msg}"
                            )
                            error_rows.append(
                                {
                                    "reason": (f"Post error: {err_msg}"),
                                    "move_id": mid,
                                }
                            )

        # Vul Errors-tab indien er fouten waren
        if error_rows:
            result.extra_report_data["Errors"] = error_rows

        return result

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _make_skip_row(row: Any, reason: str) -> dict:
        """Bouw een skip-report-rij-dict uit een DataFrame-rij."""
        return {
            "reason": reason,
            "Invoice number": _safe_str(row.get("Invoice number")),
            "Reference": _safe_str(row.get("Reference")),
            "Supplier code": _safe_str(row.get("Supplier code")),
            "File number": _safe_str(row.get("File number")),
            "net amount": row.get("net amount", ""),
        }
