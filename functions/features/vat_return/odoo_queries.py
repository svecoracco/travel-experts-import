"""Odoo-specifieke queries voor VAT Return: tax-lookup, read_group, tag-resolutie.

Poort van
`travel-experts-backend/apps/main/app/vat_return/odoo_queries.py`.
Odoo-toegang herschreven naar `odoo_conn`. Let op: `fetch_belgium_country_id`
zoekt hard op landcode "BE" (Odoo-domeinlogica, geen client-naam) — dit is
onveranderd t.o.v. de bron.
"""

from __future__ import annotations

import logging
import re
from typing import Any, Dict, List, Tuple

import odoo_conn

logger = logging.getLogger(__name__)


def resolve_sale_tax_id(
    client: Any,
    vat_code_name: str,
    company_id: int,
) -> int:
    """Zoek een Odoo account.tax op via naam voor sale-type-belastingen."""
    tax = odoo_conn.search_read(
        client,
        "account.tax",
        [
            ("name", "=", vat_code_name),
            ("company_id", "=", company_id),
            ("type_tax_use", "=", "sale"),
        ],
        ["id"],
        limit=1,
    )
    if not tax:
        raise RuntimeError(
            f"Sale tax not found for name={vat_code_name!r} company_id={company_id}"
        )
    return tax[0]["id"]


def fetch_all_journal_ids(
    client: Any,
    company_id: int,
    exclude_ids: List[int] | None = None,
) -> List[int]:
    """Haal alle journal-ID's op voor een company, optioneel enkele uitsluitend."""
    journals = odoo_conn.search_read(
        client,
        "account.journal",
        [("company_id", "=", company_id), ("active", "in", [True, False])],
        ["id"],
    )
    all_ids = [j["id"] for j in journals]
    if exclude_ids:
        all_ids = [jid for jid in all_ids if jid not in exclude_ids]
    return all_ids


def _parse_grid_from_tag_name(tag_name: str) -> str | None:
    """Extraheer gridnummer uit de tag-weergavenaam.

    Voorbeelden:
        '+03 (Basis)' -> '03'
        '-54 (BTW)'   -> '54'
        '00'          -> '00'
    """
    m = re.match(r"^[+-]?(\d{2})\b", tag_name.strip())
    return m.group(1) if m else None


def _get_tag_sign(tag_name: str) -> str:
    """Extraheer het teken-prefix uit een tagnaam ('+' of '-')."""
    tag_name = tag_name.strip()
    if tag_name.startswith("+"):
        return "+"
    if tag_name.startswith("-"):
        return "-"
    return "+"


def fetch_vat_data_for_tax(
    client: Any,
    tax_id: int,
    company_id: int,
    journal_ids: List[int],
    period_start: str,
    period_end: str,
) -> Tuple[Dict[str, float], Dict[str, Dict[str, Any]]]:
    """Haal het geaggregeerde saldo per taxgrid op voor een specifieke belasting.

    Retourneert:
        grid_balances: {'03': -164095.93, '54': -2756.91, ...}
        tag_info: {'03': {'plus_id': 123, 'minus_id': 456}, ...}
    """
    domain: List[Any] = [
        "&",
        "&",
        "&",
        "&",
        "&",
        "&",
        ["display_type", "not in", ["line_section", "line_subsection", "line_note"]],
        ["company_id", "in", [company_id]],
        ["journal_id", "in", journal_ids],
        ["date", ">=", period_start],
        ["date", "<=", period_end],
        ["parent_state", "=", "posted"],
        "|",
        "|",
        "&",
        "&",
        ["tax_ids", "in", [tax_id]],
        ["tax_ids.type_tax_use", "=", "sale"],
        ["tax_repartition_line_id", "=", False],
        ["tax_line_id", "=", tax_id],
        "&",
        "&",
        ["tax_ids", "=", tax_id],
        ["tax_ids.type_tax_use", "=", "sale"],
        ["tax_repartition_line_id", "!=", False],
    ]

    result = odoo_conn.call(
        client,
        "account.move.line",
        "read_group",
        [domain],
        {
            "fields": ["tax_tag_ids", "balance"],
            "groupby": ["tax_tag_ids"],
            "lazy": False,
        },
    )

    grid_balances: Dict[str, float] = {}
    tag_info: Dict[str, Dict[str, Any]] = {}

    for group in result:
        tag_data = group.get("tax_tag_ids")
        if not tag_data or not isinstance(tag_data, (list, tuple)) or len(tag_data) < 2:
            continue

        tag_id = tag_data[0]
        tag_display = tag_data[1]
        balance = group.get("balance", 0.0)

        grid = _parse_grid_from_tag_name(tag_display)
        if grid is None:
            logger.warning("Could not parse grid from tag: %s", tag_display)
            continue

        grid_balances[grid] = grid_balances.get(grid, 0.0) + balance

        sign = _get_tag_sign(tag_display)
        if grid not in tag_info:
            tag_info[grid] = {}
        if sign == "+":
            tag_info[grid]["plus_id"] = tag_id
        else:
            tag_info[grid]["minus_id"] = tag_id

    return grid_balances, tag_info


def fetch_tax_tags_for_country(
    client: Any,
    country_id: int,
) -> Dict[str, Dict[str, int]]:
    """Haal alle taxtags op voor een land en bouw een grid->sign->tag_id-lookup.

    Retourneert: {'03': {'plus_id': 123, 'minus_id': 456}, ...}
    """
    tags = odoo_conn.search_read(
        client,
        "account.account.tag",
        [
            ("applicability", "=", "taxes"),
            ("country_id", "=", country_id),
        ],
        ["id", "name"],
    )

    result: Dict[str, Dict[str, int]] = {}
    for tag in tags:
        name = tag.get("name", "")
        grid = _parse_grid_from_tag_name(name)
        if grid is None:
            continue
        sign = _get_tag_sign(name)
        if grid not in result:
            result[grid] = {}
        if sign == "+":
            result[grid]["plus_id"] = tag["id"]
        else:
            result[grid]["minus_id"] = tag["id"]

    return result


def fetch_belgium_country_id(client: Any) -> int:
    """Los Belgium's country_id op in Odoo."""
    res = odoo_conn.search_read(
        client,
        "res.country",
        [("code", "=", "BE")],
        ["id"],
        limit=1,
    )
    if not res:
        raise RuntimeError("Belgium country record not found in Odoo")
    return res[0]["id"]
