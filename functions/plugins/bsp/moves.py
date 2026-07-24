"""BSP move-payload builders — poort van
`travel-experts-backend/apps/main/app/plugins/bsp/moves.py`.

Odoo-toegang loopt uitsluitend via `shared.account_utils.resolve_account_id`/
`resolve_tax_id` (zelf al herschreven op `odoo_conn`); de payload-builders
hier zijn zuivere Python en ongewijzigd t.o.v. de bron.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from plugins.bsp.parser import ParsedLine
from shared.account_utils import resolve_account_id, resolve_tax_id


@dataclass(frozen=True)
class BspConfig:
    partner_id: int
    currency_id: int
    journal_id: int
    misc_journal_id: int
    line_account_code: int
    line_tax_ids: List[int]
    misc_clearing_code: int
    cash_account_code: int
    default_card_gl: Optional[int]
    card_suffix_map: Dict[str, int]
    bts_table: Optional[str]
    bts_ticket_col: Optional[str]
    bsp_vatcode: Optional[str]


def normalize_tax_ids(raw: Any) -> List[int]:
    if raw is None:
        return []
    if isinstance(raw, list):
        return [int(x) for x in raw if str(x).strip() != ""]
    if isinstance(raw, str):
        s = raw.strip()
        if not s:
            return []
        return [int(x.strip()) for x in s.split(",") if x.strip()]
    return [int(raw)]


def req_int(cfg: Dict[str, Any], key: str, company_id: int) -> int:
    val = cfg.get(key)
    if val is None or str(val).strip() == "":
        raise ValueError(
            f"Missing required config key: odoo.companies.{company_id}.scripts.bsp.{key}"
        )
    return int(val)


def build_bsp_config(cfg: Dict[str, Any], company_id: int) -> BspConfig:
    line_tax_ids = normalize_tax_ids(cfg.get("line_tax_ids"))
    card_suffix_map_raw = cfg.get("card_suffix_map") or {}
    card_suffix_map = {str(k): int(v) for k, v in card_suffix_map_raw.items()}
    default_card_gl = cfg.get("default_card_gl")
    if default_card_gl is not None and str(default_card_gl).strip() != "":
        default_card_gl = int(default_card_gl)
    else:
        default_card_gl = None

    return BspConfig(
        partner_id=req_int(cfg, "partner_id", company_id),
        currency_id=req_int(cfg, "currency_id", company_id),
        journal_id=req_int(cfg, "journal_id", company_id),
        misc_journal_id=req_int(cfg, "misc_journal_id", company_id),
        line_account_code=req_int(cfg, "line_account_id", company_id),
        line_tax_ids=line_tax_ids,
        misc_clearing_code=req_int(cfg, "misc_clearing_account_id", company_id),
        cash_account_code=req_int(cfg, "cash_account_id", company_id),
        default_card_gl=default_card_gl,
        card_suffix_map=card_suffix_map,
        bts_table=cfg.get("bts_table"),
        bts_ticket_col=cfg.get("bts_ticket_col"),
        bsp_vatcode=cfg.get("bsp_vatcode"),
    )


def build_ref(
    ticket10: str,
    card_ref_short: Optional[str],
    input_stem: str,
    file_number: Optional[str] = None,
) -> str:
    fn_part = file_number if file_number else "???"
    if card_ref_short:
        return f"BSP-{ticket10} - CARD: {card_ref_short} - {input_stem} - FN: {fn_part}"
    else:
        return f"BSP-{ticket10} - CASH - {input_stem} - FN: {fn_part}"


def card_gl_from_payment(
    payment_ref: Optional[str],
    card_prefix4: Optional[str],
    bsp_cfg: BspConfig,
) -> Optional[int]:
    if not payment_ref:
        return bsp_cfg.cash_account_code
    if card_prefix4:
        mapped = bsp_cfg.card_suffix_map.get(str(card_prefix4))
        if mapped is not None:
            return mapped
    if bsp_cfg.default_card_gl is not None:
        return bsp_cfg.default_card_gl
    return None


def build_moves_for_line(
    line: ParsedLine,
    ticket10: str,
    ref: str,
    bsp_cfg: BspConfig,
    company_id: int,
    currency_id: int,
    account_id_cache: Dict[int, int],
    client: Any,
    analytic_distribution: Optional[Dict[str, float]] = None,
    debug: bool = False,
    tax_id_cache: Optional[Dict[str, int]] = None,
) -> List[Dict[str, Any]]:
    """Bouw Odoo-move-payloads voor één BSP-lijn.

    Voor kaartbetalingen: retourneert [invoice_payload, misc_payload]
    Voor cash-betalingen: retourneert [invoice_payload] (misc apart gebouwd via consolidatie)
    Voor split-betalingen: retourneert [invoice_payload, card_misc_payload]
        (het cash-deel wordt apart verzameld voor consolidatie)
    """
    total = float(line.total_amount)
    abs_total = abs(total)

    # --- Bouw invoice-payload (zelfde voor alle betaalmethodes) ---
    inv_line = {
        "account_id": resolve_account_id(
            client, bsp_cfg.line_account_code, company_id, account_id_cache, debug=debug
        ),
        "name": ref,
        "quantity": 1,
        "price_unit": round(abs_total, 2),
    }
    if analytic_distribution:
        inv_line["analytic_distribution"] = dict(analytic_distribution)
    if bsp_cfg.bsp_vatcode:
        if tax_id_cache is None:
            tax_id_cache = {}
        tax_id = resolve_tax_id(client, bsp_cfg.bsp_vatcode, company_id, tax_id_cache)
        inv_line["tax_ids"] = [(6, 0, [tax_id])]
    elif bsp_cfg.line_tax_ids:
        inv_line["tax_ids"] = [(6, 0, bsp_cfg.line_tax_ids)]

    move_type = "in_refund" if total < 0 else "in_invoice"
    invoice_payload = {
        "move_type": move_type,
        "company_id": company_id,
        "invoice_date": line.issue_date,
        "partner_id": bsp_cfg.partner_id,
        "currency_id": currency_id,
        "journal_id": bsp_cfg.journal_id,
        "ref": ref,
        "invoice_line_ids": [(0, 0, inv_line)],
    }

    # --- Cash-betaling: retourneer alleen invoice (misc wordt geconsolideerd) ---
    if line.payment_method == "cash":
        return [invoice_payload]

    # --- Card- of split-betaling: bouw individuele misc-entry ---
    card_amount = (
        float(line.card_amount) if line.card_amount is not None else float(total)
    )

    if card_amount >= 0:
        card_debit, card_credit = 0.0, card_amount
        clear_debit, clear_credit = card_amount, 0.0
    else:
        card_debit, card_credit = abs(card_amount), 0.0
        clear_debit, clear_credit = 0.0, abs(card_amount)

    card_balance = round(card_debit - card_credit, 2)
    clear_balance = round(clear_debit - clear_credit, 2)

    card_gl_code = card_gl_from_payment(line.payment_ref, line.card_prefix4, bsp_cfg)
    if card_gl_code is None:
        raise RuntimeError(f"Unknown card mapping for payment_ref={line.payment_ref}")

    card_line = {
        "account_id": resolve_account_id(
            client, int(card_gl_code), company_id, account_id_cache, debug=debug
        ),
        "name": ref,
        "debit": round(card_debit, 2),
        "credit": round(card_credit, 2),
        "amount_currency": float(card_balance),
        "currency_id": currency_id,
    }
    clear_line = {
        "account_id": resolve_account_id(
            client,
            bsp_cfg.misc_clearing_code,
            company_id,
            account_id_cache,
            debug=debug,
        ),
        "name": ref,
        "debit": round(clear_debit, 2),
        "credit": round(clear_credit, 2),
        "amount_currency": float(clear_balance),
        "currency_id": currency_id,
        "partner_id": bsp_cfg.partner_id,
    }

    misc_payload = {
        "move_type": "entry",
        "company_id": company_id,
        "date": line.issue_date,
        "journal_id": bsp_cfg.misc_journal_id,
        "ref": ref,
        "line_ids": [
            (0, 0, card_line),
            (0, 0, clear_line),
        ],
    }

    return [invoice_payload, misc_payload]


@dataclass
class CashClearingLine:
    """Data voor één clearing-lijn in de geconsolideerde cash-misc-entry."""

    ref: str
    amount: float  # positief = credit op clearing, negatief = debit


def build_consolidated_cash_misc(
    cash_lines: List[CashClearingLine],
    bsp_cfg: BspConfig,
    company_id: int,
    currency_id: int,
    account_id_cache: Dict[int, int],
    client: Any,
    entry_date: str,
    period: str,
    stem: str,
    debug: bool = False,
) -> Dict[str, Any]:
    """Bouw ÉÉN geconsolideerde misc-entry voor alle BSP Cash-transacties.

    Structuur:
      Debit:  580800 (BSP Cash)      TOTAAL    naam="BSP-CASH - {periode}"
      Credit: 440000 (Clearing)      bedrag1   naam=ref1
      Credit: 440000 (Clearing)      bedrag2   naam=ref2
      ...
    """
    clearing_account_id = resolve_account_id(
        client,
        bsp_cfg.misc_clearing_code,
        company_id,
        account_id_cache,
        debug=debug,
    )
    cash_account_id = resolve_account_id(
        client,
        bsp_cfg.cash_account_code,
        company_id,
        account_id_cache,
        debug=debug,
    )

    line_ids = []
    total_cash = 0.0

    for cl in cash_lines:
        amount = cl.amount
        total_cash += amount

        if amount >= 0:
            debit, credit = amount, 0.0
        else:
            debit, credit = 0.0, abs(amount)

        balance = round(debit - credit, 2)

        line_ids.append(
            (
                0,
                0,
                {
                    "account_id": clearing_account_id,
                    "name": cl.ref,
                    "debit": round(debit, 2),
                    "credit": round(credit, 2),
                    "amount_currency": float(balance),
                    "currency_id": currency_id,
                    "partner_id": bsp_cfg.partner_id,
                },
            )
        )

    # BSP Cash-totaallijn (tegenovergestelde zijde)
    total_cash = round(total_cash, 2)
    consolidated_ref = f"BSP-CASH - {period} - {stem}"

    if total_cash >= 0:
        cash_debit, cash_credit = 0.0, total_cash
    else:
        cash_debit, cash_credit = abs(total_cash), 0.0

    cash_balance = round(cash_debit - cash_credit, 2)

    line_ids.append(
        (
            0,
            0,
            {
                "account_id": cash_account_id,
                "name": consolidated_ref,
                "debit": round(cash_debit, 2),
                "credit": round(cash_credit, 2),
                "amount_currency": float(cash_balance),
                "currency_id": currency_id,
            },
        )
    )

    return {
        "move_type": "entry",
        "company_id": company_id,
        "date": entry_date,
        "journal_id": bsp_cfg.misc_journal_id,
        "ref": consolidated_ref,
        "line_ids": line_ids,
    }
