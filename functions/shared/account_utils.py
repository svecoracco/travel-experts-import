"""Account/tax/analytic-account lookups — poort van
`travel-experts-backend/apps/main/app/shared/account_utils.py`.

Odoo-toegang herschreven van de oude generieke `OdooClient`-aanroepmethode
(handgeschreven op de legacy XML-RPC-module) naar de generieke
`odoo_conn`-helpers (pakket-transport, JSON-2).
Gedrag, caching en foutvorm zijn 1-op-1 behouden — zie de fase-2-opdracht
("Behoud resolve_account_id, resolve_tax_id ... exact").

`client` hier is altijd een duck-typed Odoo-client: de echte
`odoo.OdooClient` (via `odoo_conn.get_client()`) in productie, of een
fake/mock in tests en de payload-parity-harness. Deze module importeert
`env`/`odoo` zelf nooit — puur transform-/lookup-logica.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

import odoo_conn

# Module-level flags zodat de fields_get-RPC maar één keer per proces gebeurt
_company_field_checked: Optional[bool] = None
_analytic_company_field_checked: Optional[bool] = None


def resolve_account_id(
    client: Any,
    code: int,
    company_id: int,
    cache: Dict[int, int],
    debug: bool = False,
) -> int:
    """Zoek een Odoo account.account op via code en company_id.

    Cachet resultaten in het meegegeven dict. Houdt rekening met
    Odoo-versieverschillen in het company_id-veld (op Odoo 19+ is dit
    `company_ids`, niet `company_id` — vandaar de dynamische fields_get-check
    in plaats van het gebruik van de pakket-`find_by_code`-conventiemethode,
    die company_id ongeconditioneerd in de domain zet).
    """
    if code in cache:
        return cache[code]

    global _company_field_checked
    if _company_field_checked is None:
        try:
            fields = odoo_conn.call(
                client,
                "account.account",
                "fields_get",
                [["company_id"]],
                {"attributes": ["type"]},
            )
            _company_field_checked = "company_id" in fields
        except Exception:
            _company_field_checked = False

    domain = [("code", "=", str(code))]
    if _company_field_checked:
        domain.append(("company_id", "=", company_id))
    ctx = {"allowed_company_ids": [company_id], "company_id": company_id}
    res = odoo_conn.search_read(
        client,
        "account.account",
        domain,
        ["id", "code", "name"],
        limit=1,
        context=ctx,
    )
    if not res:
        raise RuntimeError(f"Account not found for code={code} company_id={company_id}")
    acc_id = res[0]["id"]
    if debug:
        logging.info(
            "[account_lookup] code=%s id=%s name=%s", code, acc_id, res[0].get("name")
        )
    cache[code] = acc_id
    return acc_id


def resolve_tax_id(
    client: Any,
    vatcode: str,
    company_id: int,
    cache: Dict[str, int],
) -> int:
    """Zoek een Odoo account.tax op via naam, company_id en purchase-type."""
    if vatcode in cache:
        return cache[vatcode]
    domain = [
        ("name", "=", vatcode),
        ("company_id", "=", company_id),
        ("type_tax_use", "=", "purchase"),
    ]
    res = odoo_conn.search_read(
        client,
        "account.tax",
        domain,
        ["id", "name"],
        limit=1,
    )
    if not res:
        raise RuntimeError(
            f"Tax not found for name={vatcode!r} company_id={company_id}"
        )
    tax_id = res[0]["id"]
    logging.info("[tax_lookup] vatcode=%s id=%s", vatcode, tax_id)
    cache[vatcode] = tax_id
    return tax_id


def build_analytic_account_map(
    client: Any,
    file_numbers: List[str],
    company_id: int,
    chunk_size: int = 500,
    debug: bool = False,
) -> Dict[str, int]:
    """Map bestandsnummer-namen naar Odoo analytic-account-ID's (bulk-lookup).

    Retourneert {file_number_str: analytic_account_id}.
    """
    if not file_numbers:
        return {}

    global _analytic_company_field_checked
    if _analytic_company_field_checked is None:
        try:
            fields = odoo_conn.call(
                client,
                "account.analytic.account",
                "fields_get",
                [["company_id"]],
                {"attributes": ["type"]},
            )
            _analytic_company_field_checked = "company_id" in fields
        except Exception:
            _analytic_company_field_checked = False

    ctx = {"allowed_company_ids": [company_id], "company_id": company_id}
    results: Dict[str, int] = {}
    for i in range(0, len(file_numbers), chunk_size):
        chunk = file_numbers[i : i + chunk_size]
        domain: list = [("name", "in", chunk)]
        if _analytic_company_field_checked:
            domain.append(("company_id", "=", company_id))
        res = odoo_conn.search_read(
            client,
            "account.analytic.account",
            domain,
            ["id", "name"],
            context=ctx,
        )
        for row in res:
            name = str(row.get("name") or "").strip()
            if name:
                results[name] = int(row["id"])

    if debug:
        logging.info(
            "[analytic_lookup] file_numbers=%s matched=%s",
            len(file_numbers),
            len(results),
        )
    return results


def create_analytic_accounts(
    client: Any,
    file_numbers: List[str],
    company_id: int,
    plan_name: str = "File number",
) -> Dict[str, int]:
    """Maak ontbrekende `account.analytic.account`-records aan in Odoo.

    Retourneert `{file_number: new_analytic_id}` voor elk aangemaakt record.
    Werpt `RuntimeError` als het analytic plan niet gevonden wordt.
    """
    if not file_numbers:
        return {}

    # Resolve het analytic-plan-ID één keer
    plan_rows = odoo_conn.search_read(
        client,
        "account.analytic.plan",
        [("name", "=", plan_name)],
        ["id", "name"],
        limit=1,
    )
    if not plan_rows:
        raise RuntimeError(
            f"Analytic plan '{plan_name}' not found in Odoo. "
            "Check the bsp_analytic_plan config key."
        )
    plan_id = plan_rows[0]["id"]
    logging.info("[analytic_create] plan '%s' id=%s", plan_name, plan_id)

    created: Dict[str, int] = {}
    for fn in file_numbers:
        vals: Dict[str, Any] = {"name": fn, "plan_id": plan_id}
        # company_id meenemen tenzij het veld expliciet afwezig bevestigd is
        if _analytic_company_field_checked is not False:
            vals["company_id"] = company_id
        new_id = odoo_conn.create(client, "account.analytic.account", vals)
        created[fn] = int(new_id)
        logging.info("[analytic_create] created '%s' id=%s", fn, new_id)

    return created
