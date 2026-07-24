"""Commission plugin — config-resolutie en invoice-payload-builder.

Poort van
`travel-experts-backend/apps/main/app/plugins/commission/transform.py`.
Odoo-toegang (`lookup_commission_partner`) herschreven naar `odoo_conn`.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

import odoo_conn
from plugins.commission.excel_reader import CommissionHeaderData, CommissionRow

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CommissionConfig:
    """Runtime-config voor de Commission-plugin, opgelost uit app_config + env."""

    company_id: int
    partner_id: int  # opgelost via supplier_ref
    purchase_journal_id: int  # uit config
    line_account_id: int  # GL-accountcode (bv. 613500) — opgelost naar Odoo-ID
    tax_id: Optional[int]  # Odoo-tax-record-ID uit config
    analytic_plan: str  # default "File number"
    accounting_date: Optional[str]
    original_entry_ref: Optional[str]


def _req_str(cfg: Dict[str, Any], key: str, company_id: int) -> str:
    val = cfg.get(key)
    if val is None or str(val).strip() == "":
        raise ValueError(
            f"Missing required Commission config key '{key}' for company_id={company_id}. "
            "Set it via Settings → Config."
        )
    return str(val).strip()


def _req_int(cfg: Dict[str, Any], key: str, company_id: int) -> int:
    val = cfg.get(key)
    if val is None or str(val).strip() == "":
        raise ValueError(
            f"Missing required Commission config key '{key}' for company_id={company_id}. "
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


def lookup_commission_partner(
    client: Any, supplier_ref: str, company_id: int
) -> int:
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
            f"Commission supplier not found for ref='{supplier_ref}' "
            f"(company_id={company_id}). Check the commission supplier_ref config key."
        )
    partner_id = int(rows[0]["id"])
    logger.info(
        "[commission] Partner resolved: ref='%s' → id=%d name='%s'",
        supplier_ref,
        partner_id,
        rows[0].get("name", ""),
    )
    return partner_id


def build_commission_config(
    cfg: Dict[str, Any], company_id: int, odoo_client: Any
) -> CommissionConfig:
    """Bouw een CommissionConfig uit het app-config-dict."""
    supplier_ref = _req_str(cfg, "supplier_ref", company_id)
    partner_id = lookup_commission_partner(odoo_client, supplier_ref, company_id)
    return CommissionConfig(
        company_id=company_id,
        partner_id=partner_id,
        purchase_journal_id=_req_int(cfg, "purchase_journal_id", company_id),
        line_account_id=_req_int(cfg, "line_account_id", company_id),
        tax_id=_opt_int(cfg, "tax_id"),
        analytic_plan=str(cfg.get("commission_analytic_plan") or "File number").strip(),
        accounting_date=cfg.get("accounting_date") or None,
        original_entry_ref=cfg.get("original_entry_ref") or None,
    )


# ---------------------------------------------------------------------------
# Invoice-payload-builder
# ---------------------------------------------------------------------------


def build_invoice_payload(
    config: CommissionConfig,
    gl_account_id: int,
    header: CommissionHeaderData,
    rows: List[CommissionRow],
    analytic_map: Dict[str, int],
    company_id: int,
    file_ref: str,
    invoice_date_override: Optional[str] = None,
    accounting_date_override: Optional[str] = None,
    ref_override: Optional[str] = None,
    narration_override: Optional[str] = None,
    partner_id_override: Optional[int] = None,
) -> Dict[str, Any]:
    """Bouw één vendor-bill-payload voor alle commission-rijen.

    Retourneert een dict klaar voor `odoo_conn.create(client, "account.move", payload)`.
    """
    # Bepaal netto-totaal om move_type te kiezen
    net_total = sum(r.amount for r in rows)
    move_type = "in_refund" if net_total < 0 else "in_invoice"

    # Bouw invoice-lijnen
    invoice_lines = []
    line_total = 0.0
    skip_report: List[Dict[str, Any]] = []
    needs_review: List[Dict[str, Any]] = []

    for row in rows:
        # Sla lege dossier over
        if not row.dossier:
            skip_report.append(
                {
                    "reason": "Empty dossier",
                    "client_name": row.client_name,
                    "amount": row.amount,
                }
            )
            continue

        # Sla nulbedrag over
        if row.amount == 0.0:
            skip_report.append(
                {
                    "reason": "Zero amount",
                    "dossier": row.dossier,
                    "client_name": row.client_name,
                }
            )
            continue

        label = f"File: {row.dossier} - Client: {row.client_name}"

        # in_invoice: houd signed bedrag (negatief = creditlijn binnen de bill)
        # in_refund: alle bedragen positief (Odoo handelt de omkering af)
        price_unit = round(
            abs(row.amount) if move_type == "in_refund" else row.amount, 2
        )

        inv_line: Dict[str, Any] = {
            "account_id": gl_account_id,
            "name": label,
            "quantity": 1,
            "price_unit": price_unit,
        }

        # Analytic-verdeling
        analytic_id = analytic_map.get(row.dossier)
        if analytic_id:
            inv_line["analytic_distribution"] = {str(analytic_id): 100.0}
        else:
            needs_review.append(
                {
                    "reason": "No analytic account found",
                    "dossier": row.dossier,
                    "client_name": row.client_name,
                    "amount": row.amount,
                }
            )

        # BTW
        if config.tax_id is not None:
            inv_line["tax_ids"] = [(6, 0, [config.tax_id])]

        invoice_lines.append((0, 0, inv_line))
        line_total += price_unit

    # Tegenlijn: zet het invoice-totaal op nul op dezelfde GL-account
    counter_line: Dict[str, Any] = {
        "account_id": gl_account_id,
        "name": file_ref,
        "quantity": 1,
        "price_unit": round(-line_total, 2),
    }
    if config.tax_id is not None:
        counter_line["tax_ids"] = [(6, 0, [config.tax_id])]
    invoice_lines.append((0, 0, counter_line))

    # Resolve data
    inv_date = invoice_date_override or (
        header.invoice_date.isoformat() if header.invoice_date else None
    )
    ref = ref_override or header.invoice_ref or file_ref

    payload: Dict[str, Any] = {
        "move_type": move_type,
        "company_id": company_id,
        "partner_id": partner_id_override or config.partner_id,
        "journal_id": config.purchase_journal_id,
        "ref": ref,
        "payment_reference": file_ref,
        "invoice_line_ids": invoice_lines,
    }

    if inv_date:
        payload["invoice_date"] = inv_date

    acc_date = accounting_date_override or config.accounting_date
    if acc_date:
        payload["date"] = acc_date
    elif inv_date:
        payload["date"] = inv_date

    narration = narration_override or header.narration
    if narration:
        payload["narration"] = narration

    return payload, skip_report, needs_review
