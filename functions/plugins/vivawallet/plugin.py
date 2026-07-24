"""Vivawallet-import-plugin — poort van
`travel-experts-backend/apps/main/app/plugins/vivawallet/plugin.py`.

Odoo-toegang herschreven naar `odoo_conn`.
"""

from __future__ import annotations

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
from plugins.vivawallet.excel_reader import read_viva_excel
from plugins.vivawallet.transform import VivaConfig, build_moves_from_rows
from shared.move_utils import post_moves, reconcile_invoice_lines


class VivawalletPlugin(ImportPlugin):
    def get_meta(self) -> PluginMeta:
        return PluginMeta(
            name="vivawallet",
            display_name="Vivawallet",
            accepted_extensions=[".xlsx"],
            description="Reads Vivawallet payment Excel exports and creates misc journal entries in Odoo.",
        )

    def validate_file(self, file_path: Path) -> ValidationResult:
        if not file_path.exists():
            return ValidationResult(valid=False, errors=["File not found"])
        if file_path.suffix.lower() not in (".xlsx", ".xls"):
            return ValidationResult(valid=False, errors=["Expected .xlsx file"])

        try:
            df = read_viva_excel(str(file_path))
            return ValidationResult(valid=True, row_count=len(df))
        except Exception as exc:
            return ValidationResult(valid=False, errors=[str(exc)])

    def parse(self, file_path: Path, config: dict[str, Any]) -> ParsedData:
        df = read_viva_excel(str(file_path))
        return ParsedData(
            items=[df],
            metadata={"row_count": len(df)},
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

        card_gl_map = config.get("card_gl_map", {})
        if isinstance(card_gl_map, dict):
            card_gl_map = {str(k).lower(): int(v) for k, v in card_gl_map.items()}

        viva_cfg = VivaConfig(
            company_id=company_id,
            move_prefix=str(config.get("move_prefix", "VIVA")),
            journal_id=int(config["journal_id"]) if config.get("journal_id") else None,
            account_vivawallet=int(config.get("account_vivawallet", 550100)),
            account_costs=int(config.get("account_costs", 652000)),
            account_clients=int(config.get("account_clients", 400000)),
            account_suspense=int(config.get("account_suspense", 499100)),
            card_gl_map=card_gl_map,
        )

        moves_payload, stats = build_moves_from_rows(df, odoo_client, viva_cfg)

        result = []
        for payload, meta in moves_payload:
            meta["move_prefix"] = viva_cfg.move_prefix
            result.append(
                MovePayload(
                    payload=payload,
                    move_type="entry",
                    ref=meta.get("transaction_id", ""),
                    meta=meta,
                )
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
        review_rows: list[dict] = []
        total_moves = len(moves)

        if not moves:
            return result

        # Haal move_prefix en journal_id uit de eerste move
        journal_id = moves[0].payload.get("journal_id")

        # Verzamel alle Transaction ID's voor batch-idempotentie-check (narration-veld)
        all_tids = [m.meta.get("transaction_id", "") for m in moves]
        all_tids = [t for t in all_tids if t]
        existing_tids: set[str] = set()
        if all_tids and journal_id:
            if on_progress:
                on_progress("executing", 0, total_moves, "Checking existing entries...")
            # Batch-search op narration (Transaction ID)
            for i in range(0, len(all_tids), 200):
                chunk = all_tids[i : i + 200]
                found = odoo_conn.search_read(
                    odoo_client,
                    "account.move",
                    [
                        ("narration", "in", chunk),
                        ("journal_id", "=", journal_id),
                        ("company_id", "=", company_id),
                    ],
                    fields=["narration"],
                )
                existing_tids.update(
                    r["narration"] for r in found if r.get("narration")
                )

        # Bevraag Odoo voor bestaande volgnummers per clearance-datum-prefix
        # zodat we verder nummeren vanaf de hoogste bestaande entry
        seq_by_date: dict[str, int] = {}
        clearance_dates = {m.meta.get("clearance_date_compact", "") for m in moves}
        clearance_dates.discard("")
        if clearance_dates and journal_id:
            for cd in clearance_dates:
                # Zoek naar moves met namen die dit datumpatroon matchen
                found = odoo_conn.search_read(
                    odoo_client,
                    "account.move",
                    [
                        ("name", "like", f"/{cd}/"),
                        ("journal_id", "=", journal_id),
                        ("company_id", "=", company_id),
                    ],
                    fields=["name"],
                )
                if found:
                    # Zoek hoogste seq uit namen zoals "B15/20260202/00042"
                    max_seq = 0
                    for rec in found:
                        try:
                            seq_str = rec["name"].rsplit("/", 1)[-1]
                            max_seq = max(max_seq, int(seq_str))
                        except (ValueError, IndexError):
                            pass
                    seq_by_date[cd] = max_seq

        # Haal move_prefix uit meta (gezet door build_moves vanuit config)
        move_prefix = moves[0].meta.get("move_prefix", "VIVA") if moves else "VIVA"

        for idx, m in enumerate(moves):
            if on_progress and idx % 5 == 0:
                on_progress(
                    "executing",
                    idx,
                    total_moves,
                    f"Processing move {idx}/{total_moves}",
                )
            result.items_processed += 1

            move_ref = m.payload.get("ref", "")
            tid = m.meta.get("transaction_id", "")
            cd = m.meta.get("clearance_date_compact", "")

            # Idempotentie: check via Transaction ID (opgeslagen in narration)
            if tid and tid in existing_tids:
                result.skipped += 1
                row = {
                    "reason": "Already exists in Odoo",
                    "transaction_id": tid,
                    "ref": move_ref,
                }
                row.update(m.meta.get("raw", {}))
                result.skip_report_rows.append(row)
                continue
            # Terugval voor rijen zonder Transaction ID: check via ref + journal
            if not tid and move_ref and journal_id:
                existing = odoo_conn.search(
                    odoo_client,
                    "account.move",
                    [
                        ("ref", "=", move_ref),
                        ("journal_id", "=", journal_id),
                        ("company_id", "=", company_id),
                    ],
                    limit=1,
                )
                if existing:
                    result.skipped += 1
                    row = {"reason": "Already exists in Odoo (by ref)", "ref": move_ref}
                    row.update(m.meta.get("raw", {}))
                    result.skip_report_rows.append(row)
                    continue

            # Ken sequentiële naam toe (loopt door vanaf bestaande Odoo-entries)
            seq = seq_by_date.get(cd, 0) + 1
            seq_by_date[cd] = seq
            move_name = f"{move_prefix}/{cd}/{seq:05d}"
            m.payload["name"] = move_name

            if dry_run:
                result.created += 1
                if not m.meta.get("invoice_id"):
                    review_row = {
                        "move_name": move_name,
                        "reason": "No matching invoice found — not posted",
                    }
                    review_row.update(m.meta.get("raw", {}))
                    review_rows.append(review_row)
                elif not m.meta.get("partner_id"):
                    review_row = {
                        "move_name": move_name,
                        "reason": "No partner on invoice — not reconciled",
                    }
                    review_row.update(m.meta.get("raw", {}))
                    review_rows.append(review_row)
                continue

            try:
                move_id = odoo_conn.create(odoo_client, "account.move", m.payload)
                result.created += 1
            except Exception as exc:
                result.errors += 1
                result.log_messages.append(f"Create error ref={move_ref}: {exc}")
                row = {"move_name": move_name, "reason": f"Create error: {exc}"}
                row.update(m.meta.get("raw", {}))
                result.skip_report_rows.append(row)
                continue

            invoice_id = m.meta.get("invoice_id")
            partner_id = m.meta.get("partner_id")

            # Houd bij welke entries handmatige review nodig hebben
            if not invoice_id:
                review_row = {
                    "move_name": move_name,
                    "reason": "No matching invoice found — not posted",
                }
                review_row.update(m.meta.get("raw", {}))
                review_rows.append(review_row)
            elif not partner_id:
                review_row = {
                    "move_name": move_name,
                    "reason": "No partner on invoice — not reconciled",
                }
                review_row.update(m.meta.get("raw", {}))
                review_rows.append(review_row)

            if auto_post and invoice_id:
                try:
                    post_moves(odoo_client, [move_id], company_id)
                except Exception as exc:
                    result.errors += 1
                    result.log_messages.append(f"Post error ref={move_ref}: {exc}")
                    review_row = {
                        "move_name": move_name,
                        "reason": f"Post error: {exc}",
                    }
                    review_row.update(m.meta.get("raw", {}))
                    review_rows.append(review_row)
                    continue

            if auto_reconcile and invoice_id and partner_id:
                # Sla reconciliatie enkel over bij overpayments (tx > residual).
                # Gedeeltelijke betalingen en exacte matches worden normaal gereconcilieerd.
                tx_amount = round(abs(m.meta.get("counterpart_amount") or 0.0), 2)
                residual = round(abs(m.meta.get("amount_residual") or 0.0), 2)
                if tx_amount > residual:
                    reason = (
                        f"Overpayment — transaction {tx_amount:.2f} "
                        f"> invoice residual {residual:.2f}"
                    )
                    result.log_messages.append(
                        f"Reconcile skipped ref={move_ref}: {reason}"
                    )
                    review_row = {"move_name": move_name, "reason": reason}
                    review_row.update(m.meta.get("raw", {}))
                    review_rows.append(review_row)
                else:
                    try:
                        account_clients = m.meta.get("account_clients_id")
                        if account_clients:
                            reconcile_invoice_lines(
                                odoo_client,
                                move_id,
                                invoice_id,
                                account_clients,
                                partner_id,
                                company_id,
                            )
                    except Exception as exc:
                        result.log_messages.append(
                            f"Reconcile warning ref={move_ref}: {exc}"
                        )
                        review_row = {
                            "move_name": move_name,
                            "reason": f"Reconcile error: {exc}",
                        }
                        review_row.update(m.meta.get("raw", {}))
                        review_rows.append(review_row)

        # Voeg review-items toe als apart rapport-tabblad
        if review_rows:
            result.extra_report_data["Needs Review"] = review_rows

        return result
