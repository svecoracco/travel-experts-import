"""Vivawallet transform — poort van
`travel-experts-backend/apps/main/app/plugins/vivawallet/transform.py`.

Odoo-toegang loopt uitsluitend via `shared.account_utils.resolve_account_id`
en `shared.invoice_lookup.*` (beide al herschreven op `odoo_conn`); de
payload-builder hier is verder zuivere Python en ongewijzigd t.o.v. de bron.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd

from shared.account_utils import resolve_account_id
from shared.invoice_lookup import (
    find_invoice_by_merchant_ref,
    get_first_line_analytic_distribution,
)


def card_to_gl(card_type: str, card_gl_map: Dict[str, int], fallback: int) -> int:
    c = (card_type or "").strip().lower()
    return card_gl_map.get(c, fallback)


def is_filled(s: Any) -> bool:
    if s is None:
        return False
    s = str(s).strip()
    return s != "" and s.lower() != "nan"


@dataclass
class BuildStats:
    rows_processed: int = 0
    moves_prepared: int = 0
    invoice_found: int = 0
    invoice_not_found: int = 0


@dataclass
class VivaConfig:
    """Vivawallet-config uit app_config of environment."""

    company_id: int
    move_prefix: str
    journal_id: Optional[int]
    account_vivawallet: int
    account_costs: int
    account_clients: int
    account_suspense: int
    card_gl_map: Dict[str, int]


def build_moves_from_rows(
    df: pd.DataFrame,
    client: Any,
    cfg: VivaConfig,
    debug_lookup: bool = False,
) -> Tuple[List[Tuple[Dict[str, Any], Dict[str, Any]]], BuildStats]:
    stats = BuildStats()
    moves: List[Tuple[Dict[str, Any], Dict[str, Any]]] = []
    debug_lookup = bool(debug_lookup)
    account_id_cache: Dict[int, int] = {}

    def _resolve_account(code: int) -> int:
        return resolve_account_id(
            client, code, cfg.company_id, account_id_cache, debug=debug_lookup
        )

    for _, row in df.iterrows():
        stats.rows_processed += 1

        merchant_ref_raw = row.get("Merchant Reference", "")
        merchant_ref = (
            str(merchant_ref_raw).strip() if is_filled(merchant_ref_raw) else ""
        )
        card_type = str(row.get("Card Type", "")).strip()
        order_code = str(row.get("Order Code", "")).strip()
        source_desc_raw = row.get("Source Description", "")
        customer_desc_raw = row.get("Customer Description", "")
        source_desc = str(source_desc_raw).strip() if is_filled(source_desc_raw) else ""
        customer_desc = (
            str(customer_desc_raw).strip() if is_filled(customer_desc_raw) else ""
        )

        amount = float(row.get("Amount", 0.0) or 0.0)
        net_amount = float(row.get("NetAmount", 0.0) or 0.0)

        transaction_id = row.get("Transaction ID", "")
        has_tid = is_filled(transaction_id)

        fees = (amount - net_amount) if has_tid else 0.0
        base_550 = net_amount if has_tid else amount

        clearance_date = row.get("Clearance Date")
        if pd.isna(clearance_date):
            raise ValueError("Missing Clearance Date on a row.")
        clearing_dt = pd.to_datetime(clearance_date).date()
        booking_date_str = clearing_dt.isoformat()
        clearance_date_compact = clearing_dt.strftime("%Y%m%d")

        invoice_year = datetime.now().year
        if merchant_ref:
            move_ref = f"I/{invoice_year}/{merchant_ref}"
            label_long = f"I/{invoice_year}/{merchant_ref} - paid with: {card_type} - {order_code}"
            label_short = f"I/{invoice_year}/{merchant_ref}"
        else:
            desc_parts = [p for p in [source_desc, customer_desc] if p]
            desc = " - ".join(desc_parts) if desc_parts else "No details"
            label_long = f"No invoice - {desc}"
            label_short = f"No invoice - {desc}"
            move_ref = label_short

        partner_id = False
        analytic_distribution = None
        invoice = find_invoice_by_merchant_ref(
            client,
            merchant_ref,
            cfg.company_id,
            invoice_year=invoice_year,
            debug=debug_lookup,
        )
        if invoice:
            stats.invoice_found += 1
            invoice_ref = str(invoice.get("ref") or "").strip()
            invoice_name = str(invoice.get("name") or "").strip()
            inv_parts = [p for p in [invoice_ref, invoice_name] if p]
            if inv_parts:
                invoice_label = " - ".join(inv_parts)
                label_short = invoice_label
                label_long = f"{invoice_label} - paid with: {card_type} - {order_code}"
            partner = invoice.get("partner_id")
            if partner and isinstance(partner, list) and partner:
                partner_id = partner[0]
                if debug_lookup:
                    partner_name = partner[1] if len(partner) > 1 else ""
                    print(
                        f"[invoice_lookup] partner id={partner_id} name={partner_name}"
                    )
            inv_line_ids = invoice.get("invoice_line_ids") or []
            analytic_distribution = get_first_line_analytic_distribution(
                client, inv_line_ids
            )
            if debug_lookup:
                print(f"[invoice_lookup] analytic_distribution={analytic_distribution}")
        else:
            stats.invoice_not_found += 1

        base_account = cfg.account_vivawallet
        ctype = (card_type or "").strip().lower()
        if ctype in {"amex", "american express"}:
            base_account = card_to_gl(
                card_type, cfg.card_gl_map, cfg.account_vivawallet
            )

        if invoice and partner_id:
            gl_58x = cfg.account_clients
        else:
            gl_58x = cfg.account_suspense

        def dc(amount_val: float) -> Tuple[float, float]:
            if amount_val >= 0:
                return float(amount_val), 0.0
            return 0.0, float(-amount_val)

        debit_550, credit_550 = dc(base_550)
        debit_fee, credit_fee = dc(fees)
        debit_58, credit_58 = dc(amount)
        debit_58, credit_58 = credit_58, debit_58

        line_550 = {
            "account_id": _resolve_account(base_account),
            "name": label_long,
            "debit": round(debit_550, 2),
            "credit": round(credit_550, 2),
        }

        line_58x = {
            "account_id": _resolve_account(gl_58x),
            "name": label_short,
            "debit": round(debit_58, 2),
            "credit": round(credit_58, 2),
        }

        line_fee = {
            "account_id": _resolve_account(cfg.account_costs),
            "name": label_short,
            "debit": round(debit_fee, 2),
            "credit": round(credit_fee, 2),
        }

        if invoice and partner_id:
            line_550["partner_id"] = partner_id
            line_58x["partner_id"] = partner_id
            line_fee["partner_id"] = partner_id

        if invoice and analytic_distribution:
            line_58x["analytic_distribution"] = analytic_distribution
            line_fee["analytic_distribution"] = analytic_distribution

        tid_str = str(transaction_id).strip() if has_tid else ""

        payload = {
            "move_type": "entry",
            "company_id": cfg.company_id,
            "date": booking_date_str,
            "ref": move_ref,
            "line_ids": [
                (0, 0, line_550),
                (0, 0, line_58x),
                (0, 0, line_fee),
            ],
        }
        if tid_str:
            payload["narration"] = tid_str
        if cfg.journal_id is not None:
            payload["journal_id"] = cfg.journal_id

        meta = {
            "merchant_ref": merchant_ref,
            "order_code": order_code,
            "transaction_id": tid_str,
            "clearance_date_compact": clearance_date_compact,
            "invoice_id": invoice.get("id") if invoice else None,
            "partner_id": partner_id if partner_id else None,
            "amount_residual": (
                round(float(invoice.get("amount_residual") or 0.0), 2)
                if invoice
                else None
            ),
            "counterpart_amount": round(amount, 2),
            "account_clients_id": (
                _resolve_account(cfg.account_clients)
                if invoice and partner_id
                else None
            ),
            # Rauwe rijdata voor skip-/foutrapportage
            "raw": {
                "Clearance Date": booking_date_str,
                "Merchant Reference": merchant_ref,
                "Order Code": order_code,
                "Card Type": card_type,
                "Amount": amount,
                "Fees": round(fees, 2),
                "Transaction ID": (
                    str(transaction_id).strip() if is_filled(transaction_id) else ""
                ),
                "Source Description": source_desc,
                "Customer Description": customer_desc,
            },
        }

        moves.append((payload, meta))
        stats.moves_prepared += 1

    return moves, stats
