"""Commission billing import plugin — poort van
`travel-experts-backend/apps/main/app/plugins/commission/plugin.py`.

Pipeline
--------
1. Parse Excel-bestand (headerzone + datarijen met kleur-gebaseerde stop)
2. build_moves():
   a. Resolve partner, GL-account uit config
   b. Optioneel: zoek de originele Odoo-entry op via referentie → override
      datums/ref, bouw narration
   c. Bulk analytic-account-lookup via dossiernummers (direct uit Excel,
      geen SQL Server)
   d. Bouw ÉÉN vendor-bill-payload met één invoice_line per dossier-rij
3. execute():
   a. Idempotentie-check via payment_reference (= invoice_ref uit header)
   b. Maak vendor bill aan
   c. Post invoice

Odoo-toegang herschreven naar `odoo_conn`.
"""

from __future__ import annotations

import base64
import logging
from collections import Counter
from pathlib import Path
from typing import Any

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
from plugins.commission.excel_reader import (
    CommissionFileData,
    count_commission_data_rows,
    read_commission_excel,
    validate_commission_columns,
)
from plugins.commission.transform import (
    CommissionConfig,
    build_commission_config,
    build_invoice_payload,
)
from shared.account_utils import (
    build_analytic_account_map,
    create_analytic_accounts,
    resolve_account_id,
)
from shared.move_utils import post_moves

logger = logging.getLogger(__name__)


class CommissionPlugin(ImportPlugin):
    # ------------------------------------------------------------------
    # Meta
    # ------------------------------------------------------------------

    def get_meta(self) -> PluginMeta:
        return PluginMeta(
            name="commission",
            display_name="Commission",
            accepted_extensions=[".xlsx", ".xls"],
            description=(
                "Reads commission billing Excel files and creates one purchase "
                "invoice in Odoo with one cost line per dossier and analytic "
                "distribution by file number."
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

        errors = validate_commission_columns(str(file_path))
        if errors:
            return ValidationResult(valid=False, errors=errors)

        row_count = count_commission_data_rows(str(file_path))
        if row_count == 0:
            return ValidationResult(
                valid=False,
                errors=["No data rows found between headers and total row"],
            )

        return ValidationResult(valid=True, row_count=row_count)

    # ------------------------------------------------------------------
    # Parse
    # ------------------------------------------------------------------

    def parse(self, file_path: Path, config: dict[str, Any]) -> ParsedData:
        file_data = read_commission_excel(str(file_path))

        return ParsedData(
            items=[file_data],
            metadata={
                "invoice_ref": file_data.header.invoice_ref or "",
                "row_count": len(file_data.rows),
                "file_path": str(file_path),
                "file_name": file_path.name,
            },
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
        self._commission_cfg: CommissionConfig | None = None
        self._created_analytic_accounts: dict = {}
        self._analytic_create_error: str | None = None
        self._original_entry_warning: str | None = None
        self._file_path: str | None = parsed.metadata.get("file_path")
        self._file_name: str = parsed.metadata.get("file_name", "")

        file_data: CommissionFileData = parsed.items[0]
        header = file_data.header
        rows = file_data.rows

        if not rows:
            return []

        if on_progress:
            on_progress("building", 0, 0, "Resolving Commission config and partner...")

        # --- Bouw config (resolveert ook partner_id via supplier_ref) ---
        cfg = build_commission_config(config, company_id, odoo_client)
        self._commission_cfg = cfg

        # --- Resolve GL-account-ID uit code ---
        account_cache: dict[int, int] = {}
        gl_account_id = resolve_account_id(
            odoo_client, cfg.line_account_id, company_id, account_cache
        )

        # --- Optioneel: zoek originele entry in Odoo ---
        original_ref = str(config.get("original_entry_ref") or "").strip()
        invoice_date_override: str | None = None
        accounting_date_override: str | None = None
        ref_override: str | None = None
        narration_override: str | None = None
        partner_id_override: int | None = None

        if original_ref:
            if on_progress:
                on_progress(
                    "building", 0, 0, f"Looking up original entry '{original_ref}'..."
                )
            try:
                found = odoo_conn.search_read(
                    odoo_client,
                    "account.move",
                    [("name", "=", original_ref), ("company_id", "=", company_id)],
                    ["id", "ref", "invoice_date", "date", "partner_id"],
                    limit=1,
                )
                if found:
                    entry = found[0]
                    if entry.get("ref"):
                        ref_override = str(entry["ref"])
                    if entry.get("invoice_date"):
                        invoice_date_override = str(entry["invoice_date"])
                    if entry.get("date"):
                        accounting_date_override = str(entry["date"])
                    # partner_id komt als [id, name] uit Odoo
                    if entry.get("partner_id"):
                        pid = entry["partner_id"]
                        partner_id_override = (
                            pid[0] if isinstance(pid, (list, tuple)) else int(pid)
                        )
                    entry_id = entry["id"]
                    narration_override = (
                        f"Analytical Distribution of entry "
                        f'<a href="/web#id={entry_id}&model=account.move&view_type=form">'
                        f"{original_ref}</a>"
                    )
                    logger.info(
                        "[commission] Original entry '%s' found (id=%d): ref=%s invoice_date=%s date=%s partner_id=%s",
                        original_ref,
                        entry_id,
                        ref_override,
                        invoice_date_override,
                        accounting_date_override,
                        partner_id_override,
                    )
                else:
                    logger.warning(
                        "[commission] Original entry '%s' not found in Odoo (company_id=%d)",
                        original_ref,
                        company_id,
                    )
                    self._original_entry_warning = (
                        f"original_entry_ref '{original_ref}' not found in Odoo — "
                        "using dates from Excel file"
                    )
            except Exception as exc:
                logger.warning("[commission] Original entry lookup failed: %s", exc)
                self._original_entry_warning = (
                    f"original_entry_ref lookup failed: {exc}"
                )

        # --- Analytic accounts: bulk-lookup via dossiernummers ---
        dossier_numbers = sorted({r.dossier for r in rows if r.dossier})
        analytic_map: dict[str, int] = {}

        if dossier_numbers:
            if on_progress:
                on_progress(
                    "building",
                    0,
                    0,
                    f"Looking up {len(dossier_numbers)} analytic account(s)...",
                )

            analytic_map = build_analytic_account_map(
                odoo_client, dossier_numbers, company_id
            )

            missing_fns = [fn for fn in dossier_numbers if fn not in analytic_map]
            if missing_fns:
                try:
                    newly_created = create_analytic_accounts(
                        odoo_client,
                        missing_fns,
                        company_id,
                        cfg.analytic_plan,
                    )
                    analytic_map.update(newly_created)
                    self._created_analytic_accounts = newly_created
                    logger.info(
                        "[commission] Auto-created %d analytic account(s): %s",
                        len(newly_created),
                        list(newly_created.keys()),
                    )
                except Exception as exc_create:
                    logger.error(
                        "[commission] Failed to auto-create analytic accounts: %s",
                        exc_create,
                    )
                    self._analytic_create_error = str(exc_create)

        if on_progress:
            on_progress("building", 0, 0, "Building invoice payload...")

        # --- File ref: bestandsnaam zonder extensie (idempotentiesleutel) ---
        file_ref = Path(self._file_name).stem if self._file_name else ""

        # --- Bouw de invoice-payload ---
        payload, skip_report, needs_review = build_invoice_payload(
            config=cfg,
            gl_account_id=gl_account_id,
            header=header,
            rows=rows,
            analytic_map=analytic_map,
            company_id=company_id,
            file_ref=file_ref,
            invoice_date_override=invoice_date_override,
            accounting_date_override=accounting_date_override,
            ref_override=ref_override,
            narration_override=narration_override,
            partner_id_override=partner_id_override,
        )

        self._skip_rows = skip_report
        self._needs_review_rows = needs_review

        move_type = payload["move_type"]
        line_count = len(payload.get("invoice_line_ids", []))
        logger.info(
            "[commission] build_moves done: move_type=%s file_ref=%s lines=%d "
            "skipped=%d needs_review=%d",
            move_type,
            file_ref,
            line_count,
            len(self._skip_rows),
            len(self._needs_review_rows),
        )

        if not payload.get("invoice_line_ids"):
            logger.warning("[commission] All rows were skipped — no invoice to create")
            return []

        return [MovePayload(payload=payload, move_type=move_type, ref=file_ref)]

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

        # Toon build-time-waarschuwingen
        original_entry_warning = getattr(self, "_original_entry_warning", None)
        if original_entry_warning:
            result.log_messages.append(f"Warning: {original_entry_warning}")

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

        # Vul Needs Review-tab
        if needs_review_rows:
            result.extra_report_data["Needs Review"] = needs_review_rows
            prefix = "[dry-run] " if dry_run else ""
            result.log_messages.append(
                f"{prefix}{len(needs_review_rows)} dossier(s) imported without "
                "analytic distribution (Needs Review)"
            )

        if not moves:
            result.log_messages.append(
                "No invoice to create — all commission rows were skipped."
            )
            return result

        invoice_ref = moves[0].ref
        move_type = moves[0].move_type

        # ------------------------------------------------------------------
        # Idempotentie-check
        # ------------------------------------------------------------------
        try:
            existing = odoo_conn.search_read(
                odoo_client,
                "account.move",
                [
                    ("payment_reference", "=", invoice_ref),
                    ("move_type", "=", move_type),
                    ("company_id", "=", company_id),
                ],
                ["id", "payment_reference", "move_type"],
                limit=1,
            )
        except Exception as exc:
            logger.warning("[commission] Idempotency check failed: %s", exc)
            existing = []

        if existing:
            result.skipped += 1
            result.log_messages.append(
                f"Invoice already exists for ref='{invoice_ref}' "
                f"(id={existing[0]['id']}) — skipped (idempotent)"
            )
            return result

        result.items_processed = 1

        # ------------------------------------------------------------------
        # Dry run
        # ------------------------------------------------------------------
        if dry_run:
            result.created = 1
            line_count = len(moves[0].payload.get("invoice_line_ids", []))
            result.log_messages.append(
                f"[dry-run] Would create 1 vendor bill ({move_type}) "
                f"with {line_count} commission line(s) — ref={invoice_ref}"
            )
            return result

        # ------------------------------------------------------------------
        # Maak invoice aan
        # ------------------------------------------------------------------
        if on_progress:
            on_progress("executing", 0, 1, "Creating Commission vendor bill...")
        try:
            move_id = odoo_conn.create(odoo_client, "account.move", moves[0].payload)
            result.created = 1
            logger.info(
                "[commission] Created invoice id=%d payment_reference=%s",
                move_id,
                invoice_ref,
            )
        except Exception as exc:
            result.errors += 1
            result.log_messages.append(f"Create error: {exc}")
            return result

        # ------------------------------------------------------------------
        # Post
        # ------------------------------------------------------------------
        if auto_post:
            if on_progress:
                on_progress("posting", 0, 1, "Posting Commission invoice...")
            try:
                post_moves(odoo_client, [move_id], company_id)
                logger.info("[commission] Posted invoice id=%d", move_id)
            except Exception as exc:
                result.log_messages.append(f"Post error move_id={move_id}: {exc}")

        # ------------------------------------------------------------------
        # Lees geposte move-naam (volgnummer)
        # ------------------------------------------------------------------
        move_name = invoice_ref
        try:
            name_rows = odoo_conn.search_read(
                odoo_client,
                "account.move",
                [("id", "=", move_id)],
                ["name"],
                limit=1,
            )
            if name_rows and name_rows[0].get("name") and name_rows[0]["name"] != "/":
                move_name = name_rows[0]["name"]
        except Exception as exc:
            logger.warning(
                "[commission] Could not read move name for id=%d: %s", move_id, exc
            )
        result.log_messages.append(f"Created vendor bill: {move_name} (id={move_id})")

        # ------------------------------------------------------------------
        # Voeg bronbestand toe aan de vendor bill
        # ------------------------------------------------------------------
        file_path_str = getattr(self, "_file_path", None)
        file_name_str = getattr(self, "_file_name", None) or "commission_import.xlsx"
        if file_path_str:
            try:
                with open(file_path_str, "rb") as f:
                    datas = base64.b64encode(f.read()).decode()
                odoo_conn.create(
                    odoo_client,
                    "ir.attachment",
                    {
                        "name": file_name_str,
                        "res_model": "account.move",
                        "res_id": move_id,
                        "datas": datas,
                        "type": "binary",
                    },
                )
                result.log_messages.append(
                    f"Source file '{file_name_str}' attached to vendor bill."
                )
            except Exception as exc:
                result.log_messages.append(
                    f"Warning: Could not attach source file: {exc}"
                )

        return result
