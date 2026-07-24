"""Business-logica voor VAT Return: config-loading, correctie-berekening,
move-building. Poort van
`travel-experts-backend/apps/main/app/vat_return/service.py`.

Verschillen t.o.v. de bron (transport/entrypoint-wijzigingen, zie plan-
verschil #2/#3):
- `_get_odoo_client()` → `odoo_conn.get_client()` (pakket, JSON-2).
- `resolve_config` → `config_resolve.resolve_config` (raw SQL i.p.v.
  Flask-SQLAlchemy; fase-3-consolidatie van de fase-2-interim
  `shared/config_store.py`, zie `config_resolve.py`).
- `VatReturnEntry.query`/`db.session` (Flask-SQLAlchemy) →
  `features.vat_return.store` (raw SQL/pyodbc).
- `g.current_user`/Flask-JWT → de aanroeper (Next.js) geeft `created_by`
  expliciet mee in de request-body/DTO (zie `docs/contracts.md` §2, geen
  eigen user-auth in de functie zelf).
"""

from __future__ import annotations

import base64
import calendar
import logging
from decimal import ROUND_HALF_UP, Decimal
from typing import Any, Dict, List, Tuple

import odoo_conn
from config_resolve import resolve_config
from features.vat_return import store
from features.vat_return.excel_builder import build_vat_return_excel
from features.vat_return.odoo_queries import (
    fetch_all_journal_ids,
    fetch_belgium_country_id,
    fetch_tax_tags_for_country,
    fetch_vat_data_for_tax,
    resolve_sale_tax_id,
)
from shared.account_utils import resolve_account_id

logger = logging.getLogger(__name__)

REQUIRED_CONFIG_KEYS = [
    "vat_codes",
    "correction_mappings",
    "remainder_grid",
    "correction_account",
    "vat_return_journal_id",
    "standard_vat_rate",
]


def _round2(value: float) -> float:
    """Rond af op 2 decimalen via banker's rounding."""
    return float(Decimal(str(value)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def load_vat_return_config(company_id: int) -> Dict[str, Any]:
    """Laad alle vat_return-configsleutels uit app_config."""
    config: Dict[str, Any] = {}
    for key in REQUIRED_CONFIG_KEYS:
        val = resolve_config(company_id, "vat_return", key)
        if val is None:
            raise ValueError(f"Missing required config key: vat_return.{key}")
        config[key] = val

    # Optionele sleutels
    config["vat_return_exclude_journal_ids"] = resolve_config(
        company_id, "vat_return", "vat_return_exclude_journal_ids", default=[]
    )

    # Valideer
    if not config["vat_codes"]:
        raise ValueError("vat_codes must be a non-empty list")
    if not config["correction_mappings"]:
        raise ValueError("correction_mappings must be a non-empty list")
    rate = config["standard_vat_rate"]
    if not rate or float(rate) == 0:
        raise ValueError("standard_vat_rate must not be zero")

    return config


def _parse_period(period: str) -> Tuple[str, str]:
    """Parse YYYY-MM naar (start_date, end_date)-strings."""
    parts = period.split("-")
    if len(parts) != 2:
        raise ValueError(f"Invalid period format: {period}, expected YYYY-MM")
    year, month = int(parts[0]), int(parts[1])
    if month < 1 or month > 12:
        raise ValueError(f"Invalid month: {month}")
    last_day = calendar.monthrange(year, month)[1]
    return f"{year}-{month:02d}-01", f"{year}-{month:02d}-{last_day:02d}"


def fetch_vat_return_data(company_id: int, period: str) -> Dict[str, Any]:
    """Haal VAT-data op uit Odoo voor alle geconfigureerde VAT-codes.

    Retourneert de volledige responsestructuur voor het GET-endpoint.
    """
    config = load_vat_return_config(company_id)
    client = odoo_conn.get_client()
    period_start, period_end = _parse_period(period)

    # Haal alle journal-ID's op voor de company
    exclude_ids = config.get("vat_return_exclude_journal_ids", [])
    journal_ids = fetch_all_journal_ids(client, company_id, exclude_ids)
    if not journal_ids:
        raise RuntimeError("No journals found for company")

    # Haal taxtags op voor België (nodig om later te boeken)
    country_id = fetch_belgium_country_id(client)
    all_tag_info = fetch_tax_tags_for_country(client, country_id)

    # Haal data op per VAT-code
    data: Dict[str, Dict[str, float]] = {}
    per_code_tag_info: Dict[str, Dict[str, Dict[str, Any]]] = {}

    for vat_code in config["vat_codes"]:
        tax_id = resolve_sale_tax_id(client, vat_code, company_id)
        grid_balances, code_tag_info = fetch_vat_data_for_tax(
            client, tax_id, company_id, journal_ids, period_start, period_end
        )
        data[vat_code] = grid_balances
        # Merge tag-info van deze code's data met de globale tag-info
        merged_tags = dict(all_tag_info)
        for grid, info in code_tag_info.items():
            if grid not in merged_tags:
                merged_tags[grid] = info
            else:
                merged_tags[grid] = {**merged_tags[grid], **info}
        per_code_tag_info[vat_code] = merged_tags

    return {
        "period": period,
        "config": {
            "correction_mappings": config["correction_mappings"],
            "remainder_grid": config["remainder_grid"],
            "standard_vat_rate": float(config["standard_vat_rate"]),
            "correction_account": int(config["correction_account"]),
            "vat_return_journal_id": int(config["vat_return_journal_id"]),
        },
        "data": data,
        "tag_info": all_tag_info,
    }


def check_existing_entry(company_id: int, period: str) -> Dict[str, Any]:
    """Check of er een actieve (niet-dismissed) correctie-entry bestaat voor company/period."""
    existing = store.get_active_entry(company_id, period)
    if existing:
        return {
            "exists": True,
            "move_id": existing.odoo_move_id,
            "move_name": existing.odoo_move_name,
            "created_at": (
                existing.created_at.isoformat() if existing.created_at else None
            ),
            "created_by": existing.created_by,
        }
    return {"exists": False}


def dismiss_entry(
    company_id: int, period: str, dismissed_by: str | None = None
) -> Dict[str, Any]:
    """Markeer een bestaande correctie-entry als dismissed (lock released).

    De audit-log-rij blijft staan, maar de security-banner verschijnt niet meer.
    """
    existing = store.get_active_entry(company_id, period)
    if not existing:
        return {"error": "No active correction entry found for this period"}

    store.dismiss_entry(existing.id, dismissed_by)

    return {"success": True}


def book_correction_entry(
    company_id: int,
    period: str,
    correction_lines: List[Dict[str, Any]],
    start_data: Dict[str, Dict[str, float]] | None = None,
    created_by: str | None = None,
) -> Dict[str, Any]:
    """Maak en post de correctie-journal-entry in Odoo.

    Elke correction_line: {description, grid, amount, tag_id}
    """
    config = load_vat_return_config(company_id)
    client = odoo_conn.get_client()
    period_start, period_end = _parse_period(period)

    journal_id = int(config["vat_return_journal_id"])
    correction_account_code = int(config["correction_account"])

    # PRIMAIRE guard: lokale DB-idempotentie-check (alleen actieve, niet-dismissed entries)
    existing_local = store.get_active_entry(company_id, period)
    if existing_local:
        return {
            "error": "correction_exists",
            "move_id": existing_local.odoo_move_id,
            "move_name": existing_local.odoo_move_name,
            "created_at": (
                existing_local.created_at.isoformat()
                if existing_local.created_at
                else None
            ),
            "created_by": existing_local.created_by,
        }

    # SECUNDAIRE terugval: Odoo-side-idempotentie-check
    ref = f"VAT-CORR-{period}"
    existing = odoo_conn.search_read(
        client,
        "account.move",
        [
            ("ref", "=", ref),
            ("journal_id", "=", journal_id),
            ("date", ">=", period_start),
            ("date", "<=", period_end),
            ("company_id", "=", company_id),
        ],
        ["id"],
        limit=1,
    )
    if existing:
        return {
            "error": f"Correction entry already exists for {period}",
            "move_id": existing[0]["id"],
        }

    # Resolve correctie-account-ID
    account_cache: Dict[int, int] = {}
    correction_account_id = resolve_account_id(
        client, correction_account_code, company_id, account_cache
    )

    # Bouw line_ids
    line_ids = []
    for line in correction_lines:
        amount = _round2(line["amount"])
        debit = amount if amount > 0 else 0.0
        credit = abs(amount) if amount < 0 else 0.0

        tag_id = line.get("tag_id")
        tax_tag_ids = [(6, 0, [tag_id])] if tag_id else []

        line_ids.append(
            (
                0,
                0,
                {
                    "account_id": correction_account_id,
                    "name": line["description"],
                    "debit": debit,
                    "credit": credit,
                    "tax_tag_ids": tax_tag_ids,
                },
            )
        )

    if not line_ids:
        return {"error": "No correction lines to book"}

    move_payload = {
        "move_type": "entry",
        "ref": ref,
        "date": period_end,
        "journal_id": journal_id,
        "company_id": company_id,
        "line_ids": line_ids,
    }

    move_id = odoo_conn.create(client, "account.move", move_payload)

    # Post de entry
    warning = None
    try:
        odoo_conn.call(client, "account.move", "action_post", [[move_id]])
    except Exception as e:
        logger.error("Failed to post move %s: %s", move_id, e)
        warning = f"Move created but could not be posted: {e}"

    # Haal de move's weergavenaam op uit Odoo
    odoo_move_name = None
    try:
        move_data = odoo_conn.read(client, "account.move", [move_id], ["name"])
        if move_data:
            odoo_move_name = move_data[0].get("name")
    except Exception as e:
        logger.warning("Could not fetch move name for %s: %s", move_id, e)

    # Voeg Excel-workbook toe indien start_data is meegegeven
    if start_data:
        try:
            excel_config = {
                "correction_mappings": config["correction_mappings"],
                "remainder_grid": config["remainder_grid"],
                "standard_vat_rate": float(config["standard_vat_rate"]),
                "correction_account": int(config["correction_account"]),
            }
            excel_bytes = build_vat_return_excel(period, start_data, excel_config)
            odoo_conn.create(
                client,
                "ir.attachment",
                {
                    "name": f"VAT_Return_Calculation_{period}.xlsx",
                    "type": "binary",
                    "datas": base64.b64encode(excel_bytes).decode("utf-8"),
                    "res_model": "account.move",
                    "res_id": move_id,
                    "mimetype": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                },
            )
        except Exception as e:
            logger.error("Failed to attach Excel to move %s: %s", move_id, e)

    # Bewaar audittrail in lokale DB
    total_amount = sum(_round2(line["amount"]) for line in correction_lines)

    store.insert_entry(
        company_id=company_id,
        period=period,
        odoo_move_id=move_id,
        odoo_move_name=odoo_move_name,
        ref=ref,
        created_by=created_by,
        total_amount=total_amount,
        line_count=len(correction_lines),
    )

    result: Dict[str, Any] = {
        "success": True,
        "move_id": move_id,
        "odoo_move_name": odoo_move_name,
    }
    if warning:
        result["warning"] = warning

    return result
