"""Business-logica voor analytic-account-vertaalconsistentie-checks.

Poort van
`travel-experts-backend/apps/main/app/translation_check/service.py`.
`_get_odoo_client()` → `odoo_conn.get_client()`.
"""

from __future__ import annotations

import logging
from collections import Counter
from typing import Any, Dict, List, Optional

import odoo_conn

logger = logging.getLogger(__name__)


def _pick_reference_name(names: Dict[str, str]) -> str:
    """Kies de canonieke naam over de taalvertalingen heen.

    Strategie: kies de meest voorkomende naam (meerderheid). Bij een gelijke
    stand: geef voorkeur aan de waarde uit en_US indien aanwezig, anders de
    eerste alfabetisch.
    """
    counts = Counter(names.values())
    top_count = max(counts.values())
    candidates = sorted(n for n, c in counts.items() if c == top_count)
    if len(candidates) == 1:
        return candidates[0]
    en = names.get("en_US")
    if en in candidates:
        return en
    return candidates[0]


def check_translations(company_id: int, plan_id: Optional[int]) -> Dict[str, Any]:
    """Zoek analytic accounts wiens naam verschilt tussen actieve talen."""
    client = odoo_conn.get_client()

    langs = odoo_conn.search_read(
        client,
        "res.lang",
        [("active", "=", True)],
        ["code", "name"],
        context={"allowed_company_ids": [company_id]},
    )
    languages_out = [{"code": lg["code"], "name": lg["name"]} for lg in langs]

    domain: List[Any] = [("company_id", "in", [company_id, False])]
    if plan_id:
        domain.append(("plan_id", "=", plan_id))

    consolidated: Dict[int, Dict[str, Any]] = {}
    plan_seen: Dict[int, str] = {}

    for lg in langs:
        lang_code = lg["code"]
        accounts = odoo_conn.search_read(
            client,
            "account.analytic.account",
            domain,
            ["id", "name", "plan_id"],
            context={
                "lang": lang_code,
                "allowed_company_ids": [company_id],
            },
        )
        for acc in accounts:
            acc_id = acc["id"]
            entry = consolidated.setdefault(
                acc_id, {"names": {}, "plan_id": acc.get("plan_id")}
            )
            entry["names"][lang_code] = acc.get("name") or ""
            plan = acc.get("plan_id")
            if plan and isinstance(plan, (list, tuple)) and len(plan) >= 2:
                plan_seen[plan[0]] = plan[1]

    mismatches: List[Dict[str, Any]] = []
    for acc_id, entry in consolidated.items():
        names = entry["names"]
        if len(set(names.values())) <= 1:
            continue
        reference = _pick_reference_name(names)
        deviating = sorted(code for code, val in names.items() if val != reference)
        plan = entry.get("plan_id")
        plan_db_id: Optional[int] = None
        plan_name = ""
        if plan and isinstance(plan, (list, tuple)) and len(plan) >= 2:
            plan_db_id = plan[0]
            plan_name = plan[1]
        mismatches.append(
            {
                "account_id": acc_id,
                "plan_id": plan_db_id,
                "plan_name": plan_name,
                "reference_name": reference,
                "translations": names,
                "deviating_langs": deviating,
            }
        )

    mismatches.sort(key=lambda m: (m.get("plan_name") or "", m["account_id"]))

    plans_out = [{"id": pid, "name": pname} for pid, pname in sorted(plan_seen.items())]

    return {
        "languages": languages_out,
        "plans": plans_out,
        "mismatches": mismatches,
        "total_checked": len(consolidated),
        "total_mismatched": len(mismatches),
    }


def apply_fixes(company_id: int, fixes: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Overschrijf de naam van elke opgegeven analytic account in elke actieve
    taal zodat alle vertalingen matchen met `correct_name`."""
    client = odoo_conn.get_client()

    langs = odoo_conn.search_read(
        client,
        "res.lang",
        [("active", "=", True)],
        ["code", "name"],
        context={"allowed_company_ids": [company_id]},
    )
    lang_codes = [lg["code"] for lg in langs]

    results: List[Dict[str, Any]] = []
    total_fixed = 0
    total_errors = 0

    for fix in fixes:
        try:
            account_id = int(fix.get("account_id"))
        except (TypeError, ValueError):
            total_errors += 1
            results.append(
                {
                    "account_id": fix.get("account_id"),
                    "correct_name": fix.get("correct_name"),
                    "fixed_langs": [],
                    "already_ok_langs": [],
                    "errors": ["Invalid account_id"],
                }
            )
            continue

        correct_name = fix.get("correct_name")
        if not isinstance(correct_name, str) or not correct_name.strip():
            total_errors += 1
            results.append(
                {
                    "account_id": account_id,
                    "correct_name": correct_name,
                    "fixed_langs": [],
                    "already_ok_langs": [],
                    "errors": ["Missing or empty correct_name"],
                }
            )
            continue

        fixed_langs: List[str] = []
        already_ok: List[str] = []
        errors: List[str] = []
        account_fixed = False

        for lang_code in lang_codes:
            try:
                current = odoo_conn.read(
                    client,
                    "account.analytic.account",
                    [account_id],
                    ["name"],
                    context={
                        "lang": lang_code,
                        "allowed_company_ids": [company_id],
                    },
                )
                current_name = current[0].get("name") if current else None
                if current_name == correct_name:
                    already_ok.append(lang_code)
                    continue
                odoo_conn.write(
                    client,
                    "account.analytic.account",
                    [account_id],
                    {"name": correct_name},
                    context={
                        "lang": lang_code,
                        "allowed_company_ids": [company_id],
                    },
                )
                fixed_langs.append(lang_code)
                account_fixed = True
            except Exception as exc:  # noqa: BLE001
                logger.exception(
                    "Failed to fix translation lang=%s account_id=%s",
                    lang_code,
                    account_id,
                )
                errors.append(f"{lang_code}: {exc}")

        if account_fixed:
            total_fixed += 1
        if errors:
            total_errors += 1

        results.append(
            {
                "account_id": account_id,
                "correct_name": correct_name,
                "fixed_langs": fixed_langs,
                "already_ok_langs": already_ok,
                "errors": errors,
            }
        )

    return {
        "results": results,
        "total_fixed": total_fixed,
        "total_errors": total_errors,
    }
