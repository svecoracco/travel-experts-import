"""BSP purchases import plugin — poort van
`travel-experts-backend/apps/main/app/plugins/bsp/plugin.py`.

Odoo-toegang herschreven naar `odoo_conn` (pakket-transport, JSON-2);
parser/moves-logica en de idempotentie (batch `search_read` op `ref` +
`move_type`) 1-op-1 behouden.
"""

from __future__ import annotations

import logging
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
from plugins.bsp.moves import (
    CashClearingLine,
    build_bsp_config,
    build_consolidated_cash_misc,
    build_moves_for_line,
    build_ref,
    card_gl_from_payment,
)
from plugins.bsp.parser import (
    count_data_rows,
    input_stem,
    read_bsp_csv,
    validate_csv_columns,
)
from shared.account_utils import (
    build_analytic_account_map,
    create_analytic_accounts,
    resolve_account_id,
)
from shared.move_utils import (
    post_moves,
    reconcile_cash_clearing_lines,
    reconcile_clearing_lines,
)
from shared.sql_server import (
    expand_combined_ticket,
    fetch_raw_tickets,
    fetch_ticket_filenumbers,
)

logger = logging.getLogger(__name__)

# Chunk-groottes voor batched Odoo-operaties
IDEMPOTENCY_CHUNK = 200  # refs per search_read-call
POST_CHUNK = 50  # move-ID's per action_post-call


def _parsed_to_row(parsed: Any, reason: str, **extra: Any) -> dict[str, Any]:
    """Bouw een skip-report-rij uit een ParsedLine."""
    row: dict[str, Any] = {
        "reason": reason,
        "doc_number": parsed.doc_number or "",
        "issue_date": parsed.issue_date or "",
        "doc_type": parsed.doc_type or "",
        "airline_code": parsed.airline_code or "",
        "airline_name": parsed.airline_name or "",
        "currency": parsed.currency or "",
        "total": parsed.total_amount if parsed.total_amount is not None else "",
        "payment_method": parsed.payment_method or "",
        "payment_ref": parsed.payment_ref or "",
        "card": parsed.card_ref_short or "",
        "file_number": "",
        "analytic_id": "",
        "error_detail": "",
        "raw_line": parsed.raw_line,
    }
    row.update(extra)
    return row


class BspPlugin(ImportPlugin):
    def get_meta(self) -> PluginMeta:
        return PluginMeta(
            name="bsp",
            display_name="BSP Purchases",
            accepted_extensions=[".csv"],
            description="Parses BSP CSV billing analysis files and creates purchase invoices + misc entries in Odoo.",
        )

    def validate_file(self, file_path: Path) -> ValidationResult:
        if not file_path.exists():
            return ValidationResult(valid=False, errors=["File not found"])
        if file_path.suffix.lower() != ".csv":
            return ValidationResult(valid=False, errors=["Expected .csv file"])

        valid, errors = validate_csv_columns(str(file_path))
        if not valid:
            return ValidationResult(valid=False, errors=errors)

        row_count = count_data_rows(str(file_path))
        if row_count == 0:
            return ValidationResult(valid=False, errors=["No data rows found"])

        return ValidationResult(valid=True, row_count=row_count)

    def parse(self, file_path: Path, config: dict[str, Any]) -> ParsedData:
        stem = input_stem(str(file_path))
        parsed_lines = read_bsp_csv(str(file_path))

        # Detecteer periode en laatste datum voor de geconsolideerde entry
        periods = set(line.period for line in parsed_lines if line.period)
        period = sorted(periods)[0] if periods else "unknown"

        dates = [line.issue_date for line in parsed_lines if line.issue_date]
        latest_date = max(dates) if dates else None

        return ParsedData(
            items=parsed_lines,
            metadata={
                "stem": stem,
                "period": period,
                "latest_date": latest_date,
            },
        )

    def build_moves(
        self,
        parsed: ParsedData,
        odoo_client: Any,
        config: dict[str, Any],
        company_id: int,
        on_progress: ProgressCallback = None,
    ) -> list[MovePayload]:
        bsp_cfg = build_bsp_config(config, company_id)
        self._bsp_cfg = bsp_cfg
        stem = parsed.metadata["stem"]
        period = parsed.metadata["period"]
        latest_date = parsed.metadata["latest_date"]
        account_cache: dict[int, int] = {}
        tax_cache: dict[str, int] = {}
        moves: list[MovePayload] = []
        self._skip_rows: list[dict] = []
        self._needs_review_rows: list[dict] = (
            []
        )  # lijnen geïmporteerd zonder DB-file-number
        # Reset per-run state zodat oude waarden niet doorlekken naar volgende runs
        self._sql_error: str | None = None
        self._pass3_error: str | None = None
        self._analytic_create_error: str | None = None
        self._created_analytic_accounts: dict = {}
        # Houd bij welke refs cash zijn (voor reconciliatie in execute)
        self._cash_invoice_refs: set[str] = set()

        # --- Airline-codes-config voor het samenvattingsrapport ---
        airline_names: dict[str, str] = config.get("bsp_airlines_codes", {})
        if isinstance(airline_names, str):
            import json

            try:
                airline_names = json.loads(airline_names)
            except (json.JSONDecodeError, ValueError):
                airline_names = {}
        self._airline_names = airline_names

        # --- Bouw airline code → naam map uit CSV-data + config ---
        csv_airline_names: dict[str, str] = {}
        for line in parsed.items:
            if line.airline_code and line.airline_name:
                csv_airline_names.setdefault(line.airline_code, line.airline_name)
        # Config overschrijft CSV-namen indien opgegeven
        merged_airline_names = {**csv_airline_names, **airline_names}
        self._airline_names = merged_airline_names

        # --- Accumuleer airline-totalen over ALLE geparste lijnen ---
        airline_totals: dict[tuple[str, str, str], dict[str, Any]] = {}
        for line in parsed.items:
            if line.payment_method == "skip":
                continue
            code = line.airline_code or "???"
            doc = line.doc_type or "???"

            if line.payment_method == "split":
                # Split-betalingen: kaartdeel op card-GL, cash-deel op BSP Cash
                card_gl = card_gl_from_payment(
                    line.payment_ref, line.card_prefix4, bsp_cfg
                )
                card_gl_label = str(card_gl) if card_gl is not None else "unknown"
                card_key = (code, doc, card_gl_label)
                if card_key not in airline_totals:
                    airline_totals[card_key] = {"count": 0, "total": 0.0}
                airline_totals[card_key]["count"] += 1
                if line.card_amount is not None:
                    airline_totals[card_key]["total"] += float(line.card_amount)

                cash_gl_label = str(bsp_cfg.cash_account_code)
                cash_key = (code, doc, cash_gl_label)
                if cash_key not in airline_totals:
                    airline_totals[cash_key] = {"count": 0, "total": 0.0}
                # Verhoog count niet opnieuw — het is hetzelfde ticket
                if line.cash_amount is not None:
                    airline_totals[cash_key]["total"] += float(line.cash_amount)
            else:
                gl_code = card_gl_from_payment(
                    line.payment_ref, line.card_prefix4, bsp_cfg
                )
                gl_label = str(gl_code) if gl_code is not None else "unknown"
                key = (code, doc, gl_label)
                if key not in airline_totals:
                    airline_totals[key] = {"count": 0, "total": 0.0}
                airline_totals[key]["count"] += 1
                if line.total_amount is not None:
                    airline_totals[key]["total"] += float(line.total_amount)
        self._airline_totals = airline_totals

        # --- Filter geldige lijnen ---
        valid_lines = []
        for line in parsed.items:
            if line.payment_method == "skip":
                self._skip_rows.append(_parsed_to_row(line, "Zero amount / void"))
                continue
            if not line.doc_number or not line.issue_date:
                self._skip_rows.append(_parsed_to_row(line, "No doc/date"))
                continue
            if line.total_amount is None or float(line.total_amount) == 0.0:
                self._skip_rows.append(_parsed_to_row(line, "No/zero total"))
                continue
            valid_lines.append(line)

        # --- Verzamel ticket-sleutels voor bulk SQL Server-lookup ---
        ticket_set = set()
        for line in valid_lines:
            if line.ticket_key:
                ticket_set.add(line.ticket_key)

        # --- SQL Server: ticket → file number (bulk, twee-pass) ---
        ticket_to_file: dict[str, str] = {}
        ticket_to_analytic_id: dict[str, int] = {}

        # Bouw airline_code → doc_number-mapping voor pass 2
        ticket_airline: dict[str, str] = {}
        for line in valid_lines:
            if line.ticket_key and line.airline_code:
                ticket_airline[line.ticket_key] = line.airline_code

        table_name = str(bsp_cfg.bts_table or "").strip()
        ticket_col = str(bsp_cfg.bts_ticket_col or "").strip()
        if ticket_set and table_name and ticket_col:
            db_cfg = {
                "sql_connection_string": config.get("sql_connection_string", ""),
                "timeout": str(config.get("sql_db_timeout", 10)),
                "query_timeout": str(config.get("sql_db_query_timeout", 120)),
                "chunk_size": str(config.get("sql_db_chunk_size", 500)),
            }
            try:
                # Pass 1: zoek op 10-cijferig doc-nummer direct
                ticket_to_file = fetch_ticket_filenumbers(
                    db_cfg,
                    sorted(ticket_set),
                    table_name,
                    ticket_col,
                )
                logger.info(
                    "DB lookup pass 1: %d/%d tickets found",
                    len(ticket_to_file),
                    len(ticket_set),
                )

                # Pass 2: herprobeer niet-gematchte tickets met airline-code-prefix
                # Probeert zowel geconcateneerd (0741100607144) als dash-gescheiden
                # (074-1100607144)
                unmatched = [t for t in ticket_set if t not in ticket_to_file]
                if unmatched:
                    prefixed_tickets = []
                    prefixed_to_original: dict[str, str] = {}
                    for t in unmatched:
                        ac = ticket_airline.get(t, "")
                        if ac:
                            # Geconcateneerd formaat: 0741100607144
                            prefixed = f"{ac}{t}"
                            prefixed_tickets.append(prefixed)
                            prefixed_to_original[prefixed[:10]] = t
                            # Dash-gescheiden formaat: 074-1100607144
                            prefixed_dash = f"{ac}-{t}"
                            prefixed_tickets.append(prefixed_dash)
                            prefixed_to_original[prefixed_dash[:10]] = t

                    if prefixed_tickets:
                        pass2_results = fetch_ticket_filenumbers(
                            db_cfg,
                            sorted(prefixed_tickets),
                            table_name,
                            ticket_col,
                        )
                        # Map resultaten terug naar originele ticket-sleutels
                        for db_key, filenumber in pass2_results.items():
                            original = prefixed_to_original.get(db_key)
                            if original and original not in ticket_to_file:
                                ticket_to_file[original] = filenumber
                        logger.info(
                            "DB lookup pass 2 (airline prefix): %d/%d unmatched tickets found",
                            len(pass2_results),
                            len(unmatched),
                        )

                # Pass 3: suffix-expansie voor gecombineerde tickets (bv. 0826330738683-84-85)
                unmatched3 = [t for t in ticket_set if t not in ticket_to_file]
                if unmatched3:
                    # Bouw korte prefixen voor bredere zoekopdracht: airline_code + eerste
                    # 7 tekens van doc_number
                    short_prefixes: list[str] = []
                    prefix_to_targets: dict[str, set[str]] = (
                        {}
                    )  # prefix -> set van target-ticket-sleutels
                    for t in unmatched3:
                        ac = ticket_airline.get(t, "")
                        if ac:
                            # Gebruik eerste 7 tekens van doc_number met airline-prefix
                            # Zoek zowel geconcateneerd (0742954346) als dash-gescheiden
                            # (074-2954346)
                            short = f"{ac}{t[:7]}"
                            short_dash = f"{ac}-{t[:7]}"
                            short_prefixes.append(short)
                            short_prefixes.append(short_dash)
                            prefix_to_targets.setdefault(short, set()).add(t)
                            prefix_to_targets.setdefault(short_dash, set()).add(t)

                    if short_prefixes:
                        try:
                            raw_rows = fetch_raw_tickets(
                                db_cfg,
                                short_prefixes,
                                table_name,
                                ticket_col,
                            )
                            pass3_found = 0
                            for full_ticket, filenumber in raw_rows:
                                expanded = expand_combined_ticket(full_ticket)
                                for exp in expanded:
                                    # Match laatste 10 tekens van geëxpandeerd ticket tegen unmatched
                                    exp_key = exp[-10:] if len(exp) >= 10 else exp
                                    if (
                                        exp_key in unmatched3
                                        and exp_key not in ticket_to_file
                                    ):
                                        ticket_to_file[exp_key] = filenumber
                                        pass3_found += 1
                            logger.info(
                                "DB lookup pass 3 (suffix expansion): %d/%d unmatched tickets found",
                                pass3_found,
                                len(unmatched3),
                            )
                        except Exception as exc3:
                            logger.warning("Pass 3 suffix expansion failed: %s", exc3)
                            self._pass3_error = str(exc3)

            except Exception as sql_exc:
                logger.exception(
                    "SQL Server lookup failed; analytic distribution will be skipped"
                )
                self._sql_error = str(sql_exc)
                ticket_to_file = {}

            # --- Odoo: file number → analytic account (bulk) ---
            if ticket_to_file:
                file_numbers = sorted({fn for fn in ticket_to_file.values() if fn})
                file_to_analytic = build_analytic_account_map(
                    odoo_client,
                    file_numbers,
                    company_id,
                )

                # Auto-creëer analytic accounts voor file numbers die nog niet in Odoo staan
                missing_fns = [fn for fn in file_numbers if fn not in file_to_analytic]
                if missing_fns:
                    analytic_plan = str(
                        config.get("bsp_analytic_plan") or "File number"
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
                            "Auto-created %d analytic accounts: %s",
                            len(newly_created),
                            list(newly_created.keys()),
                        )
                    except Exception as exc_create:
                        logger.error(
                            "Failed to auto-create analytic accounts: %s", exc_create
                        )
                        self._analytic_create_error = str(exc_create)

                ticket_to_analytic_id = {
                    t: file_to_analytic[fn]
                    for t, fn in ticket_to_file.items()
                    if fn in file_to_analytic
                }

            logger.info(
                "Analytic mapping: tickets=%d file_numbers=%d analytics=%d",
                len(ticket_set),
                len({fn for fn in ticket_to_file.values() if fn}),
                len(ticket_to_analytic_id),
            )
        else:
            if ticket_set:
                logger.warning(
                    "SQL Server lookup skipped: missing bts_table or bts_ticket_col config"
                )

        # --- Bouw move-payloads (twee-pass) ---
        # Pass 1: Bouw invoices + card-misc-entries, verzamel cash-clearing-data
        cash_clearing_lines: list[CashClearingLine] = []
        total_valid = len(valid_lines)

        for idx, line in enumerate(valid_lines):
            if on_progress and idx % 10 == 0:
                on_progress(
                    "building", idx, total_valid, f"Building moves: {idx}/{total_valid}"
                )

            ticket10 = (line.doc_number or "")[:10]
            ticket_key = (line.ticket_key or ticket10)[:10]
            file_number = ticket_to_file.get(ticket_key)
            ref = build_ref(
                ticket10, line.card_ref_short, period, file_number=file_number
            )

            # Bepaal analytic-verdeling (gebruikt ticket_key voor SQL Server-lookup)
            analytic_id = ticket_to_analytic_id.get(ticket_key)
            analytic_distribution = None
            needs_review_line = ticket_key not in ticket_to_file
            if needs_review_line:
                # Toch importeren, maar markeren voor de Needs Review-tab
                # (dry-run toont als skipped)
                self._needs_review_rows.append(
                    _parsed_to_row(
                        line,
                        "No file number in DB",
                    )
                )
            elif analytic_id is None:
                fn = ticket_to_file.get(ticket_key, "")
                self._skip_rows.append(
                    _parsed_to_row(
                        line,
                        "No analytic account in Odoo",
                        file_number=fn,
                        error_detail=f"FileNumber {fn} has no analytic account",
                    )
                )
            else:
                analytic_distribution = {str(analytic_id): 100.0}

            try:
                payloads = build_moves_for_line(
                    line,
                    ticket10,
                    ref,
                    bsp_cfg,
                    company_id,
                    bsp_cfg.currency_id,
                    account_cache,
                    odoo_client,
                    analytic_distribution=analytic_distribution,
                    tax_id_cache=tax_cache,
                )
            except Exception as exc:
                self._skip_rows.append(
                    _parsed_to_row(
                        line,
                        "Build error",
                        file_number=ticket_to_file.get(ticket_key, ""),
                        error_detail=str(exc),
                    )
                )
                continue

            for payload in payloads:
                move_type = payload.get("move_type", "entry")
                moves.append(
                    MovePayload(
                        payload=payload,
                        move_type=move_type,
                        ref=ref,
                        meta={"needs_review": True} if needs_review_line else {},
                    )
                )

            # Verzamel cash-clearing-data
            if line.payment_method == "cash":
                self._cash_invoice_refs.add(ref)
                cash_clearing_lines.append(
                    CashClearingLine(
                        ref=ref,
                        amount=float(line.total_amount),
                    )
                )
            elif line.payment_method == "split" and line.cash_amount is not None:
                # Het kleine cash-deel van split-betalingen
                cash_clearing_lines.append(
                    CashClearingLine(
                        ref=ref,
                        amount=float(line.cash_amount),
                    )
                )

        # Pass 2: Bouw geconsolideerde cash-misc-entry
        if cash_clearing_lines and latest_date:
            try:
                consolidated = build_consolidated_cash_misc(
                    cash_clearing_lines,
                    bsp_cfg,
                    company_id,
                    bsp_cfg.currency_id,
                    account_cache,
                    odoo_client,
                    entry_date=latest_date,
                    period=period,
                    stem=stem,
                )
                consolidated_ref = consolidated["ref"]
                self._consolidated_cash_ref = consolidated_ref
                moves.append(
                    MovePayload(
                        payload=consolidated,
                        move_type="entry",
                        ref=consolidated_ref,
                    )
                )
                logger.info(
                    "Built consolidated cash misc entry: %d clearing lines, ref=%s",
                    len(cash_clearing_lines),
                    consolidated_ref,
                )
            except Exception as exc:
                logger.exception("Failed to build consolidated cash misc entry")
                logger.warning("%s", exc)
                self._consolidated_cash_ref = None
        else:
            self._consolidated_cash_ref = None

        return moves

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
        result.skip_report_rows = getattr(self, "_skip_rows", [])
        result.skipped = len(result.skip_report_rows)
        needs_review_rows = getattr(self, "_needs_review_rows", [])

        # Vat skip-redenen samen in log_messages zodat het UI-logpaneel ze toont
        if result.skip_report_rows:
            from collections import Counter

            reason_counts = Counter(
                row.get("reason", "Unknown") for row in result.skip_report_rows
            )
            for reason, count in sorted(reason_counts.items(), key=lambda x: -x[1]):
                result.log_messages.append(f"Skipped {count}: {reason}")

        # Log needs-review-lijnen (geïmporteerd zonder file number)
        if needs_review_rows:
            prefix = "[dry-run] " if dry_run else ""
            result.log_messages.append(
                f"{prefix}{len(needs_review_rows)} line(s) imported without file number (Needs Review)"
            )

        # Toon SQL Server-lookup-fouten/-statistieken in de UI-log
        sql_error = getattr(self, "_sql_error", None)
        if sql_error:
            result.log_messages.append(
                f"Warning: SQL Server lookup failed — {sql_error}"
            )
        pass3_error = getattr(self, "_pass3_error", None)
        if pass3_error:
            result.log_messages.append(
                f"Warning: SQL Server pass 3 (suffix expansion) failed — {pass3_error}"
            )
        created_analytics = getattr(self, "_created_analytic_accounts", {})
        if created_analytics:
            fns = ", ".join(sorted(created_analytics.keys()))
            result.log_messages.append(
                f"Created {len(created_analytics)} analytic account(s): {fns}"
            )
        analytic_create_error = getattr(self, "_analytic_create_error", None)
        if analytic_create_error:
            result.log_messages.append(
                f"Warning: Failed to auto-create analytic accounts — {analytic_create_error}"
            )

        # Voeg airline-samenvatting toe aan het rapport
        airline_totals = getattr(self, "_airline_totals", {})
        airline_names = getattr(self, "_airline_names", {})
        if airline_totals:
            summary_rows = []
            for (code, doc, gl_account), stats in sorted(airline_totals.items()):
                summary_rows.append(
                    {
                        "airline_code": code,
                        "airline_name": airline_names.get(code, ""),
                        "doc_type": doc,
                        "account_code": gl_account,
                        "count": stats["count"],
                        "total_amount": round(stats["total"], 2),
                    }
                )
            result.extra_report_data["airline_summary"] = summary_rows

        bsp_cfg = getattr(self, "_bsp_cfg", None)
        cash_invoice_refs = getattr(self, "_cash_invoice_refs", set())
        consolidated_cash_ref = getattr(self, "_consolidated_cash_ref", None)

        # Groepeer moves op ref
        ref_groups: dict[str, list[MovePayload]] = {}
        for m in moves:
            ref_groups.setdefault(m.ref, []).append(m)

        # --- Bulk-idempotentie-check ---
        all_refs = list(ref_groups.keys())
        existing_set: set[tuple[str, str]] = set()
        bulk_check_ok = True
        for i in range(0, len(all_refs), IDEMPOTENCY_CHUNK):
            chunk = all_refs[i : i + IDEMPOTENCY_CHUNK]
            try:
                found = odoo_conn.search_read(
                    odoo_client,
                    "account.move",
                    [("ref", "in", chunk), ("company_id", "=", company_id)],
                    ["ref", "move_type"],
                )
                for rec in found:
                    existing_set.add((rec["ref"], rec["move_type"]))
            except Exception as exc:
                logger.warning(
                    "Bulk idempotency check failed, falling back to per-move: %s", exc
                )
                bulk_check_ok = False
                break

        logger.info(
            "Idempotency check: %d existing moves found for %d refs (bulk=%s)",
            len(existing_set),
            len(all_refs),
            bulk_check_ok,
        )

        # --- Maak moves aan ---
        all_created_ids: list[int] = []
        ref_created: dict[str, list[int]] = {}
        total_refs = len(ref_groups)
        consolidated_misc_id: int | None = None

        for ref_idx, (ref, group) in enumerate(ref_groups.items()):
            if on_progress and ref_idx % 5 == 0:
                on_progress(
                    "executing",
                    ref_idx,
                    total_refs,
                    f"Creating moves: {ref_idx}/{total_refs}",
                )
            result.items_processed += 1
            created_ids: list[int] = []

            for m in group:
                is_needs_review = m.meta.get("needs_review", False)

                if bulk_check_ok:
                    already_exists = (m.ref, m.move_type) in existing_set
                else:
                    existing = odoo_conn.search(
                        odoo_client,
                        "account.move",
                        [
                            ("ref", "=", m.ref),
                            ("move_type", "=", m.move_type),
                            ("company_id", "=", company_id),
                        ],
                        limit=1,
                    )
                    already_exists = bool(existing)

                if already_exists:
                    result.skipped += 1
                    continue
                if dry_run:
                    # In dry-run: needs_review-moves tellen niet als "created"
                    if not is_needs_review:
                        result.created += 1
                    continue

                try:
                    move_id = odoo_conn.create(odoo_client, "account.move", m.payload)
                    created_ids.append(move_id)
                    all_created_ids.append(move_id)
                    # needs_review-moves tellen via extra_report_data, niet result.created
                    if not is_needs_review:
                        result.created += 1

                    # Volg het ID van de geconsolideerde cash-misc-entry
                    if ref == consolidated_cash_ref and m.move_type == "entry":
                        consolidated_misc_id = move_id
                except Exception as exc:
                    result.errors += 1
                    result.log_messages.append(f"Create error ref={ref}: {exc}")

            if created_ids:
                ref_created[ref] = created_ids

        # Vul Needs Review-tab in het Excel-rapport (zowel dry-run als echte import)
        if needs_review_rows:
            result.extra_report_data["Needs Review"] = needs_review_rows

        if dry_run:
            return result

        # --- Batch-post ---
        posted_ids: set[int] = set()
        if on_progress and all_created_ids:
            on_progress(
                "posting",
                0,
                len(all_created_ids),
                f"Posting {len(all_created_ids)} moves...",
            )
        if auto_post and all_created_ids:
            for i in range(0, len(all_created_ids), POST_CHUNK):
                chunk = all_created_ids[i : i + POST_CHUNK]
                try:
                    post_moves(odoo_client, chunk, company_id)
                    posted_ids.update(chunk)
                    if on_progress:
                        on_progress(
                            "posting",
                            len(posted_ids),
                            len(all_created_ids),
                            f"Posted {len(posted_ids)}/{len(all_created_ids)} moves",
                        )
                    logger.info(
                        "Posted batch %d-%d (%d moves)", i, i + len(chunk), len(chunk)
                    )
                except Exception as exc:
                    logger.warning(
                        "Batch post failed for chunk %d-%d, falling back to per-move: %s",
                        i,
                        i + len(chunk),
                        exc,
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

            failed_post = set(all_created_ids) - posted_ids
            if failed_post:
                logger.warning("%d moves failed to post", len(failed_post))

        # --- Reconciliatie ---
        if on_progress and ref_created:
            on_progress("reconciling", 0, len(ref_created), "Reconciling moves...")
        if auto_reconcile and bsp_cfg:
            reconcile_account_cache: dict[int, int] = {}
            clearing_id = resolve_account_id(
                odoo_client,
                bsp_cfg.misc_clearing_code,
                company_id,
                reconcile_account_cache,
            )

            # Kaartbetalingen: reconcilieer per ref (invoice + zijn individuele misc-entry)
            for ref, created_ids in ref_created.items():
                if ref == consolidated_cash_ref:
                    continue  # apart hieronder afgehandeld
                if ref in cash_invoice_refs:
                    continue  # cash-invoices worden tegen de geconsolideerde entry gereconcilieerd
                if len(created_ids) < 2:
                    continue
                if auto_post and not all(mid in posted_ids for mid in created_ids):
                    result.log_messages.append(
                        f"Reconcile skipped ref={ref}: not all moves posted"
                    )
                    continue
                try:
                    reconcile_clearing_lines(
                        odoo_client, created_ids, clearing_id, company_id
                    )
                except Exception as exc:
                    result.log_messages.append(f"Reconcile warning ref={ref}: {exc}")

            # Cash-betalingen: reconcilieer elke invoice tegen de geconsolideerde misc-entry
            if consolidated_misc_id and consolidated_misc_id in posted_ids:
                cash_invoice_ids = []
                for ref in cash_invoice_refs:
                    if ref in ref_created:
                        cash_invoice_ids.extend(ref_created[ref])

                if cash_invoice_ids:
                    if on_progress:
                        on_progress(
                            "reconciling",
                            0,
                            len(cash_invoice_ids),
                            f"Reconciling {len(cash_invoice_ids)} cash invoices...",
                        )
                    try:
                        reconcile_cash_clearing_lines(
                            odoo_client,
                            cash_invoice_ids,
                            consolidated_misc_id,
                            clearing_id,
                            company_id,
                        )
                    except Exception as exc:
                        result.log_messages.append(f"Cash reconcile error: {exc}")

        return result
