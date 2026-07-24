"""Rail transform — poort van
`travel-experts-backend/apps/main/app/plugins/rail/transform.py`.

Odoo-toegang (`lookup_rail_partner`) herschreven naar `odoo_conn`.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

import odoo_conn

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RailConfig:
    """Runtime-config voor de Rail-plugin, opgelost uit app_config + env."""

    company_id: int
    partner_id: int  # opgelost via supplier_ref-lookup
    purchase_journal_id: int  # journal voor de vendor bill
    line_account_id: int  # GL-account-CODE (bv. 604000)
    tax_id: Optional[int]  # Odoo-tax-record-ID (direct); None = geen belasting
    bts_table: Optional[str]  # SQL Server-tabel voor ticket→file-number-lookup
    bts_ticket_col: Optional[
        str
    ]  # Kolom in bts_table met ticketnummer (voor tiebreaker)
    bts_dnr_col: Optional[str]  # Kolom in bts_table met DNR-waarde (primaire lookup)


def _req_str(cfg: Dict[str, Any], key: str, company_id: int) -> str:
    val = cfg.get(key)
    if val is None or str(val).strip() == "":
        raise ValueError(
            f"Missing required Rail config key '{key}' for company_id={company_id}. "
            "Set it via Settings → Config."
        )
    return str(val).strip()


def _req_int(cfg: Dict[str, Any], key: str, company_id: int) -> int:
    val = cfg.get(key)
    if val is None or str(val).strip() == "":
        raise ValueError(
            f"Missing required Rail config key '{key}' for company_id={company_id}. "
            "Set it via Settings → Config."
        )
    return int(val)


def _opt_int(cfg: Dict[str, Any], key: str) -> Optional[int]:
    val = cfg.get(key)
    if val is None or str(val).strip() == "":
        return None
    try:
        return int(val)
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------------------
# Partner-lookup
# ---------------------------------------------------------------------------


def lookup_rail_partner(client: Any, supplier_ref: str, company_id: int) -> int:
    """Zoek partner_id via het ref-veld van de partner."""
    rows = odoo_conn.search_read(
        client,
        "res.partner",
        [("ref", "=", supplier_ref)],
        ["id", "ref", "name"],
        limit=1,
        context={"allowed_company_ids": [company_id]},
    )
    if not rows:
        raise RuntimeError(
            f"Rail supplier not found for ref='{supplier_ref}' (company_id={company_id}). "
            "Check the rail supplier_ref config key."
        )
    partner_id = int(rows[0]["id"])
    logger.info(
        "[rail] Partner resolved: ref='%s' → id=%d name='%s'",
        supplier_ref,
        partner_id,
        rows[0].get("name", ""),
    )
    return partner_id


def build_rail_config(
    cfg: Dict[str, Any], company_id: int, odoo_client: Any
) -> RailConfig:
    supplier_ref = _req_str(cfg, "supplier_ref", company_id)
    partner_id = lookup_rail_partner(odoo_client, supplier_ref, company_id)
    return RailConfig(
        company_id=company_id,
        partner_id=partner_id,
        purchase_journal_id=_req_int(cfg, "purchase_journal_id", company_id),
        line_account_id=_req_int(cfg, "line_account_id", company_id),
        tax_id=_opt_int(cfg, "tax_id"),
        bts_table=cfg.get("bts_table") or None,
        bts_ticket_col=cfg.get("bts_ticket_col") or None,
        bts_dnr_col=cfg.get("bts_dnr_col") or None,
    )


# ---------------------------------------------------------------------------
# Invoice-payload-builder
# ---------------------------------------------------------------------------


def build_invoice_payload(
    cfg: RailConfig,
    gl_account_id: int,
    invoice_date: str,
    accounting_date: Optional[str],
    ref: str,
    doc_number: str,
    rows: List[Dict[str, Any]],
    ticket_to_analytic: Dict[str, int],
    company_id: int,
    narration: Optional[str],
) -> Dict[str, Any]:
    """Bouw één vendor bill (altijd in_invoice) voor alle ticket-rijen.

    Structuur:
      - move_type: altijd in_invoice (totaal wordt op nul gezet door de tegenlijn)
      - payment_reference: OFFICIAL_DOC_NUMBER (idempotentiesleutel + AP-lijn-label)
      - ref: bill-referentie (uit de originele entry indien opgegeven, anders OFFICIAL_DOC_NUMBER)
      - invoice_line_ids:
          - één kostenlijn per ticket-rij (signed NET_AMOUNT, met analytic-verdeling)
              label = "Ticket: {TICKET_NBR} - DNR: {DNR_ID} - PRICE: {TRANSACTION_PRICE} EUR"
          - één tegenlijn op dezelfde GL-account (price_unit = -som van ticketlijnen)
              label = OFFICIAL_DOC_NUMBER; draagt belasting indien geconfigureerd; geen analytic
      - narration: HTML-string die linkt naar de originele entry (indien opgegeven)
    """
    move_type = "in_invoice"

    invoice_lines = []
    line_total = 0.0
    for row in rows:
        ticket_nbr_raw = str(row.get("TICKET_NBR", "") or "").strip()
        dnr_raw = str(row.get("DNR_ID", "") or "").strip()
        # Behoud oorspronkelijke stringweergave voor het label (bv. "43,00")
        price_raw = str(row.get("TRANSACTION_PRICE", "") or "").strip()
        net_amount = float(row.get("_net_amount", 0.0))

        label = f"Ticket: {ticket_nbr_raw} - DNR: {dnr_raw} - PRICE: {price_raw} EUR"

        price_unit = round(net_amount, 2)
        line_total += price_unit

        inv_line: Dict[str, Any] = {
            "account_id": gl_account_id,
            "name": label,
            "quantity": 1,
            "price_unit": price_unit,
        }

        # Analytic-verdeling: zoek via DNR_ID (primaire SQL Server-lookupsleutel)
        dnr_key = str(row.get("DNR_ID", "") or "").strip()
        analytic_id = ticket_to_analytic.get(dnr_key)
        if analytic_id:
            inv_line["analytic_distribution"] = {str(analytic_id): 100.0}

        if cfg.tax_id is not None:
            inv_line["tax_ids"] = [(6, 0, [cfg.tax_id])]

        invoice_lines.append((0, 0, inv_line))

    # Tegenlijn: zet het invoice-totaal op nul op dezelfde GL-account
    counter_line: Dict[str, Any] = {
        "account_id": gl_account_id,
        "name": doc_number,
        "quantity": 1,
        "price_unit": round(-line_total, 2),
    }
    if cfg.tax_id is not None:
        counter_line["tax_ids"] = [(6, 0, [cfg.tax_id])]
    invoice_lines.append((0, 0, counter_line))

    payload: Dict[str, Any] = {
        "move_type": move_type,
        "company_id": company_id,
        "invoice_date": invoice_date,
        "partner_id": cfg.partner_id,
        "journal_id": cfg.purchase_journal_id,
        "ref": ref,
        "payment_reference": doc_number,
        "invoice_line_ids": invoice_lines,
    }

    if accounting_date:
        payload["date"] = accounting_date

    if narration:
        payload["narration"] = narration

    return payload
