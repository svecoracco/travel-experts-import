"""Unit-tests voor `shared/account_utils.py` (resolve_account_id/resolve_tax_id/
analytic-account-helpers) tegen `FakeOdooClient` — volledig offline.
"""

from __future__ import annotations

import pytest

from shared.account_utils import (
    build_analytic_account_map,
    create_analytic_accounts,
    resolve_account_id,
    resolve_tax_id,
)
from tools.fake_odoo_client import FakeOdooClient


def test_resolve_account_id_found_and_cached():
    client = FakeOdooClient(
        {"account.account": [{"id": 42, "code": "604000", "name": "Travel"}]}
    )
    cache: dict[int, int] = {}
    acc_id = resolve_account_id(client, 604000, company_id=1, cache=cache)
    assert acc_id == 42
    assert cache[604000] == 42

    # Tweede aanroep mag geen nieuwe lookup nodig hebben (cache-hit) — leeg de
    # canned data om dat te bewijzen.
    client._canned["account.account"] = []
    acc_id_cached = resolve_account_id(client, 604000, company_id=1, cache=cache)
    assert acc_id_cached == 42


def test_resolve_account_id_not_found_raises():
    client = FakeOdooClient({"account.account": []})
    with pytest.raises(RuntimeError, match="Account not found"):
        resolve_account_id(client, 999999, company_id=1, cache={})


def test_resolve_tax_id_found():
    client = FakeOdooClient(
        {
            "account.tax": [
                {
                    "id": 5,
                    "name": "21%",
                    "company_id": 1,
                    "type_tax_use": "purchase",
                }
            ]
        }
    )
    tax_id = resolve_tax_id(client, "21%", company_id=1, cache={})
    assert tax_id == 5


def test_build_analytic_account_map_and_create_missing():
    client = FakeOdooClient(
        {
            "account.analytic.account": [{"id": 1, "name": "FN-100"}],
            "account.analytic.plan": [{"id": 9, "name": "File number"}],
        }
    )
    existing = build_analytic_account_map(client, ["FN-100", "FN-200"], company_id=1)
    assert existing == {"FN-100": 1}

    created = create_analytic_accounts(client, ["FN-200"], company_id=1)
    assert "FN-200" in created
    assert isinstance(created["FN-200"], int)


def test_create_analytic_accounts_missing_plan_raises():
    client = FakeOdooClient({"account.analytic.plan": []})
    with pytest.raises(RuntimeError, match="Analytic plan"):
        create_analytic_accounts(client, ["FN-999"], company_id=1, plan_name="Unknown plan")
