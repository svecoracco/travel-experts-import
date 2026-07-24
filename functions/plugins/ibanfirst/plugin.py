"""IbanFirst bank-statement import plugin — poort van
`travel-experts-backend/apps/main/app/plugins/ibanfirst/plugin.py`.

Odoo-toegang herschreven naar `odoo_conn`.
"""

from __future__ import annotations

import base64
import logging
from pathlib import Path
from typing import Any, Dict

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
from plugins.ibanfirst.csv_reader import read_ibanfirst_csv
from plugins.ibanfirst.transform import (
    IbanFirstConfig,
    build_statement_lines,
    detect_file_currency,
    pair_fx_rows,
)

logger = logging.getLogger(__name__)


class IbanFirstPlugin(ImportPlugin):
    def get_meta(self) -> PluginMeta:
        return PluginMeta(
            name="ibanfirst",
            display_name="IbanFirst",
            accepted_extensions=[".csv"],
            description="Reads IbanFirst bank CSV exports and creates bank statement lines in Odoo.",
        )

    def validate_file(self, file_path: Path) -> ValidationResult:
        if not file_path.exists():
            return ValidationResult(valid=False, errors=["File not found"])
        if file_path.suffix.lower() != ".csv":
            return ValidationResult(valid=False, errors=["Expected .csv file"])

        try:
            df = read_ibanfirst_csv(str(file_path))
            return ValidationResult(valid=True, row_count=len(df))
        except Exception as exc:
            return ValidationResult(valid=False, errors=[str(exc)])

    def parse(self, file_path: Path, config: dict[str, Any]) -> ParsedData:
        df = read_ibanfirst_csv(str(file_path))

        # FX-extractie en -koppeling
        df = pair_fx_rows(df)

        # Detecteer bestandsvaluta
        file_currency = detect_file_currency(df)

        return ParsedData(
            items=[df],
            metadata={
                "row_count": len(df),
                "file_currency": file_currency,
                "file_name": file_path.name,
                "file_path": str(file_path),
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
        df = parsed.items[0]
        file_currency = parsed.metadata.get("file_currency", "EUR")

        # Resolve journal uit journal_map
        journal_map = config.get("journal_map", {})
        if isinstance(journal_map, str):
            import json

            journal_map = json.loads(journal_map)
        journal_map = {str(k).upper(): int(v) for k, v in journal_map.items()}

        journal_id = journal_map.get(file_currency)
        if journal_id is None:
            raise ValueError(
                f"No journal configured for currency '{file_currency}'. "
                f"Add it to journal_map in ibanfirst config. "
                f"Available: {list(journal_map.keys())}"
            )

        iban_cfg = IbanFirstConfig(
            company_id=company_id,
            journal_id=journal_id,
            file_currency=file_currency,
        )

        # Bewaar voor gebruik in execute()
        self._file_path = parsed.metadata.get("file_path")
        self._file_name = parsed.metadata.get("file_name", "ibanfirst.csv")
        self._journal_id = journal_id

        if on_progress:
            on_progress("building", 0, len(df), "Building statement lines...")

        currency_cache: Dict[str, int] = {}
        line_tuples = build_statement_lines(df, iban_cfg, odoo_client, currency_cache)

        result = []
        for i, (payload, meta) in enumerate(line_tuples):
            if on_progress and i % 50 == 0:
                on_progress(
                    "building",
                    i,
                    len(line_tuples),
                    f"Building line {i}/{len(line_tuples)}",
                )
            result.append(
                MovePayload(
                    payload=payload,
                    move_type="statement_line",
                    ref=meta.get("identificatie", ""),
                    meta=meta,
                )
            )

        if on_progress:
            on_progress(
                "building",
                len(result),
                len(result),
                f"Built {len(result)} statement lines",
            )

        return result

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
        total = len(moves)
        all_created_ids: list[int] = []

        # Haal journal_id uit de eerste move voor idempotentiequeries
        journal_id = moves[0].payload.get("journal_id") if moves else None

        for idx, m in enumerate(moves):
            if on_progress and idx % 5 == 0:
                on_progress("executing", idx, total, f"Creating line {idx}/{total}")

            result.items_processed += 1
            ref = m.ref

            # Idempotentie: check of statement-line met deze ref al bestaat
            if ref and journal_id:
                existing = odoo_conn.search(
                    odoo_client,
                    "account.bank.statement.line",
                    [("ref", "=", ref), ("journal_id", "=", journal_id)],
                    limit=1,
                )
                if existing:
                    result.skipped += 1
                    result.log_messages.append(
                        f"Skipped (already in Odoo): ref={ref} "
                        f"amount={m.payload.get('amount')} "
                        f"label={m.payload.get('payment_ref', '')[:60]}"
                    )
                    continue

            if dry_run:
                result.created += 1
                result.log_messages.append(
                    f"[dry-run] Would create: ref={ref} amount={m.payload.get('amount')} "
                    f"label={m.payload.get('payment_ref', '')[:60]}"
                )
                continue

            try:
                line_id = odoo_conn.create(
                    odoo_client, "account.bank.statement.line", m.payload
                )
                all_created_ids.append(line_id)
                result.created += 1
            except Exception as exc:
                result.errors += 1
                result.log_messages.append(f"Create error ref={ref}: {exc}")

        # --- Bank-statement-aanmaak (alleen echte import, als er lijnen aangemaakt werden) ---
        if not dry_run and all_created_ids:
            self._create_bank_statement(odoo_client, moves, all_created_ids, result)

        summary = (
            f"Import complete: {result.created} created, "
            f"{result.skipped} skipped, {result.errors} errors "
            f"(out of {total} lines)"
        )
        result.log_messages.append(summary)

        if on_progress:
            on_progress("done", total, total, summary)

        return result

    def _create_bank_statement(
        self,
        odoo_client: Any,
        moves: list,
        created_ids: list[int],
        result: Any,
    ) -> None:
        """Maak een account.bank.statement aan, koppel de statement-lines en voeg het bronbestand toe."""
        file_name = getattr(self, "_file_name", "ibanfirst.csv")
        file_path = getattr(self, "_file_path", None)
        journal_id = getattr(self, "_journal_id", None)

        # Statement-naam: strip "-enhanced" en eventuele browser-download-suffix zoals " (1)"
        stem = Path(file_name).stem.split("-enhanced")[0]

        # Idempotentie: sla over als statement al bestaat voor deze naam + journal
        try:
            existing = odoo_conn.search(
                odoo_client,
                "account.bank.statement",
                [("name", "=", stem), ("journal_id", "=", journal_id)],
                limit=1,
            )
            if existing:
                result.log_messages.append(
                    f"Bank statement '{stem}' already exists in Odoo — skipped creation."
                )
                return
        except Exception as exc:
            result.log_messages.append(
                f"Warning: Could not check for existing bank statement: {exc}"
            )
            return

        # Gebruik de meest recente datum uit de statement-line-payloads
        max_date = max(
            (m.payload.get("date", "") for m in moves if m.payload.get("date")),
            default="",
        )

        # Maak de bank-statement-header aan
        try:
            statement_payload: Dict[str, Any] = {
                "name": stem,
                "journal_id": journal_id,
            }
            if max_date:
                statement_payload["date"] = max_date

            statement_id = odoo_conn.create(
                odoo_client, "account.bank.statement", statement_payload
            )
            result.log_messages.append(
                f"Bank statement created: '{stem}' (id={statement_id}, {len(created_ids)} lines)"
            )
        except Exception as exc:
            result.log_messages.append(
                f"Warning: Could not create bank statement: {exc}"
            )
            return

        # Koppel alle aangemaakte statement-lines aan de bank statement
        try:
            odoo_conn.write(
                odoo_client,
                "account.bank.statement.line",
                created_ids,
                {
                    "statement_id": statement_id,
                },
            )
        except Exception as exc:
            result.log_messages.append(
                f"Warning: Could not link lines to bank statement: {exc}"
            )

        # Zet opening-/sluitingssaldo
        try:
            # Openingssaldo = sluitingssaldo van het meest recente vorige statement
            prev_stmts = odoo_conn.search_read(
                odoo_client,
                "account.bank.statement",
                [("journal_id", "=", journal_id), ("id", "!=", statement_id)],
                ["balance_end_real", "date"],
            )
            if prev_stmts:
                prev_stmts.sort(key=lambda s: s.get("date") or "", reverse=True)
                balance_start = float(prev_stmts[0].get("balance_end_real") or 0.0)
            else:
                balance_start = 0.0

            # Sluitingssaldo = opening + som van nieuwe lijnbedragen
            line_data = odoo_conn.read(
                odoo_client, "account.bank.statement.line", created_ids, ["amount"]
            )
            total_amount = sum(float(line.get("amount") or 0.0) for line in line_data)
            balance_end_real = balance_start + total_amount

            odoo_conn.write(
                odoo_client,
                "account.bank.statement",
                [statement_id],
                {
                    "balance_start": balance_start,
                    "balance_end_real": balance_end_real,
                },
            )
            result.log_messages.append(
                f"Balances: start={balance_start:.2f}, "
                f"change={total_amount:.2f}, end={balance_end_real:.2f}"
            )
        except Exception as exc:
            result.log_messages.append(
                f"Warning: Could not update statement balances: {exc}"
            )

        # Voeg het bron-CSV-bestand toe aan de bank statement
        if file_path:
            try:
                with open(file_path, "rb") as f:
                    datas = base64.b64encode(f.read()).decode()
                odoo_conn.create(
                    odoo_client,
                    "ir.attachment",
                    {
                        "name": file_name,
                        "res_model": "account.bank.statement",
                        "res_id": statement_id,
                        "datas": datas,
                        "type": "binary",
                    },
                )
                result.log_messages.append("CSV file attached to bank statement.")
            except Exception as exc:
                result.log_messages.append(
                    f"Warning: Could not attach CSV to bank statement: {exc}"
                )
