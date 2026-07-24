"""TUI travel import plugin — poort van
`travel-experts-backend/apps/main/app/plugins/tui/plugin.py`.

Pipeline
--------
1. Parse '#'-gescheiden CSV-bestand (geen header)
2. build_moves():
   a. Groepeer rijen op col_group
   b. SQL Server-lookup: ticketrefs + commissienummers → file number → analytic
   c. Bouw één purchase-invoice-payload per groep
3. execute():
   a. Idempotentie-check via payment_reference
   b. Maak purchase invoices aan (één per groep)
   c. Post invoices (batch)

Odoo-toegang herschreven naar `odoo_conn`.
"""

from __future__ import annotations

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
from plugins.tui.csv_reader import read_tui_csv, validate_tui_csv
from plugins.tui.transform import (
    TuiConfig,
    build_invoice_payload,
    build_tui_config,
    extract_commission_number,
    fetch_tui_filenumbers,
    is_commission_row,
    lookup_currencies,
    parse_tui_filename,
    strip_leading_zeros,
)
from shared.account_utils import (
    build_analytic_account_map,
    create_analytic_accounts,
    resolve_account_id,
    resolve_tax_id,
)
from shared.move_utils import post_moves

logger = logging.getLogger(__name__)

IDEMPOTENCY_CHUNK = 200
POST_CHUNK = 50


class TuiPlugin(ImportPlugin):
    # ------------------------------------------------------------------
    # Meta
    # ------------------------------------------------------------------

    def get_meta(self) -> PluginMeta:
        return PluginMeta(
            name="tui",
            display_name="TUI",
            accepted_extensions=[".csv", ".txt"],
            description=(
                "Reads TUI travel CSV exports and creates purchase invoices "
                "in Odoo with ticket and commission lines."
            ),
        )

    # ------------------------------------------------------------------
    # Validate
    # ------------------------------------------------------------------

    def validate_file(self, file_path: Path) -> ValidationResult:
        if not file_path.exists():
            return ValidationResult(valid=False, errors=["File not found"])
        if file_path.suffix.lower() not in (".csv", ".txt"):
            return ValidationResult(valid=False, errors=["Expected .csv or .txt file"])

        ok, errors = validate_tui_csv(str(file_path))
        if not ok:
            return ValidationResult(valid=False, errors=errors)

        try:
            df = read_tui_csv(str(file_path))
            return ValidationResult(valid=True, row_count=len(df))
        except Exception as exc:
            return ValidationResult(valid=False, errors=[str(exc)])

    # ------------------------------------------------------------------
    # Parse
    # ------------------------------------------------------------------

    def parse(self, file_path: Path, config: dict[str, Any]) -> ParsedData:
        df = read_tui_csv(str(file_path))

        # Parse bestandsnaam voor invoice_date en ref
        try:
            invoice_date, tui_ref = parse_tui_filename(file_path.name)
        except ValueError as exc:
            logger.warning("[tui] Filename parse failed: %s", exc)
            invoice_date = ""
            tui_ref = f"TUI-{file_path.stem}"

        return ParsedData(
            items=[df],
            metadata={
                "row_count": len(df),
                "invoice_date": invoice_date,
                "tui_ref": tui_ref,
                "file_name": file_path.name,
                "file_path": str(file_path),
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
        import pandas as pd

        # Reset per-run state
        self._skip_rows: list[dict] = []
        self._needs_review_rows: list[dict] = []
        self._tui_cfg: TuiConfig | None = None
        self._created_analytic_accounts: dict = {}
        self._analytic_create_error: str | None = None
        self._sql_error: str | None = None
        self._summary_data: list[dict] = []

        df: pd.DataFrame = parsed.items[0]
        invoice_date: str = parsed.metadata["invoice_date"]
        tui_ref: str = parsed.metadata["tui_ref"]

        if df.empty:
            return []

        if on_progress:
            on_progress("building", 0, 0, "Resolving TUI config...")

        # --- Bouw config ---
        cfg = build_tui_config(config, company_id)
        self._tui_cfg = cfg

        # --- Beschrijving-gebaseerde skip-lijst (case-insensitief) ---
        skip_descriptions: set[str] = {
            str(d).strip().upper()
            for d in (config.get("tui_skip_descriptions") or [])
            if str(d).strip()
        }

        # --- Resolve GL-account-ID's ---
        account_cache: dict[int, int] = {}
        ticket_gl_account_id = resolve_account_id(
            odoo_client,
            cfg.glaccount_ticket,
            company_id,
            account_cache,
        )
        comm_gl_account_id = resolve_account_id(
            odoo_client,
            cfg.glaccount_comm,
            company_id,
            account_cache,
        )

        # --- Resolve BTW ---
        tax_cache: dict[str, int] = {}
        tax_id: int | None = None
        if cfg.vatcode:
            try:
                tax_id = resolve_tax_id(odoo_client, cfg.vatcode, company_id, tax_cache)
            except Exception as exc:
                logger.warning("[tui] VAT code '%s' not found: %s", cfg.vatcode, exc)

        # --- Valuta-lookup ---
        unique_currencies = sorted(
            {str(v).strip() for v in df["col_currency"].unique() if str(v).strip()}
        )
        currency_map: dict[str, int] = {}
        if unique_currencies:
            currency_map = lookup_currencies(odoo_client, unique_currencies)

        # --- Bereid rij-data voor en verzamel search-keys voor SQL Server ---
        all_rows: list[dict] = []
        search_keys: list[str] = []

        for _, row in df.iterrows():
            col_group = str(row["col_group"]).strip()
            col_amount = float(row["col_amount"])
            col_ticket_ref = str(row["col_ticket_ref"]).strip()
            col_description = str(row["col_description"]).strip()
            col_comm_ref = str(row["col_comm_ref"]).strip()
            col_departure = str(row["col_departure"]).strip()
            col_currency = str(row["col_currency"]).strip()

            # Sla rijen zonder groep over
            if not col_group:
                self._skip_rows.append(
                    {
                        "reason": "Missing col_group",
                        "col_ticket_ref": col_ticket_ref,
                        "col_description": col_description,
                        "col_amount": col_amount,
                    }
                )
                continue

            # Sla rijen met nulbedrag over
            if col_amount == 0.0:
                self._skip_rows.append(
                    {
                        "reason": "Zero amount",
                        "col_group": col_group,
                        "col_ticket_ref": col_ticket_ref,
                        "col_description": col_description,
                        "col_amount": col_amount,
                    }
                )
                continue

            # Sla rijen over waarvan de beschrijving matcht met tui_skip_descriptions-config
            if skip_descriptions and col_description.upper() in skip_descriptions:
                self._skip_rows.append(
                    {
                        "reason": f"Description excluded by config: {col_description}",
                        "col_group": col_group,
                        "col_ticket_ref": col_ticket_ref,
                        "col_description": col_description,
                        "col_amount": col_amount,
                    }
                )
                continue

            is_comm = is_commission_row(col_description)

            # Bepaal SQL Server-search-key
            search_key = ""
            if is_comm:
                comm_num = extract_commission_number(col_description)
                if comm_num:
                    search_key = comm_num
            else:
                if col_ticket_ref:
                    search_key = strip_leading_zeros(col_ticket_ref)

            if search_key:
                search_keys.append(search_key)

            all_rows.append(
                {
                    "col_group": col_group,
                    "col_amount": col_amount,
                    "col_ticket_ref": col_ticket_ref,
                    "col_departure": col_departure,
                    "col_description": col_description,
                    "col_comm_ref": col_comm_ref,
                    "col_currency": col_currency,
                    "_is_commission": is_comm,
                    "_search_key": search_key,
                }
            )

        if not all_rows:
            return []

        # --- SQL Server-lookup ---
        unique_search_keys = sorted({k for k in search_keys if k})
        ticket_to_file: dict[str, str] = {}
        error_filenumbers: set[str] = set(config.get("tui_error_filenumbers") or [])

        if unique_search_keys and cfg.tui_table and cfg.tui_ticket_col:
            db_cfg = {
                "connection_string": config.get("sql_connection_string", ""),
                "server": config.get("bts_db_server", ""),
                "database": config.get("bts_db_database", ""),
                "username": config.get("bts_db_username", ""),
                "password": config.get("bts_db_password", ""),
                "driver": config.get("bts_db_driver", "ODBC Driver 17 for SQL Server"),
                "timeout": str(config.get("bts_db_timeout", 10)),
                "query_timeout": str(config.get("bts_db_query_timeout", 120)),
                "chunk_size": str(config.get("bts_db_chunk_size", 500)),
            }
            _has_connection = db_cfg["connection_string"] or (
                db_cfg["server"] and db_cfg["database"]
            )
            if not _has_connection:
                self._sql_error = (
                    "DB lookup skipped: no SQL connection configured — "
                    "set sql_connection_string (or bts_db_server + bts_db_database) in Settings → Config."
                )
            else:
                if on_progress:
                    on_progress(
                        "building",
                        0,
                        0,
                        f"Looking up {len(unique_search_keys)} ticket(s) in SQL Server...",
                    )
                try:
                    ticket_to_file = fetch_tui_filenumbers(
                        db_cfg,
                        unique_search_keys,
                        cfg.tui_table,
                        cfg.tui_ticket_col,
                    )
                    if not ticket_to_file and unique_search_keys:
                        self._sql_error = (
                            f"SQL lookup returned 0 results for {len(unique_search_keys)} "
                            f"key(s) {unique_search_keys} in "
                            f"{cfg.tui_table}.{cfg.tui_ticket_col}. "
                            "Check tui_table and tui_ticket_col in Settings → Config."
                        )
                except Exception as sql_exc:
                    logger.exception("[tui] SQL Server lookup failed")
                    self._sql_error = str(sql_exc)

        # --- Analytic-account-lookup + auto-create ---
        file_numbers = sorted(
            {fn for fn in ticket_to_file.values() if fn and fn not in error_filenumbers}
        )
        analytic_map: dict[str, int] = {}  # search_key → analytic_account_id

        if file_numbers:
            file_to_analytic = build_analytic_account_map(
                odoo_client,
                file_numbers,
                company_id,
            )

            # Auto-creëer ontbrekende analytic accounts
            missing_fns = [fn for fn in file_numbers if fn not in file_to_analytic]
            if missing_fns:
                analytic_plan = str(
                    config.get("tui_analytic_plan") or "File number"
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
                        "[tui] Auto-created %d analytic account(s): %s",
                        len(newly_created),
                        list(newly_created.keys()),
                    )
                except Exception as exc_create:
                    logger.error(
                        "[tui] Failed to auto-create analytic accounts: %s",
                        exc_create,
                    )
                    self._analytic_create_error = str(exc_create)

            # Bouw search_key → analytic_id-map
            for search_key, fn in ticket_to_file.items():
                if fn in file_to_analytic:
                    analytic_map[search_key] = file_to_analytic[fn]

        # --- Markeer needs-review-rijen (tickets zonder analytic-verdeling) ---
        for row in all_rows:
            search_key = row.get("_search_key", "")
            if search_key and search_key not in analytic_map:
                fn = ticket_to_file.get(search_key, "")
                if error_filenumbers and fn in error_filenumbers:
                    reason = f"FileNumber is an error filenumber ({fn})"
                elif fn:
                    reason = f"Ticket found in SQL Server (FileNumber={fn}) but no analytic account resolved"
                else:
                    reason = "Ticket not found in SQL Server DB"
                self._needs_review_rows.append(
                    {
                        "reason": reason,
                        "col_group": row["col_group"],
                        "col_ticket_ref": row["col_ticket_ref"],
                        "col_description": row["col_description"],
                        "col_amount": row["col_amount"],
                        "_search_key": search_key,
                    }
                )

        # --- Groepeer rijen op col_group ---
        groups: dict[str, list[dict]] = {}
        for row in all_rows:
            groups.setdefault(row["col_group"], []).append(row)

        # --- Bouw invoice-payloads per groep ---
        moves: list[MovePayload] = []
        summary_rows: list[dict] = []

        for col_group, group_rows in groups.items():
            net_amount = sum(r["col_amount"] for r in group_rows)

            # Bepaal valuta voor de groep (gebruik valuta van eerste rij)
            group_currency = group_rows[0].get("col_currency", "EUR")
            currency_id = currency_map.get(group_currency)

            payload = build_invoice_payload(
                cfg=cfg,
                col_group=col_group,
                tui_ref=tui_ref,
                invoice_date=invoice_date,
                currency_id=currency_id,
                rows=group_rows,
                ticket_gl_account_id=ticket_gl_account_id,
                comm_gl_account_id=comm_gl_account_id,
                tax_id=tax_id,
                analytic_map=analytic_map,
            )

            file_ref = tui_ref.replace("TUI-", "", 1)
            payment_ref = f"TUI-{file_ref}-{col_group}"

            moves.append(
                MovePayload(
                    payload=payload,
                    move_type=payload["move_type"],
                    ref=payment_ref,
                )
            )

            # Samenvattingsdata
            needs_review_count = sum(
                1
                for r in group_rows
                if r.get("_search_key") and r["_search_key"] not in analytic_map
            )
            summary_rows.append(
                {
                    "col_group": col_group,
                    "invoice_ref": payment_ref,
                    "invoice_date": invoice_date,
                    "line_count": len(group_rows),
                    "total_amount": round(net_amount, 2),
                    "needs_review_count": needs_review_count,
                }
            )

        self._summary_data = summary_rows

        logger.info(
            "[tui] build_moves: %d invoices built, %d rows skipped, %d needs-review",
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
        summary_data: list[dict] = list(getattr(self, "_summary_data", []))

        # Log skip-reden-samenvatting
        if result.skip_report_rows:
            reason_counts = Counter(
                row.get("reason", "Unknown") for row in result.skip_report_rows
            )
            for reason, count in sorted(reason_counts.items(), key=lambda x: -x[1]):
                result.log_messages.append(f"Skipped {count}: {reason}")

        # Log SQL-fout
        sql_error = getattr(self, "_sql_error", None)
        if sql_error:
            result.log_messages.append(
                f"Warning: SQL Server lookup failed — {sql_error}"
            )

        # Log auto-created analytic accounts
        created_analytics: dict = getattr(self, "_created_analytic_accounts", {})
        if created_analytics:
            fns = ", ".join(sorted(created_analytics.keys()))
            result.log_messages.append(
                f"Created {len(created_analytics)} analytic account(s): {fns}"
            )

        analytic_create_error: str | None = getattr(
            self,
            "_analytic_create_error",
            None,
        )
        if analytic_create_error:
            result.log_messages.append(
                f"Warning: Failed to auto-create analytic accounts — "
                f"{analytic_create_error}"
            )

        # Voeg Needs Review- en Summary-tabs toe
        if needs_review_rows:
            result.extra_report_data["Needs Review"] = needs_review_rows
        if summary_data:
            result.extra_report_data["tui_summary"] = summary_data

        if not moves:
            result.log_messages.append("No invoices to create.")
            return result

        total_moves = len(moves)

        # ------------------------------------------------------------------
        # Idempotentie-check: batch-search op payment_reference
        # ------------------------------------------------------------------
        all_refs = [m.ref for m in moves]
        existing_set: set[str] = set()

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
                        existing_set.add(pr)
            except Exception as exc:
                logger.warning("[tui] Idempotency check failed: %s", exc)

        logger.info(
            "[tui] Idempotency: %d already exist out of %d refs",
            len(existing_set),
            len(all_refs),
        )

        # ------------------------------------------------------------------
        # Maak invoices aan
        # ------------------------------------------------------------------
        all_invoice_ids: list[int] = []

        for idx, m in enumerate(moves):
            if on_progress and idx % 10 == 0:
                on_progress(
                    "executing",
                    idx,
                    total_moves,
                    f"Creating invoices: {idx}/{total_moves}",
                )

            result.items_processed += 1

            if m.ref in existing_set:
                result.skipped += 1
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
                result.log_messages.append(f"Create error ref={m.ref}: {exc}")
                result.skip_report_rows.append(
                    {
                        "reason": f"Create error: {exc}",
                        "payment_reference": m.ref,
                    }
                )

        if dry_run:
            result.log_messages.append(
                f"[dry-run] Would create {result.created} invoice(s)"
            )
            if needs_review_rows:
                result.log_messages.append(
                    f"[dry-run] {len(needs_review_rows)} line(s) without "
                    "analytic distribution (Needs Review)"
                )
            return result

        if not all_invoice_ids:
            result.log_messages.append("No new invoices created.")

        # ------------------------------------------------------------------
        # Post invoices (batch)
        # ------------------------------------------------------------------
        if auto_post and all_invoice_ids:
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
                        "[tui] Batch post failed, falling back per-move: %s",
                        exc,
                    )
                    for mid in chunk:
                        try:
                            post_moves(odoo_client, [mid], company_id)
                        except Exception as exc2:
                            result.errors += 1
                            result.log_messages.append(
                                f"Post error move_id={mid}: {exc2}"
                            )

        # ------------------------------------------------------------------
        # Verrijk samenvatting met Odoo-entrynummers
        # ------------------------------------------------------------------
        if summary_data and all_refs:
            try:
                ref_to_name: dict[str, str] = {}
                for i in range(0, len(all_refs), IDEMPOTENCY_CHUNK):
                    chunk = all_refs[i : i + IDEMPOTENCY_CHUNK]
                    found = odoo_conn.search_read(
                        odoo_client,
                        "account.move",
                        [
                            ("payment_reference", "in", chunk),
                            ("company_id", "=", company_id),
                        ],
                        ["name", "payment_reference"],
                    )
                    for rec in found:
                        pr = str(rec.get("payment_reference") or "").strip()
                        name = str(rec.get("name") or "").strip()
                        if pr and name:
                            ref_to_name[pr] = name
                for row in summary_data:
                    ref = row.get("invoice_ref", "")
                    if ref in ref_to_name:
                        row["odoo_entry"] = ref_to_name[ref]
                # Verrijk ook needs_review-rijen met Odoo-entrynummers
                if needs_review_rows:
                    group_to_entry: dict[str, str] = {}
                    for ref, name in ref_to_name.items():
                        col_group = ref.rsplit("-", 1)[-1]
                        group_to_entry[col_group] = name
                    for row in needs_review_rows:
                        grp = row.get("col_group", "")
                        if grp in group_to_entry:
                            row["odoo_entry"] = group_to_entry[grp]
            except Exception as exc:
                logger.warning("[tui] Failed to fetch Odoo entry numbers: %s", exc)

        if all_invoice_ids:
            result.log_messages.append(
                f"Created and posted {len(all_invoice_ids)} TUI invoice(s)"
            )
        return result
