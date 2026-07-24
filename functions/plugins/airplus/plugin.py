"""Airplus travel expense import plugin — poort van
`travel-experts-backend/apps/main/app/plugins/airplus/plugin.py`.

Pipeline
--------
1. Parse Excel-bestand (één rij = één purchase invoice)
2. build_moves(): bulk-lookup partners/valuta/analytics, bouw invoice-payloads
3. execute():
   a. Idempotentie-check via payment_reference + move_type
   b. Maak purchase invoices aan
   c. Post invoices (batch)
   d. Bouw + maak één geconsolideerde payout misc entry
   e. Post misc entry
   f. Reconcilieer elke invoice's AP-lijn tegen de misc-entry supplier-lijn

Odoo-toegang herschreven naar `odoo_conn` (pakket-transport, JSON-2); reader/
transform-logica en de `payment_reference`-idempotentie (`{factuur_nr}-{i:04d}`)
1-op-1 behouden.
"""

from __future__ import annotations

import logging
from collections import Counter
from pathlib import Path
from typing import Any

import pandas as pd

import odoo_conn
from plugins.airplus.excel_reader import (
    read_airplus_excel,
    validate_airplus_columns,
)
from plugins.airplus.transform import (
    AirplusConfig,
    AirplusPayoutLine,
    build_airplus_config,
    build_invoice_payload,
    build_payout_misc_payload,
    lookup_currencies,
    lookup_partners,
)
from plugins.base import (
    ExecutionResult,
    ImportPlugin,
    MovePayload,
    ParsedData,
    PluginMeta,
    ProgressCallback,
    ValidationResult,
)
from shared.account_utils import (
    build_analytic_account_map,
    create_analytic_accounts,
    resolve_account_id,
    resolve_tax_id,
)
from shared.move_utils import post_moves

logger = logging.getLogger(__name__)

# Batch-groottes (zelfde conventies als de BSP-plugin)
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


class AirplusPlugin(ImportPlugin):
    # ------------------------------------------------------------------
    # Meta
    # ------------------------------------------------------------------

    def get_meta(self) -> PluginMeta:
        return PluginMeta(
            name="airplus",
            display_name="Airplus",
            accepted_extensions=[".xlsx", ".xls"],
            description=(
                "Reads Airplus travel expense Excel exports and creates "
                "purchase invoices + payout misc entry in Odoo."
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

        ok, missing = validate_airplus_columns(str(file_path))
        if not ok:
            return ValidationResult(
                valid=False,
                errors=[f"Missing required column(s): {', '.join(missing)}"],
            )

        try:
            df = read_airplus_excel(str(file_path))
            return ValidationResult(valid=True, row_count=len(df))
        except Exception as exc:
            return ValidationResult(valid=False, errors=[str(exc)])

    # ------------------------------------------------------------------
    # Parse
    # ------------------------------------------------------------------

    def parse(self, file_path: Path, config: dict[str, Any]) -> ParsedData:
        df = read_airplus_excel(str(file_path))
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
        # Reset per-run singleton state
        self._skip_rows: list[dict] = []
        self._needs_review_rows: list[dict] = []
        self._payout_lines: list[AirplusPayoutLine] = []
        self._airplus_cfg: AirplusConfig | None = None
        self._created_analytic_accounts: dict = {}
        self._analytic_create_error: str | None = None

        df: pd.DataFrame = parsed.items[0]
        if df.empty:
            return []

        cfg = build_airplus_config(config, company_id)
        self._airplus_cfg = cfg

        # Optionele per-import boekingsdatum-override (YYYY-MM-DD-string of None).
        # Indien gezet, gebruikt als move-`date` (boekingsdatum) voor alle invoices.
        # Indien afwezig wordt invoice_date (Bookings date) gebruikt — Odoo past
        # dan zijn eigen lock-date-logica toe om de boekingsdatum te bepalen.
        accounting_date_override: str | None = config.get("accounting_date") or None
        self._accounting_date_override = accounting_date_override

        account_cache: dict[int, int] = {}
        tax_cache: dict[str, int] = {}

        # --- Bulk partner-lookup (Leverancier code → res.partner.ref) ---
        raw_codes = [
            _safe_str(v) for v in df.get("Supplier code", pd.Series(dtype=str)).tolist()
        ]
        unique_codes = sorted({c for c in raw_codes if c})
        partner_map = lookup_partners(odoo_client, unique_codes, company_id)

        # --- Fallback-valuta voor rijen met lege Currency ---
        fallback_currency = config.get("airplus_fallback_currency", "").strip()
        if fallback_currency and "Currency" in df.columns:
            empty_mask = df["Currency"].isna() | (df["Currency"].str.strip() == "")
            fill_count = int(empty_mask.sum())
            if fill_count:
                df["Currency"] = df["Currency"].fillna(fallback_currency)
                df.loc[df["Currency"].str.strip() == "", "Currency"] = fallback_currency
                logger.info(
                    "[airplus] Applied fallback currency '%s' to %d rows with empty Currency",
                    fallback_currency,
                    fill_count,
                )

        # --- Bulk valuta-lookup (Munteenheid → res.currency) ---
        raw_currencies = [
            _safe_str(v) for v in df.get("Currency", pd.Series(dtype=str)).tolist()
        ]
        unique_currencies = sorted({c for c in raw_currencies if c})
        currency_map = lookup_currencies(odoo_client, unique_currencies)

        # --- Bulk analytic-account-lookup (Dossiernummer → file number → analytic) ---
        raw_dossiers = [
            _safe_str(v) for v in df.get("File number", pd.Series(dtype=str)).tolist()
        ]
        unique_dossiers = sorted({d for d in raw_dossiers if d})
        analytic_map = build_analytic_account_map(
            odoo_client, unique_dossiers, company_id
        )

        # Auto-creëer analytic accounts voor dossiers die nog niet in Odoo staan
        missing_dossiers = [d for d in unique_dossiers if d not in analytic_map]
        if missing_dossiers:
            analytic_plan = str(
                config.get("airplus_analytic_plan") or "File number"
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
                    "[airplus] Auto-created %d analytic account(s): %s",
                    len(newly_created),
                    list(newly_created.keys()),
                )
            except Exception as exc_create:
                logger.error(
                    "[airplus] Failed to auto-create analytic accounts: %s", exc_create
                )
                self._analytic_create_error = str(exc_create)

        moves: list[MovePayload] = []
        total_rows = len(df)
        suffix_counter = 0  # verhoogt per succesvol gebouwde invoice

        for idx, (_, row) in enumerate(df.iterrows()):
            if on_progress and idx % 20 == 0:
                on_progress(
                    "building",
                    idx,
                    total_rows,
                    f"Building invoices: {idx}/{total_rows}",
                )

            leverancier_code = _safe_str(row.get("Supplier code"))
            factuur_nr = _safe_str(row.get("Invoice number"))
            naam = _safe_str(row.get("Reference"))
            dossier = _safe_str(row.get("File number"))
            munteenheid = _safe_str(row.get("Currency"))
            btw_code = _safe_str(row.get("VAT code"))

            # --- Bedrag ---
            bedrag_raw = row.get("Total amount")
            try:
                bedrag = (
                    float(bedrag_raw)
                    if bedrag_raw is not None and not pd.isna(bedrag_raw)
                    else 0.0
                )
            except (TypeError, ValueError):
                bedrag = 0.0

            # --- Basis skip-rij-dict voor foutrapportage ---
            def _skip_row(reason: str, **extra: Any) -> dict:
                return {
                    "reason": reason,
                    "Invoice number": factuur_nr,
                    "Reference": naam,
                    "Supplier code": leverancier_code,
                    "File number": dossier,
                    "Total amount": bedrag,
                    "Currency": munteenheid,
                    **extra,
                }

            # --- Datum ---
            boekdatum = row.get("Bookings date")
            try:
                date_is_null = pd.isnull(boekdatum)
            except (TypeError, ValueError):
                date_is_null = boekdatum is None

            if date_is_null:
                self._skip_rows.append(_skip_row("Missing Bookings date"))
                continue

            invoice_date = pd.to_datetime(boekdatum).date().isoformat()

            # --- Nulbedrag ---
            if bedrag == 0.0:
                self._skip_rows.append(_skip_row("Zero amount"))
                continue

            # --- Partner ---
            partner_id = partner_map.get(leverancier_code)
            if not partner_id:
                self._skip_rows.append(
                    _skip_row(
                        f"Partner not found for Supplier code '{leverancier_code}'"
                    )
                )
                continue

            # --- Valuta (waarschuwing enkel — ga door met Odoo-default indien niet gevonden) ---
            currency_id = currency_map.get(munteenheid) if munteenheid else None
            if munteenheid and not currency_id:
                logger.warning(
                    "[airplus] Currency '%s' not found in Odoo — using company default for ref=%s",
                    munteenheid,
                    factuur_nr,
                )

            # --- GL-account (Ledger account) ---
            boekingsreknr_raw = row.get("Ledger account")
            try:
                gl_code = (
                    int(float(boekingsreknr_raw))
                    if boekingsreknr_raw is not None and not pd.isna(boekingsreknr_raw)
                    else None
                )
            except (TypeError, ValueError):
                gl_code = None

            if not gl_code:
                self._skip_rows.append(
                    _skip_row(
                        f"Invalid or missing Ledger account '{boekingsreknr_raw}'"
                    )
                )
                continue

            try:
                gl_account_id = resolve_account_id(
                    odoo_client, gl_code, company_id, account_cache
                )
            except Exception as exc:
                self._skip_rows.append(
                    _skip_row(f"GL account {gl_code} not found in Odoo: {exc}")
                )
                continue

            # --- Analytic-verdeling ---
            analytic_id = analytic_map.get(dossier) if dossier else None
            analytic_distribution = {str(analytic_id): 100.0} if analytic_id else None

            if dossier and not analytic_id:
                # Toch de invoice aanmaken, maar markeren voor review
                self._needs_review_rows.append(
                    {
                        **_skip_row("No analytic account in Odoo for Dossiernummer"),
                        "Dossiernummer": dossier,
                    }
                )

            # --- BTW ---
            tax_id: int | None = None
            if btw_code:
                try:
                    tax_id = resolve_tax_id(
                        odoo_client, btw_code, company_id, tax_cache
                    )
                except Exception:
                    self._skip_rows.append(
                        _skip_row(
                            f"VAT code not found in Odoo: '{btw_code}'",
                            BTW_Code=btw_code,
                        )
                    )
                    continue

            # --- Ken unieke suffix toe voor deze rij ---
            suffix_counter += 1
            unique_ref = f"{factuur_nr}-{suffix_counter:04d}"

            # --- Bouw invoice-payload ---
            # ref = Reference (leveranciersreferentie zoals getoond in Odoo)
            # payment_reference = {Factuur Nummer}-{suffix} (unieke idempotentiesleutel)
            payload = build_invoice_payload(
                cfg=cfg,
                partner_id=partner_id,
                currency_id=currency_id,
                gl_account_id=gl_account_id,
                invoice_date=invoice_date,
                accounting_date=accounting_date_override,
                amount=bedrag,
                label=naam or factuur_nr,
                payment_reference=unique_ref,
                analytic_distribution=analytic_distribution,
                tax_id=tax_id,
            )

            moves.append(
                MovePayload(
                    payload=payload,
                    move_type=payload["move_type"],
                    ref=unique_ref,
                    meta={
                        "partner_id": partner_id,
                        "currency_id": currency_id or 0,
                        "naam": naam,
                    },
                )
            )

            # Verzamel voor de geconsolideerde payout misc entry
            self._payout_lines.append(
                AirplusPayoutLine(
                    ref=unique_ref,
                    amount=bedrag,
                    partner_id=partner_id,
                    currency_id=currency_id,
                    naam=naam,
                    factuur_nr=factuur_nr,
                )
            )

        logger.info(
            "[airplus] build_moves: %d invoices built, %d skipped, %d needs-review",
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

        # Voeg skip/review-rijen toe (gebouwd in build_moves)
        result.skip_report_rows = list(getattr(self, "_skip_rows", []))
        result.skipped = len(result.skip_report_rows)
        needs_review_rows: list[dict] = list(getattr(self, "_needs_review_rows", []))
        cfg: AirplusConfig | None = getattr(self, "_airplus_cfg", None)
        payout_lines: list[AirplusPayoutLine] = list(getattr(self, "_payout_lines", []))

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
                f"Created {len(created_analytics)} analytic account(s): {fns}"
            )
        analytic_create_error: str | None = getattr(
            self, "_analytic_create_error", None
        )
        if analytic_create_error:
            result.log_messages.append(
                f"Warning: Failed to auto-create analytic accounts — {analytic_create_error}"
            )

        if not moves:
            if needs_review_rows:
                result.extra_report_data["Needs Review"] = needs_review_rows
            return result

        total_moves = len(moves)

        # ------------------------------------------------------------------
        # Idempotentie-check: batch-search op payment_reference + move_type
        # ------------------------------------------------------------------
        all_refs = [m.ref for m in moves]
        existing_set: set[tuple[str, str]] = set()
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
                    ["payment_reference", "move_type"],
                )
                for rec in found:
                    pr = str(rec.get("payment_reference") or "").strip()
                    if pr:
                        existing_set.add((pr, rec["move_type"]))
            except Exception as exc:
                logger.warning("[airplus] Idempotency check failed: %s", exc)

        logger.info(
            "[airplus] Idempotency: %d already exist out of %d refs",
            len(existing_set),
            len(all_refs),
        )

        # ------------------------------------------------------------------
        # Maak invoices aan
        # ------------------------------------------------------------------
        all_invoice_ids: list[int] = []
        ref_to_invoice: dict[str, int] = {}  # ref → Odoo move_id
        ref_to_partner: dict[str, int] = {}  # ref → partner_id (voor reconciliatie)
        for idx, m in enumerate(moves):
            if on_progress and idx % 10 == 0:
                on_progress(
                    "executing",
                    idx,
                    total_moves,
                    f"Creating invoices: {idx}/{total_moves}",
                )

            result.items_processed += 1
            already_exists = (m.ref, m.move_type) in existing_set

            if already_exists:
                result.skipped += 1
                continue

            if dry_run:
                result.created += 1
                continue

            try:
                move_id = odoo_conn.create(odoo_client, "account.move", m.payload)
                all_invoice_ids.append(move_id)
                ref_to_invoice[m.ref] = move_id
                ref_to_partner[m.ref] = m.meta.get("partner_id", 0)
                result.created += 1
            except Exception as exc:
                result.errors += 1
                result.log_messages.append(f"Create error ref={m.ref}: {exc}")
                result.skip_report_rows.append(
                    {
                        "reason": f"Create error: {exc}",
                        "Invoice number": m.ref,
                    }
                )

        # Vul Needs Review-tab (zowel dry-run als echte import)
        if needs_review_rows:
            result.extra_report_data["Needs Review"] = needs_review_rows

        if dry_run:
            payout_count = len(
                [
                    pl
                    for pl in payout_lines
                    if pl.ref not in {r for (r, _) in existing_set}
                ]
            )
            result.log_messages.append(
                f"[dry-run] Would create {result.created} invoice(s) + 1 payout misc entry "
                f"({payout_count} line(s))"
            )
            if needs_review_rows:
                result.log_messages.append(
                    f"[dry-run] {len(needs_review_rows)} invoice(s) would be created without analytic "
                    "distribution (Needs Review)"
                )
            return result

        if not all_invoice_ids:
            result.log_messages.append(
                "No new invoices created — nothing to post or reconcile."
            )
            return result

        # ------------------------------------------------------------------
        # Post invoices (batch)
        # ------------------------------------------------------------------
        posted_ids: set[int] = set()
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
                    posted_ids.update(chunk)
                except Exception as exc:
                    logger.warning(
                        "[airplus] Batch post failed, falling back per-move: %s", exc
                    )
                    for mid in chunk:
                        try:
                            post_moves(odoo_client, [mid], company_id)
                            posted_ids.add(mid)
                        except Exception as exc2:
                            result.errors += 1
                            result.log_messages.append(
                                f"Post error move_id={mid}: {exc2}"
                            )

        # ------------------------------------------------------------------
        # Bouw + maak payout-misc-entry (alleen voor invoices die aangemaakt werden)
        # ------------------------------------------------------------------
        active_payout_lines = [pl for pl in payout_lines if pl.ref in ref_to_invoice]
        misc_id: int | None = None

        if active_payout_lines and cfg:
            if on_progress:
                on_progress("executing", 0, 1, "Creating payout misc entry...")

            # Misc-entry-datum: gebruik accounting_date-override indien gezet;
            # anders de laatste invoice-datum uit de aangemaakte invoices.
            accounting_date_override: str | None = getattr(
                self, "_accounting_date_override", None
            )
            latest_invoice_date = max(
                (
                    m.payload.get("invoice_date", "")
                    for m in moves
                    if m.ref in ref_to_invoice
                ),
                default="",
            )
            latest_date = accounting_date_override or latest_invoice_date
            # Periode-label: YYYYMM
            period_label = latest_date[:7].replace("-", "") if latest_date else "000000"
            account_cache_misc: dict[int, int] = {}

            try:
                misc_payload = build_payout_misc_payload(
                    active_payout_lines,
                    cfg,
                    latest_date,
                    period_label,
                    account_cache_misc,
                    odoo_client,
                )
                misc_id = odoo_conn.create(odoo_client, "account.move", misc_payload)
                result.log_messages.append(
                    f"Payout misc entry created: id={misc_id} ref={misc_payload.get('ref')} "
                    f"({len(active_payout_lines)} line(s))"
                )
            except Exception as exc:
                result.errors += 1
                result.log_messages.append(f"Payout misc entry error: {exc}")

        # ------------------------------------------------------------------
        # Post misc entry
        # ------------------------------------------------------------------
        misc_posted = False
        if misc_id and auto_post:
            try:
                post_moves(odoo_client, [misc_id], company_id)
                misc_posted = True
            except Exception as exc:
                result.log_messages.append(
                    f"Post error payout misc entry id={misc_id}: {exc}"
                )

        # ------------------------------------------------------------------
        # Reconcilieer: elke invoice-AP-lijn tegen zijn matchende misc-entry-lijn
        # Matched op naam "{Reference} - {Factuur Nummer}-{suffix}" aan de
        # misc-entry-kant — garandeert uniciteit zelfs wanneer dezelfde
        # partner of Factuur Nummer meerdere keren in het bestand voorkomt.
        # ------------------------------------------------------------------
        if auto_reconcile and misc_id and misc_posted and cfg:
            if on_progress:
                on_progress(
                    "reconciling", 0, len(ref_to_invoice), "Reconciling invoices..."
                )

            account_cache_rec: dict[int, int] = {}
            try:
                suppliers_account_id = resolve_account_id(
                    odoo_client,
                    cfg.suppliers_gl_account,
                    company_id,
                    account_cache_rec,
                )
            except Exception as exc:
                result.log_messages.append(
                    f"Reconcile skipped — could not resolve suppliers GL account "
                    f"({cfg.suppliers_gl_account}): {exc}"
                )
                return result

            # Bouw lookup: unique_ref → misc-entry-440000-lijnnaam
            # Formaat: "{Reference} - {Factuur Nummer}-{suffix}"
            ref_to_misc_name: dict[str, str] = {
                pl.ref: f"{pl.naam} - {pl.ref}" if pl.naam else pl.ref
                for pl in payout_lines
            }

            ctx = {"allowed_company_ids": [company_id], "company_id": company_id}
            reconciled = 0
            for ref, invoice_id in ref_to_invoice.items():
                if invoice_id not in posted_ids:
                    continue
                misc_line_name = ref_to_misc_name.get(ref, ref)
                try:
                    # Invoice-AP-lijn op suppliers_gl_account
                    inv_lines = odoo_conn.search_read(
                        odoo_client,
                        "account.move.line",
                        [
                            ("move_id", "=", invoice_id),
                            ("account_id", "=", suppliers_account_id),
                            ("reconciled", "=", False),
                        ],
                        ["id", "balance"],
                    )
                    # Misc-entry-lijn voor DEZE invoice — gematcht op unieke naam
                    # "{Reference} - {Factuur Nummer}-{suffix}"
                    misc_lines = odoo_conn.search_read(
                        odoo_client,
                        "account.move.line",
                        [
                            ("move_id", "=", misc_id),
                            ("account_id", "=", suppliers_account_id),
                            ("name", "=", misc_line_name),
                            ("reconciled", "=", False),
                        ],
                        ["id", "balance"],
                    )
                    if not inv_lines or not misc_lines:
                        result.log_messages.append(
                            f"Reconcile skipped ref={ref}: "
                            f"invoice AP lines={len(inv_lines)} misc lines={len(misc_lines)}"
                        )
                        continue
                    inv_line = inv_lines[0]
                    misc_line = misc_lines[0]
                    inv_bal = round(float(inv_line.get("balance") or 0.0), 2)
                    misc_bal = round(float(misc_line.get("balance") or 0.0), 2)
                    if round(abs(inv_bal) - abs(misc_bal), 2) != 0.0:
                        result.log_messages.append(
                            f"Reconcile skipped ref={ref}: amount mismatch "
                            f"invoice={inv_bal} misc={misc_bal}"
                        )
                        continue
                    try:
                        odoo_conn.call(
                            odoo_client,
                            "account.move.line",
                            "reconcile",
                            [[inv_line["id"], misc_line["id"]]],
                            {"context": ctx},
                        )
                        reconciled += 1
                    except Exception as exc:
                        if "cannot marshal None" in str(exc):
                            reconciled += (
                                1  # Odoo retourneert None bij succes in sommige versies
                            )
                        else:
                            result.log_messages.append(
                                f"Reconcile error ref={ref}: {exc}"
                            )
                except Exception as exc:
                    result.log_messages.append(f"Reconcile warning ref={ref}: {exc}")

            result.log_messages.append(
                f"Reconciled {reconciled}/{len(ref_to_invoice)} invoice(s) against payout entry"
            )

        return result
