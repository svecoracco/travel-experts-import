"""Divers transform — poort van
`travel-experts-backend/apps/main/app/plugins/divers/transform.py`.

Odoo-toegang (`lookup_partners`) herschreven naar `odoo_conn`.
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
class DiversConfig:
    """Runtime-config voor de Divers-plugin, opgelost uit app_config."""

    company_id: int
    purchase_journal_id: int  # journal voor purchase invoices


def _req_int(cfg: Dict[str, Any], key: str, company_id: int) -> int:
    val = cfg.get(key)
    if val is None or str(val).strip() == "":
        raise ValueError(
            f"Missing required Divers config key '{key}' "
            f"for company_id={company_id}. Set it via Settings → Config."
        )
    return int(val)


def build_divers_config(cfg: Dict[str, Any], company_id: int) -> DiversConfig:
    return DiversConfig(
        company_id=company_id,
        purchase_journal_id=_req_int(cfg, "divers_purchase_journal_id", company_id),
    )


# ---------------------------------------------------------------------------
# Bulk-partnerlookup (zelfde patroon als Airplus)
# ---------------------------------------------------------------------------


def lookup_partners(
    client: Any,
    codes: List[str],
    company_id: int,
) -> Dict[str, int]:
    """Bulk-lookup res.partner via het 'ref'-veld.

    Retourneert {supplier_code: partner_id}.
    """
    codes = [c for c in codes if c]
    if not codes:
        return {}

    result: Dict[str, int] = {}
    chunk_size = 200
    for i in range(0, len(codes), chunk_size):
        chunk = codes[i : i + chunk_size]
        rows = odoo_conn.search_read(
            client,
            "res.partner",
            [("ref", "in", chunk)],
            ["id", "ref"],
            context={"allowed_company_ids": [company_id]},
        )
        for row in rows:
            ref = str(row.get("ref") or "").strip()
            if ref:
                result[ref] = int(row["id"])

    logger.info(
        "[divers] Partner lookup: %d unique codes, %d matched",
        len(codes),
        len(result),
    )
    return result


# ---------------------------------------------------------------------------
# Invoice-payload-builder
# ---------------------------------------------------------------------------


def build_invoice_payload(
    cfg: DiversConfig,
    partner_id: int,
    invoice_date: str,
    accounting_date: Optional[str],
    ref: str,
    payment_reference: str,
    lines: List[Dict[str, Any]],
    move_type: str,
    currency_id: Optional[int] = None,
) -> Dict[str, Any]:
    """Bouw een purchase-invoice-payload voor een groep rijen.

    ``lines`` is een lijst van al-gebouwde invoice_line_ids-innerdicts.
    ``move_type`` is "in_invoice" of "in_refund".
    """
    payload: Dict[str, Any] = {
        "move_type": move_type,
        "company_id": cfg.company_id,
        "invoice_date": invoice_date,
        "date": accounting_date if accounting_date else invoice_date,
        "partner_id": partner_id,
        "journal_id": cfg.purchase_journal_id,
        "ref": ref,
        "payment_reference": payment_reference,
        "invoice_line_ids": [(0, 0, line) for line in lines],
    }
    if currency_id:
        payload["currency_id"] = currency_id

    return payload
