"""Business-logica voor Self-billing Move: lijst supplier-drafts en verplaats+annuleer ze.

Poort van
`travel-experts-backend/apps/main/app/sbmov/service.py`.

Verschillen t.o.v. de bron (transport/entrypoint-wijzigingen, zie plan-
verschil #2/#3):
- `_get_odoo_client()` → `odoo_conn.get_client()` (pakket, JSON-2).
- `resolve_config` → `config_resolve.resolve_config` (raw SQL i.p.v.
  Flask-SQLAlchemy; fase-3-consolidatie van de fase-2-interim
  `shared/config_store.py`, zie `config_resolve.py`).
- de foutklasse van de legacy XML-RPC-module → `odoo.exceptions.OdooFault`
  (pakket-equivalent, zie de opdracht en `odoo/exceptions.py`). Lazy import
  (zelfde rationale als `odoo_conn.get_client()`).
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

import odoo_conn
from config_resolve import resolve_config

logger = logging.getLogger(__name__)

SCRIPT_NAME = "sbmov"
REQUIRED_CONFIG_KEYS = ["sbmov_search_journals", "sbmov_moveto_journal"]


def _load_config(company_id: int) -> Dict[str, Any]:
    """Laad + valideer de twee verplichte sbmov-configsleutels."""
    raw: Dict[str, Any] = {}
    for key in REQUIRED_CONFIG_KEYS:
        val = resolve_config(company_id, SCRIPT_NAME, key)
        if val is None:
            raise ValueError(f"Missing required config key: {SCRIPT_NAME}.{key}")
        raw[key] = val

    search = raw["sbmov_search_journals"]
    if not isinstance(search, list):
        raise ValueError(
            f"{SCRIPT_NAME}.sbmov_search_journals must be a list of journal IDs"
        )
    try:
        search_ids = [int(j) for j in search]
    except (TypeError, ValueError):
        raise ValueError(
            f"{SCRIPT_NAME}.sbmov_search_journals must contain integer IDs"
        )

    try:
        moveto = int(raw["sbmov_moveto_journal"])
    except (TypeError, ValueError):
        raise ValueError(f"{SCRIPT_NAME}.sbmov_moveto_journal must be an integer ID")

    return {"search_journals": search_ids, "moveto_journal": moveto}


def _effective_search_journals(cfg: Dict[str, Any]) -> List[int]:
    """Sluit de moveto-journal uit van de zoeklijst (de-dup-veiligheid)."""
    moveto = cfg["moveto_journal"]
    seen = set()
    out: List[int] = []
    for j in cfg["search_journals"]:
        if j == moveto or j in seen:
            continue
        seen.add(j)
        out.append(j)
    return out


def list_suppliers(company_id: int) -> Dict[str, Any]:
    """Lijst unieke suppliers met draft self-billing invoices."""
    cfg = _load_config(company_id)
    client = odoo_conn.get_client()
    effective = _effective_search_journals(cfg)
    moveto_id = cfg["moveto_journal"]

    # Resolve journal-weergavenamen voor de UI (search + moveto)
    lookup_ids = list({*effective, moveto_id})
    journals = odoo_conn.search_read(
        client,
        "account.journal",
        [("id", "in", lookup_ids)],
        ["id", "name"],
    )
    journal_name_by_id = {j["id"]: j["name"] for j in journals}

    moveto_block = {
        "id": moveto_id,
        "name": journal_name_by_id.get(moveto_id, ""),
    }
    search_block = [
        {"id": jid, "name": journal_name_by_id.get(jid, "")} for jid in effective
    ]

    if not effective:
        logger.info(
            "sbmov: listing suppliers for company %s, no effective search "
            "journals, found 0 partners",
            company_id,
        )
        return {
            "moveto_journal": moveto_block,
            "search_journals": search_block,
            "suppliers": [],
        }

    invoices = odoo_conn.search_read(
        client,
        "account.move",
        [
            ("journal_id", "in", effective),
            ("move_type", "in", ["out_invoice", "out_refund"]),
            ("state", "=", "draft"),
            ("company_id", "=", company_id),
        ],
        ["id", "partner_id", "journal_id"],
    )

    # Groepeer op partner-id (null-partners emmer onder sentinel "__null__")
    buckets: Dict[Any, Dict[str, Any]] = {}
    for inv in invoices:
        partner = inv.get("partner_id")
        if partner:
            pid: Optional[int] = partner[0]
            pname = partner[1]
        else:
            pid = None
            pname = "(No partner)"
        key = pid if pid is not None else "__null__"
        bucket = buckets.get(key)
        if bucket is None:
            bucket = {
                "partner_id": pid,
                "partner_name": pname,
                "invoice_count": 0,
                "journal_ids": set(),
            }
            buckets[key] = bucket
        bucket["invoice_count"] += 1
        journal = inv.get("journal_id")
        if journal:
            bucket["journal_ids"].add(journal[0])

    suppliers = [
        {
            "partner_id": b["partner_id"],
            "partner_name": b["partner_name"],
            "invoice_count": b["invoice_count"],
            "journal_ids": sorted(b["journal_ids"]),
        }
        for b in buckets.values()
    ]

    logger.info(
        "sbmov: listing suppliers for company %s, found %d partners",
        company_id,
        len(suppliers),
    )

    return {
        "moveto_journal": moveto_block,
        "search_journals": search_block,
        "suppliers": suppliers,
    }


def move_partner_drafts(company_id: int, partner_id: Optional[int]) -> Dict[str, Any]:
    """Verplaats alle draft self-billing invoices voor één partner naar de
    doeljournal en annuleer ze. Per-invoice-isolatie zodat één mislukking de
    batch niet afbreekt.
    """
    from odoo.exceptions import OdooFault  # lazy: alleen nodig bij echte Odoo-calls

    cfg = _load_config(company_id)
    effective = _effective_search_journals(cfg)
    moveto = cfg["moveto_journal"]

    empty_result = {"requested": 0, "moved": 0, "cancelled": 0, "errors": []}
    if not effective:
        return empty_result

    client = odoo_conn.get_client()

    domain: List[Any] = [
        ("journal_id", "in", effective),
        ("move_type", "in", ["out_invoice", "out_refund"]),
        ("state", "=", "draft"),
        ("company_id", "=", company_id),
    ]
    if partner_id is None:
        domain.append(("partner_id", "=", False))
    else:
        domain.append(("partner_id", "=", partner_id))

    invoices = odoo_conn.search_read(client, "account.move", domain, ["id"])
    invoice_ids = [inv["id"] for inv in invoices]
    requested = len(invoice_ids)
    if not invoice_ids:
        return empty_result

    logger.info(
        "sbmov: moving %d drafts for partner %s to journal %s",
        requested,
        partner_id,
        moveto,
    )

    moved = 0
    cancelled = 0
    errors: List[Dict[str, Any]] = []

    for invoice_id in invoice_ids:
        try:
            odoo_conn.write(client, "account.move", [invoice_id], {"journal_id": moveto})
            moved += 1
        except Exception as e:
            logger.error("sbmov: failed to move invoice %s: %s", invoice_id, e)
            errors.append({"invoice_id": invoice_id, "error": str(e)})
            continue
        try:
            odoo_conn.call(client, "account.move", "button_cancel", [[invoice_id]])
            cancelled += 1
        except OdooFault as e:
            # Odoo's button_cancel retourneert None; sommige backends kunnen
            # dit als fout markeren terwijl de cancel al gecommit is.
            if "cannot marshal None" in str(e):
                cancelled += 1
            else:
                logger.error("sbmov: failed to cancel invoice %s: %s", invoice_id, e)
                errors.append({"invoice_id": invoice_id, "error": str(e)})
        except Exception as e:
            logger.error("sbmov: failed to cancel invoice %s: %s", invoice_id, e)
            errors.append({"invoice_id": invoice_id, "error": str(e)})

    return {
        "requested": requested,
        "moved": moved,
        "cancelled": cancelled,
        "errors": errors,
    }
