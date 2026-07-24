"""Unit-tests voor de generieke `odoo_conn`-helpers tegen `FakeOdooClient`.

Volledig offline (geen echte Odoo, geen echte env) — zie
`tools/fake_odoo_client.py`.
"""

from __future__ import annotations

import odoo_conn
from tools.fake_odoo_client import FakeOdooClient


def test_search_read_matches_domain():
    client = FakeOdooClient(
        {"res.partner": [{"id": 1, "ref": "A"}, {"id": 2, "ref": "B"}]}
    )
    rows = odoo_conn.search_read(client, "res.partner", [("ref", "=", "B")], ["id", "ref"])
    assert rows == [{"id": 2, "ref": "B"}]


def test_search_read_in_operator_and_limit():
    client = FakeOdooClient(
        {
            "res.currency": [
                {"id": 1, "name": "EUR"},
                {"id": 2, "name": "USD"},
                {"id": 3, "name": "GBP"},
            ]
        }
    )
    rows = odoo_conn.search_read(
        client, "res.currency", [("name", "in", ["USD", "GBP"])], ["id", "name"], limit=1
    )
    assert rows == [{"id": 2, "name": "USD"}]


def test_create_assigns_incrementing_ids():
    client = FakeOdooClient()
    id1 = odoo_conn.create(client, "account.move", {"ref": "A"})
    id2 = odoo_conn.create(client, "account.move", {"ref": "B"})
    assert id2 > id1
    assert client.created[0][0] == "account.move"


def test_write_updates_canned_record():
    client = FakeOdooClient({"account.move": [{"id": 5, "state": "draft"}]})
    ok = odoo_conn.write(client, "account.move", [5], {"state": "posted"})
    assert ok is True
    assert client._canned["account.move"][0]["state"] == "posted"


def test_search_returns_ids_only():
    client = FakeOdooClient({"account.move": [{"id": 7, "ref": "X"}, {"id": 8, "ref": "Y"}]})
    ids = odoo_conn.search(client, "account.move", [("ref", "=", "Y")])
    assert ids == [8]


def test_call_routes_through_connection_execute():
    client = FakeOdooClient()
    result = odoo_conn.call(client, "account.move", "action_post", [[1, 2]])
    assert result is True
    assert client.posted == [[1, 2]]
