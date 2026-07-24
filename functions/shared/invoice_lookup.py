"""Invoice-lookup helpers — poort van
`travel-experts-backend/apps/main/app/shared/invoice_lookup.py`.

Odoo-toegang herschreven naar de generieke `odoo_conn`-helpers. Gedrag exact
behouden (zelfde domains, zelfde volgorde: name → payment_reference → ref).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional

import odoo_conn


def find_invoice_by_merchant_ref(
    client: Any,
    merchant_ref: str,
    company_id: int,
    invoice_year: Optional[int] = None,
    debug: bool = False,
) -> Optional[Dict[str, Any]]:
    """Probeer meerdere velden om de klantfactuur te vinden.

    Doorzoekt: name, payment_reference, ref. Gebruikt invoice_year om het
    referentie-prefix te bouwen (default = huidig jaar).
    """
    mref = str(merchant_ref).strip()
    year = invoice_year or datetime.now().year
    mref_i = f"I/{year}/{mref}"

    if not mref:
        return None

    domains = [
        [
            ("move_type", "=", "out_invoice"),
            ("company_id", "=", company_id),
            ("name", "=", mref_i),
        ],
        [
            ("move_type", "=", "out_invoice"),
            ("company_id", "=", company_id),
            ("payment_reference", "=", mref_i),
        ],
        [
            ("move_type", "=", "out_invoice"),
            ("company_id", "=", company_id),
            ("ref", "=", mref_i),
        ],
    ]

    fields = [
        "id",
        "partner_id",
        "invoice_line_ids",
        "name",
        "payment_reference",
        "ref",
        "amount_residual",
    ]

    for dom in domains:
        res = odoo_conn.search_read(client, "account.move", dom, fields, limit=1)
        if res:
            if debug:
                hit = res[0]
                print(
                    "[invoice_lookup] found "
                    f"id={hit.get('id')} name={hit.get('name')} "
                    f"payment_reference={hit.get('payment_reference')} ref={hit.get('ref')}"
                )
            return res[0]
    return None


def get_first_line_analytic_distribution(
    client: Any,
    invoice_line_ids: List[int],
) -> Optional[Dict[str, float]]:
    if not invoice_line_ids:
        return None
    line = odoo_conn.read(
        client, "account.move.line", [invoice_line_ids[0]], fields=["analytic_distribution"]
    )
    if not line:
        return None
    return line[0].get("analytic_distribution") or None
