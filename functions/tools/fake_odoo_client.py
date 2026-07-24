"""`FakeOdooClient` — canned, offline dubbelganger van de Odoo-client.

Gebruikt door de payload-parity-harness (`tools/parity_harness.py`) en door
`pytest`-tests van de plugins. Injecteert canned lookup-data zodat
`build_moves()` (oud én nieuw) volledig offline draait — **nooit** een echte
Odoo-server nodig, laat staan een POST daarnaartoe (harde projectregel #2).

Implementeert BEIDE client-oppervlakken tegelijk, zodat exact dezelfde
canned-state door zowel de **oude** bron-plugincode (`OdooClient.execute_kw/
search_read/create/write/search`, `travel-experts-backend/apps/main/app/
shared/odoo_client.py`) als de **nieuwe** poort (`odoo_conn.call/search_read/
create/write/search`, die routeert via `client.connection.execute(...)`)
gebruikt kan worden — dat is precies wat nodig is om ze op identieke input
te vergelijken:

- Oude, vlakke methodes: `execute_kw`, `search_read`, `read`, `create`,
  `write`, `search` (signatuur van de oude client).
- Nieuwe laag: `self.connection = self`, met een `execute(model, method,
  args, kwargs)`-methode (signatuur van `odoo/base.py::OdooConnection.execute`).

Domain-matching is een kleine subset (voldoende voor de lookups die de 8
plugins + shared/account_utils.py + shared/invoice_lookup.py doen): platte
lijst van `(field, operator, value)`-triples, impliciet AND'ed (geen
'&'/'|'-polish-notation — die komt niet voor in de build_moves-lookups).
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

_OPS = {
    "=": lambda a, b: a == b,
    "!=": lambda a, b: a != b,
    "in": lambda a, b: a in b,
    "not in": lambda a, b: a not in b,
    ">": lambda a, b: (a or 0) > b,
    ">=": lambda a, b: (a or 0) >= b,
    "<": lambda a, b: (a or 0) < b,
    "<=": lambda a, b: (a or 0) <= b,
    "like": lambda a, b: str(b).strip("%") in str(a or ""),
}


class FakeOdooClient:
    """Canned, offline Odoo-client-dubbelganger voor de parity-harness/tests."""

    def __init__(self, canned: Optional[Dict[str, List[Dict[str, Any]]]] = None):
        # Nieuwe-laag-oppervlak: `client.connection.execute(...)` (odoo_conn.*)
        self.connection = self
        self._canned: Dict[str, List[Dict[str, Any]]] = {
            model: list(rows) for model, rows in (canned or {}).items()
        }
        self._next_id = 900000
        self.created: list[tuple[str, dict, int]] = []
        self.written: list[tuple[str, list, dict]] = []
        self.posted: list[list[int]] = []

    # ------------------------------------------------------------------
    # Interne helpers
    # ------------------------------------------------------------------

    def _match_domain(self, record: Dict[str, Any], domain: List[Any]) -> bool:
        for clause in domain:
            if isinstance(clause, str):
                # Polish-notation-operator ('&'/'|') — niet ondersteund, negeer
                # (de lookups in build_moves gebruiken dit niet).
                continue
            field, op, value = clause
            actual = record.get(field)
            fn = _OPS.get(op)
            if fn is None:
                raise NotImplementedError(f"FakeOdooClient: unsupported operator {op!r}")
            if not fn(actual, value):
                return False
        return True

    def _search_read_impl(
        self,
        model: str,
        domain: List[Any],
        fields: Optional[List[str]] = None,
        limit: Optional[int] = None,
        offset: int = 0,
        order: Optional[str] = None,
        context: Optional[dict] = None,
    ) -> List[Dict[str, Any]]:
        records = self._canned.get(model, [])
        matched = [r for r in records if self._match_domain(r, domain or [])]
        matched = matched[offset:]
        if limit:
            matched = matched[:limit]
        if fields:
            return [{k: r.get(k) for k in fields} for r in matched]
        return [dict(r) for r in matched]

    def _new_id(self) -> int:
        self._next_id += 1
        return self._next_id

    # ------------------------------------------------------------------
    # Nieuwe laag: `client.connection.execute(model, method, args, kwargs)`
    # (gebruikt door odoo_conn.call/search_read/read/create/write/search)
    # ------------------------------------------------------------------

    def execute(
        self,
        model: str,
        method: str,
        args: Optional[list] = None,
        kwargs: Optional[dict] = None,
    ) -> Any:
        args = args or []
        kwargs = dict(kwargs or {})
        kwargs.pop("context", None)  # canned data heeft geen company-context nodig

        if method == "search_read":
            domain = args[0] if args else []
            return self._search_read_impl(model, domain, **kwargs)
        if method == "search":
            domain = args[0] if args else []
            recs = self._search_read_impl(model, domain, fields=["id"], **kwargs)
            return [r["id"] for r in recs]
        if method == "read":
            ids = args[0] if args else []
            fields = kwargs.get("fields")
            recs = self._canned.get(model, [])
            out = [r for r in recs if r.get("id") in ids]
            if fields:
                return [{k: r.get(k) for k in fields} for r in out]
            return [dict(r) for r in out]
        if method == "create":
            vals = args[0] if args else {}
            new_id = self._new_id()
            self.created.append((model, vals, new_id))
            self._canned.setdefault(model, []).append({**vals, "id": new_id})
            return new_id
        if method == "write":
            ids = args[0] if args else []
            vals = args[1] if len(args) > 1 else {}
            self.written.append((model, ids, vals))
            for r in self._canned.get(model, []):
                if r.get("id") in ids:
                    r.update(vals)
            return True
        if method == "action_post":
            ids = args[0] if args else []
            self.posted.append(ids)
            return True
        if method == "button_cancel":
            return True
        if method == "reconcile":
            return True
        if method == "fields_get":
            # Simuleer een moderne Odoo (>= 19): account.account heeft géén
            # company_id meer (multi-company via company_ids), zie
            # odoo/models.py::AccountAccount._default_fields.
            return {}
        raise NotImplementedError(f"FakeOdooClient: unhandled {model}.{method}")

    # ------------------------------------------------------------------
    # Oude, vlakke laag (signatuur van de bron `OdooClient`, o.a. gebruikt
    # door de OUDE plugincode rechtstreeks in de parity-harness)
    # ------------------------------------------------------------------

    def execute_kw(
        self,
        model: str,
        method: str,
        args: Optional[list] = None,
        kwargs: Optional[dict] = None,
    ) -> Any:
        return self.execute(model, method, args, kwargs)

    def search_read(
        self,
        model: str,
        domain: List[Any],
        fields: List[str],
        limit: int = 0,
        context: Optional[dict] = None,
    ) -> List[Dict[str, Any]]:
        return self._search_read_impl(model, domain, fields=fields, limit=limit or None)

    def read(
        self,
        model: str,
        ids: List[int],
        fields: List[str],
        context: Optional[dict] = None,
    ) -> List[Dict[str, Any]]:
        return self.execute(model, "read", [ids], {"fields": fields})

    def create(self, model: str, vals: Dict[str, Any]) -> int:
        return self.execute(model, "create", [vals])

    def write(
        self,
        model: str,
        ids: List[int],
        vals: Dict[str, Any],
        context: Optional[dict] = None,
    ) -> bool:
        return self.execute(model, "write", [ids, vals])

    def search(self, model: str, domain: List[Any], limit: int = 0) -> List[int]:
        return self.execute(model, "search", [domain], {"limit": limit} if limit else {})
