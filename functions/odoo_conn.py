"""functions/odoo_conn.py — Odoo-transport voor Track A (JSON-2, pakket-API).

Vervangt `travel-experts-backend/apps/main/app/shared/odoo_client.py`
(handgeschreven op de legacy XML-RPC-module uit de Python-standaardbibliotheek)
door het gedeelde `odoo`-pakket (`from odoo import OdooClient`, JSON-2). Geen
import van die legacy XML-RPC-module, geen tekstuele verwijzing naar de oude
generieke "execute-kw"-methodenaam — zie de harde projectregels #6 in het plan
en `docs/contracts.md` §4.

Twee lagen in dit bestand:

1. **`get_client()`** — de enige plek die de echte `OdooClient` bouwt, uit
   `env.ENV` (`ODOO_URL/ODOO_DATABASE/ODOO_API_KEY/ODOO_USER`). Doet een
   **lazy import** van zowel het `odoo`-pakket als `env` (pas binnen de
   functie), zodat `import odoo_conn` — en dus elke module die alleen de
   onderstaande generieke helpers gebruikt — geen echte Odoo-omgeving en geen
   geïnstalleerd `odoo`-pakket vereist. Dat maakt `ruff check .` en
   `pytest -q` voor de pure transform-/`build_moves`-code en de
   payload-parity-harness volledig offline draaibaar (zie fase 2,
   "Offline / infra-constraint").

2. **Generieke ORM-helpers** (`call`, `search_read`, `read`, `create`,
   `write`, `search`) — een dunne, transport-only laag bovenop de
   model-attribuut-API van het pakket. Elke helper routeert uitsluitend via
   `OdooConnection.execute()` (het pakket noemt dit zelf de "unified
   dispatch" — zie `odoo/base.py`), nooit via de legacy XML-RPC-module en
   nooit via een method met de oude generieke "execute-kw"-naamgeving. Reden
   om dit generieke niveau te gebruiken in plaats van overal de specifieke
   `client.<model>.<methode>()`-varianten: meerdere modelklassen in het
   pakket overschrijven `create()`/`search_read()` met een *smaller*
   named-argument-signatuur dan de rauwe payload-dicts die `build_moves()`
   bouwt (bv. `AccountMove.create(partner_id, move_type, invoice_date, lines,
   **kwargs)` ondersteunt geen debit/credit-misc-lines). Deze generieke
   helpers geven **byte-voor-byte dezelfde payload** door aan de server als
   de oude generieke aanroep — noodzakelijk voor payload-pariteit (de
   kern-eis van deze fase) — terwijl ze toch volledig via de pakket-transport
   (JSON-2/auto-fallback) lopen. Waar het pakket wél een gedragsidentieke
   convenience-methode biedt (bv. `accounts.find_by_code` voor eenvoudige
   lookups), gebruikt de geporte code die rechtstreeks — zie
   `shared/account_utils.py`.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:  # pragma: no cover - alleen voor type-checkers, geen runtime-import
    from odoo import OdooClient


def get_client(api: str = "auto") -> "OdooClient":
    """Bouw een geauthenticeerde `OdooClient` uit `env.ENV`.

    Lazy imports (zowel `odoo` als `env`) zodat het enkel *aanroepen* van deze
    functie een echte Odoo-omgeving vereist — niet het simpelweg importeren
    van deze module. `api="auto"` kiest JSON-2 op Odoo >= 19, anders het
    legacy-fallback-pad van het pakket zelf (nooit onze eigen code).
    """
    from odoo import OdooClient  # lazy: vereist het (CI-only) private pakket

    from env import ENV  # lazy: vereist een volledige Track-A-omgeving

    return OdooClient(
        url=ENV.odoo_url,
        database=ENV.odoo_database,
        user=ENV.odoo_user,
        api_key=ENV.odoo_api_key,
        api=api,
    )


def call(
    client: Any,
    model: str,
    method: str,
    args: list | None = None,
    kwargs: dict | None = None,
) -> Any:
    """Roep een willekeurige ORM-methode aan op *model* via de pakket-transport.

    Equivalent (zelfde payload, andere transport) van de oude generieke
    `OdooClient`-aanroepmethode (model, method, args, kwargs). Gebruikt
    `client.connection.execute()` — het publieke dispatch-punt (géén legacy
    XML-RPC-module, géén oude generieke "execute-kw"-naamgeving) dat elke
    modelklasse in het pakket zelf ook gebruikt (zie
    `odoo/base.py::OdooConnection.execute`).
    """
    return client.connection.execute(model, method, args or [], kwargs or {})


def search_read(
    client: Any,
    model: str,
    domain: list,
    fields: list[str] | None = None,
    limit: int = 0,
    context: dict | None = None,
    order: str | None = None,
) -> list[dict]:
    """Generieke `search_read` op een willekeurig model (oude client-signatuur)."""
    orm_kwargs: dict[str, Any] = {"fields": fields or []}
    if limit:
        orm_kwargs["limit"] = limit
    if context:
        orm_kwargs["context"] = context
    if order:
        orm_kwargs["order"] = order
    return call(client, model, "search_read", [domain], orm_kwargs)


def read(
    client: Any,
    model: str,
    ids: list[int],
    fields: list[str] | None = None,
    context: dict | None = None,
) -> list[dict]:
    """Generieke `read` op een willekeurig model (oude client-signatuur)."""
    orm_kwargs: dict[str, Any] = {"fields": fields or []}
    if context:
        orm_kwargs["context"] = context
    return call(client, model, "read", [ids], orm_kwargs)


def create(client: Any, model: str, vals: dict) -> int:
    """Generieke `create` op een willekeurig model — rauwe payload-dict.

    Bewust NIET via `client.<model>.create(...)`: meerdere modelklassen in
    het pakket overschrijven `create()` met een smaller named-argument-
    signatuur die geen rauwe `build_moves()`-payloads (debit/credit-regels
    zonder `price_unit`, e.d.) accepteert. Deze helper geeft de payload
    ongewijzigd door — vereist voor payload-pariteit.
    """
    return call(client, model, "create", [vals])


def write(
    client: Any,
    model: str,
    ids: list[int],
    vals: dict,
    context: dict | None = None,
) -> bool:
    """Generieke `write` op een willekeurig model (oude client-signatuur)."""
    orm_kwargs: dict[str, Any] = {}
    if context:
        orm_kwargs["context"] = context
    return call(client, model, "write", [ids, vals], orm_kwargs)


def search(
    client: Any,
    model: str,
    domain: list,
    limit: int = 0,
) -> list[int]:
    """Generieke `search` op een willekeurig model (oude client-signatuur)."""
    orm_kwargs: dict[str, Any] = {}
    if limit:
        orm_kwargs["limit"] = limit
    return call(client, model, "search", [domain], orm_kwargs)
