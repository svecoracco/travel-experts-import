"""Rail-ticket import plugin — poort van
`travel-experts-backend/apps/main/app/plugins/rail/plugin.py`.

Pipeline
--------
1. Parse Excel-bestand (één rij = één rail-ticket)
2. build_moves():
   a. Resolve partner, GL-account uit config
   b. Optioneel: zoek de originele Odoo-entry op via referentie → override datums/ref, bouw narration
   c. Bulk SQL Server-lookup: ISSUE_ID → file number → analytic account
   d. Bouw ÉÉN vendor-bill-payload met één invoice_line per ticket-rij
3. execute():
   a. Idempotentie-check via payment_reference (= OFFICIAL_DOC_NUMBER)
   b. Maak vendor bill aan
   c. Post invoice

Odoo-toegang herschreven naar `odoo_conn`.
"""

from __future__ import annotations

import base64
import logging
from collections import Counter
from datetime import date, datetime
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
from plugins.rail.excel_reader import (
    count_data_rows,
    read_rail_excel,
    validate_rail_columns,
)
from plugins.rail.transform import (
    RailConfig,
    build_invoice_payload,
    build_rail_config,
)
from shared.account_utils import (
    build_analytic_account_map,
    create_analytic_accounts,
    resolve_account_id,
)
from shared.move_utils import post_moves
from shared.sql_server import fetch_ticket_filenumbers_by_dnr

logger = logging.getLogger(__name__)

IDEMPOTENCY_CHUNK = 200
POST_CHUNK = 50


def _parse_date(val: Any) -> str | None:
    """Parse een datumcel naar ISO YYYY-MM-DD.

    Ondersteunt:
    - DD.MM.YYYY  (Belgisch/Europees formaat uit de rail-export)
    - YYYY-MM-DD  (ISO, fallback)
    """
    if val is None:
        return None
    s = str(val).strip()
    if not s or s.lower() in ("nan", "none", ""):
        return None
    for fmt in ("%d.%m.%Y", "%Y-%m-%d", "%d/%m/%Y"):
        try:
            return datetime.strptime(s, fmt).date().isoformat()
        except ValueError:
            continue
    return None


class RailPlugin(ImportPlugin):
    # ------------------------------------------------------------------
    # Meta
    # ------------------------------------------------------------------

    def get_meta(self) -> PluginMeta:
        return PluginMeta(
            name="rail",
            display_name="Rail Tickets",
            accepted_extensions=[".xlsx", ".xls"],
            description=(
                "Reads rail ticket Excel exports and creates one purchase invoice in Odoo "
                "with one cost line per ticket and analytic distribution via SQL Server lookup."
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

        ok, missing = validate_rail_columns(str(file_path))
        if not ok:
            return ValidationResult(
                valid=False,
                errors=[f"Missing required column(s): {', '.join(missing)}"],
            )

        row_count = count_data_rows(str(file_path))
        if row_count == 0:
            return ValidationResult(
                valid=False,
                errors=[
                    "No data rows found (OFFICIAL_DOC_NUMBER is empty for all rows)"
                ],
            )

        return ValidationResult(valid=True, row_count=row_count)

    # ------------------------------------------------------------------
    # Parse
    # ------------------------------------------------------------------

    def parse(self, file_path: Path, config: dict[str, Any]) -> ParsedData:

        df = read_rail_excel(str(file_path))

        # OFFICIAL_DOC_NUMBER: zelfde waarde voor alle datarijen in het bestand
        doc_number = ""
        if not df.empty:
            doc_number = str(df["OFFICIAL_DOC_NUMBER"].iloc[0]).strip()

        # Invoice-datum: DEPARTURE_DATE van eerste rij, fallback BOOKING_TIME, fallback vandaag
        invoice_date: str = date.today().isoformat()
        if not df.empty:
            for col in ("DEPARTURE_DATE", "BOOKING_TIME"):
                if col in df.columns:
                    parsed = _parse_date(df[col].iloc[0])
                    if parsed:
                        invoice_date = parsed
                        break

        return ParsedData(
            items=[df],
            metadata={
                "doc_number": doc_number,
                "invoice_date": invoice_date,
                "row_count": len(df),
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
        self._rail_cfg: RailConfig | None = None
        self._created_analytic_accounts: dict = {}
        self._analytic_create_error: str | None = None
        self._sql_error: str | None = None
        self._sql_skip_warning: str | None = None
        self._original_entry_warning: str | None = None
        self._file_path: str | None = parsed.metadata.get("file_path")
        self._file_name: str = parsed.metadata.get("file_name", "")

        df = parsed.items[0]
        doc_number: str = parsed.metadata["doc_number"]
        invoice_date: str = parsed.metadata["invoice_date"]

        if df.empty:
            return []

        if on_progress:
            on_progress("building", 0, 0, "Resolving Rail config and partner...")

        # --- Bouw config (resolveert ook partner_id via supplier_ref) ---
        cfg = build_rail_config(config, company_id, odoo_client)
        self._rail_cfg = cfg

        # --- Resolve GL-account-ID uit code ---
        account_cache: dict[int, int] = {}
        gl_account_id = resolve_account_id(
            odoo_client, cfg.line_account_id, company_id, account_cache
        )

        # --- Optioneel: zoek originele entry in Odoo ---
        original_ref = str(config.get("original_entry_ref") or "").strip()
        ref = doc_number
        accounting_date: str | None = None
        narration: str | None = None

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
                    ["id", "ref", "invoice_date", "date"],
                    limit=1,
                )
                if found:
                    entry = found[0]
                    if entry.get("ref"):
                        ref = str(entry["ref"])
                    if entry.get("invoice_date"):
                        invoice_date = str(entry["invoice_date"])
                    if entry.get("date"):
                        accounting_date = str(entry["date"])
                    entry_id = entry["id"]
                    narration = (
                        f"Analytical Distribution of entry "
                        f'<a href="/web#id={entry_id}&model=account.move&view_type=form">'
                        f"{original_ref}</a>"
                    )
                    logger.info(
                        "[rail] Original entry '%s' found (id=%d): ref=%s invoice_date=%s date=%s",
                        original_ref,
                        entry_id,
                        ref,
                        invoice_date,
                        accounting_date,
                    )
                else:
                    logger.warning(
                        "[rail] Original entry '%s' not found in Odoo (company_id=%d)",
                        original_ref,
                        company_id,
                    )
                    self._original_entry_warning = (
                        f"original_entry_ref '{original_ref}' not found in Odoo — "
                        "using dates from Excel file"
                    )
            except Exception as exc:
                logger.warning("[rail] Original entry lookup failed: %s", exc)
                self._original_entry_warning = (
                    f"original_entry_ref lookup failed: {exc}"
                )

        # --- Filter rijen: sla nul NET_AMOUNT over ---
        rows_list: list[dict] = []
        for _, row in df.iterrows():
            ticket_nbr = str(row.get("ISSUE_ID", "") or "").strip()
            net_amount = float(row.get("_net_amount", 0.0))

            if net_amount == 0.0:
                self._skip_rows.append(
                    {
                        "reason": "Zero NET_AMOUNT",
                        "ISSUE_ID": ticket_nbr,
                        "DNR": str(row.get("DNR_ID", "") or "").strip(),
                        "NET_AMOUNT": str(row.get("NET_AMOUNT", "") or "").strip(),
                        "OFFICIAL_DOC_NUMBER": doc_number,
                    }
                )
                continue

            rows_list.append(dict(row))

        if not rows_list:
            logger.warning("[rail] All rows were skipped (zero NET_AMOUNT)")
            return []

        # --- SQL Server: DNR → file number (single pass, ticket-tiebreaker) ---
        dnr_rows: list[dict] = []
        for row in rows_list:
            dnr = str(row.get("DNR_ID", "") or "").strip()
            issue_id = str(row.get("ISSUE_ID", "") or "").strip()
            if dnr:
                dnr_rows.append({"dnr": dnr, "issue_id": issue_id})

        ticket_to_file: dict[str, str] = {}
        ticket_to_analytic_id: dict[str, int] = {}
        sql_diagnostics: dict[str, str] = {}
        error_filenumbers: set[str] = set(config.get("rail_error_filenumbers") or [])

        table_name = str(cfg.bts_table or "").strip()
        ticket_col = str(cfg.bts_ticket_col or "").strip()
        dnr_col = str(cfg.bts_dnr_col or "").strip()

        logger.info(
            "[rail] SQL config: table=%r dnr_col=%r ticket_col=%r | "
            "dnr_rows=%d | sample DNR_IDs=%s",
            table_name,
            dnr_col,
            ticket_col,
            len(dnr_rows),
            [r["dnr"] for r in dnr_rows[:5]],
        )

        if dnr_rows and table_name and ticket_col and dnr_col:
            db_cfg = {
                "sql_connection_string": config.get("sql_connection_string", ""),
                "server": config.get("bts_db_server", ""),
                "database": config.get("bts_db_database", ""),
                "username": config.get("bts_db_username", ""),
                "password": config.get("bts_db_password", ""),
                "driver": config.get("bts_db_driver", "ODBC Driver 17 for SQL Server"),
                "timeout": str(config.get("bts_db_timeout", 10)),
                "query_timeout": str(config.get("bts_db_query_timeout", 120)),
                "chunk_size": str(config.get("bts_db_chunk_size", 500)),
            }
            if not db_cfg["sql_connection_string"]:
                self._sql_error = (
                    "DB lookup skipped: sql_connection_string is not configured — "
                    "check SQL_CONNECTION_STRING in local.settings.json."
                )
            else:
                if on_progress:
                    on_progress(
                        "building",
                        0,
                        0,
                        f"Looking up {len(dnr_rows)} ticket(s) in SQL Server...",
                    )
                try:
                    ticket_to_file = fetch_ticket_filenumbers_by_dnr(
                        db_cfg,
                        dnr_rows,
                        table_name,
                        dnr_col,
                        ticket_col,
                        excluded_filenumber=str(
                            config.get("bts_excluded_filenumber", "99999999")
                        ),
                        diagnostics=sql_diagnostics,
                    )
                    logger.info(
                        "[rail] SQL Server lookup: %d/%d DNR keys found",
                        len(ticket_to_file),
                        len(dnr_rows),
                    )
                except Exception as sql_exc:
                    logger.exception("[rail] SQL Server lookup failed")
                    self._sql_error = str(sql_exc)
                    ticket_to_file = {}

            # Odoo analytic-account-lookup + auto-create
            if ticket_to_file:
                file_numbers = sorted(
                    {
                        fn
                        for fn in ticket_to_file.values()
                        if fn and fn not in error_filenumbers
                    }
                )
                file_to_analytic = build_analytic_account_map(
                    odoo_client, file_numbers, company_id
                )

                missing_fns = [fn for fn in file_numbers if fn not in file_to_analytic]
                if missing_fns:
                    analytic_plan = str(
                        config.get("rail_analytic_plan") or "File number"
                    ).strip()
                    try:
                        newly_created = create_analytic_accounts(
                            odoo_client,
                            missing_fns,
                            company_id,
                            analytic_plan,
                        )
                        file_to_analytic.update(newly_created)
                        self._created_analytic_accounts = newly_created
                        logger.info(
                            "[rail] Auto-created %d analytic account(s): %s",
                            len(newly_created),
                            list(newly_created.keys()),
                        )
                    except Exception as exc_create:
                        logger.error(
                            "[rail] Failed to auto-create analytic accounts: %s",
                            exc_create,
                        )
                        self._analytic_create_error = str(exc_create)

                ticket_to_analytic_id = {
                    t: file_to_analytic[fn]
                    for t, fn in ticket_to_file.items()
                    if fn in file_to_analytic
                }

        else:
            if dnr_rows:
                logger.warning(
                    "[rail] SQL Server lookup skipped: missing bts_table, bts_ticket_col, or bts_dnr_col config"
                )
                self._sql_skip_warning = (
                    "SQL Server lookup skipped (bts_table, bts_ticket_col, or bts_dnr_col not configured) "
                    "— all tickets will be imported without analytic distribution"
                )

        # Markeer tickets voor Needs Review:
        #   - DNR niet gevonden in DB
        #   - DNR gevonden maar tiebreaker mislukt (meerdere FileNumbers, geen ticketmatch)
        #   - Opgelost naar een geconfigureerde error-FileNumber (rail_error_filenumbers)
        for row in rows_list:
            dnr_key = str(row.get("DNR_ID", "") or "").strip()
            if not dnr_key:
                continue

            review_entry = {
                "ISSUE_ID": str(row.get("ISSUE_ID", "") or "").strip(),
                "DNR": dnr_key,
                "NET_AMOUNT": row.get("_net_amount", 0.0),
                "OFFICIAL_DOC_NUMBER": doc_number,
            }

            fn = ticket_to_file.get(dnr_key)
            if fn is None:
                # Niet opgelost — check diagnostics voor specifieke reden
                diag_reason = sql_diagnostics.get(dnr_key) if sql_diagnostics else None
                review_entry["reason"] = diag_reason or "DNR not found in SQL Server DB"
                review_entry["FileNumber"] = ""
                self._needs_review_rows.append(review_entry)
            elif error_filenumbers and fn in error_filenumbers:
                review_entry["reason"] = f"FileNumber is an error filenumber ({fn})"
                review_entry["FileNumber"] = fn
                self._needs_review_rows.append(review_entry)

        if on_progress:
            on_progress("building", 0, 0, "Building invoice payload...")

        # --- Bouw de invoice-payload ---
        payload = build_invoice_payload(
            cfg=cfg,
            gl_account_id=gl_account_id,
            invoice_date=invoice_date,
            accounting_date=accounting_date,
            ref=ref,
            doc_number=doc_number,
            rows=rows_list,
            ticket_to_analytic=ticket_to_analytic_id,
            company_id=company_id,
            narration=narration,
        )

        move_type = payload["move_type"]
        logger.info(
            "[rail] build_moves done: move_type=%s doc=%s tickets=%d skipped=%d needs_review=%d",
            move_type,
            doc_number,
            len(rows_list),
            len(self._skip_rows),
            len(self._needs_review_rows),
        )
        return [MovePayload(payload=payload, move_type=move_type, ref=doc_number)]

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

        sql_error = getattr(self, "_sql_error", None)
        if sql_error:
            result.log_messages.append(
                f"Warning: SQL Server lookup failed — {sql_error}"
            )

        sql_skip_warning = getattr(self, "_sql_skip_warning", None)
        if sql_skip_warning:
            result.log_messages.append(f"Warning: {sql_skip_warning}")

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
                f"{prefix}{len(needs_review_rows)} ticket(s) will be imported without "
                "analytic distribution (Needs Review)"
            )

        if not moves:
            result.log_messages.append(
                "No invoice to create — all ticket rows were skipped."
            )
            return result

        doc_number = moves[0].ref

        # ------------------------------------------------------------------
        # Idempotentie-check
        # ------------------------------------------------------------------
        try:
            existing = odoo_conn.search_read(
                odoo_client,
                "account.move",
                [
                    ("payment_reference", "=", doc_number),
                    ("company_id", "=", company_id),
                ],
                ["id", "payment_reference", "move_type"],
                limit=1,
            )
        except Exception as exc:
            logger.warning("[rail] Idempotency check failed: %s", exc)
            existing = []

        if existing:
            result.skipped += 1
            result.log_messages.append(
                f"Invoice already exists for OFFICIAL_DOC_NUMBER='{doc_number}' "
                f"(id={existing[0]['id']}) — skipped (idempotent)"
            )
            return result

        result.items_processed = 1

        # ------------------------------------------------------------------
        # Dry run
        # ------------------------------------------------------------------
        if dry_run:
            result.created = 1
            ticket_count = len(moves[0].payload.get("invoice_line_ids", []))
            result.log_messages.append(
                f"[dry-run] Would create 1 vendor bill ({moves[0].move_type}) "
                f"with {ticket_count} ticket line(s) — doc={doc_number}"
            )
            return result

        # ------------------------------------------------------------------
        # Maak invoice aan
        # ------------------------------------------------------------------
        if on_progress:
            on_progress("executing", 0, 1, "Creating Rail vendor bill...")
        try:
            move_id = odoo_conn.create(odoo_client, "account.move", moves[0].payload)
            result.created = 1
            logger.info(
                "[rail] Created invoice id=%d payment_reference=%s", move_id, doc_number
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
                on_progress("posting", 0, 1, "Posting Rail invoice...")
            try:
                post_moves(odoo_client, [move_id], company_id)
                logger.info("[rail] Posted invoice id=%d", move_id)
            except Exception as exc:
                result.log_messages.append(f"Post error move_id={move_id}: {exc}")

        # ------------------------------------------------------------------
        # Lees geposte move-naam (volgnummer) en log het
        # ------------------------------------------------------------------
        move_name = doc_number
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
                "[rail] Could not read move name for id=%d: %s", move_id, exc
            )
        result.log_messages.append(f"Created vendor bill: {move_name} (id={move_id})")

        # ------------------------------------------------------------------
        # Voeg bronbestand toe aan de vendor bill
        # ------------------------------------------------------------------
        file_path_str = getattr(self, "_file_path", None)
        file_name_str = getattr(self, "_file_name", None) or "rail_import.xlsx"
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
