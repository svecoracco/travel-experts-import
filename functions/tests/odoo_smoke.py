"""Fase 0 CI import-smoke-test.

Doel: bewijzen dat het `odoo`-pakket (gepind op tag 2.0.7, JSON-2) installeerbaar en
importeerbaar is, en één triviale JSON-2 read tegen een TEST-Odoo-omgeving doet.

Regels (harde projectregels, fase 0):
- Post NOOIT tegen echte/productie-Odoo. Dit script doet uitsluitend een read
  (`search_read`, `limit=1`) tegen de omgeving die in ODOO_URL/ODOO_DATABASE staat — dat moet
  in CI een TEST-database zijn (GitHub-secrets), nooit de BTS-productieomgeving.
- Leest alle verbindingsgegevens uit de omgeving (env), nooit hardcoded of uit een
  gecommitteerd bestand.
- Faalt lokaal (zonder env) netjes met een duidelijke melding i.p.v. een stacktrace/crash —
  zodat dit script alleen "scherp" draait in CI met de vier secrets gezet.

Env-variabelen (moeten door de CI-workflow als secrets aangeleverd worden):
    ODOO_URL       - basis-URL van de (test-)Odoo-omgeving
    ODOO_DATABASE  - database-naam
    ODOO_API_KEY   - API-key (geen wachtwoord)
    ODOO_USER      - gebruikersnaam/login

Gebruik:
    python functions/tests/odoo_smoke.py

Exit-codes:
    0  - import + read geslaagd
    1  - verplichte env ontbreekt (verwacht bij lokaal draaien zonder secrets)
    2  - import van het odoo-pakket mislukt (dependency/installatieprobleem)
    3  - de JSON-2 read zelf faalde (verbindings-/auth-/API-probleem)
"""

from __future__ import annotations

import os
import sys

REQUIRED_ENV_VARS = ("ODOO_URL", "ODOO_DATABASE", "ODOO_API_KEY", "ODOO_USER")


def _missing_env_vars() -> list[str]:
    return [name for name in REQUIRED_ENV_VARS if not os.environ.get(name)]


def main() -> int:
    missing = _missing_env_vars()
    if missing:
        print(
            "odoo_smoke: SKIP (geen crash) - ontbrekende env-variabelen: "
            f"{', '.join(missing)}. Dit is verwacht wanneer lokaal gedraaid zonder "
            "CI-secrets; in CI (.github/workflows/odoo-import-smoke.yml) worden deze "
            "als GitHub-secrets aangeleverd tegen een TEST-Odoo-omgeving.",
            file=sys.stderr,
        )
        return 1

    try:
        from odoo import OdooClient
    except ImportError as exc:  # pragma: no cover - dependency/installatieprobleem
        print(
            f"odoo_smoke: FAIL - kan het 'odoo'-pakket niet importeren ({exc}). "
            "Controleer dat functions/requirements.txt geïnstalleerd is (odoo==2.0.7, "
            "git+https met GH_TOKEN-URL-rewrite, zie requirements.txt-commentaar).",
            file=sys.stderr,
        )
        return 2

    url = os.environ["ODOO_URL"]
    database = os.environ["ODOO_DATABASE"]
    api_key = os.environ["ODOO_API_KEY"]
    user = os.environ["ODOO_USER"]

    try:
        client = OdooClient(
            url=url,
            database=database,
            user=user,
            api_key=api_key,
            api="auto",  # kiest json2 op Odoo >= 19, anders xmlrpc-fallback
        )

        # Triviale, read-only JSON-2-call. NOOIT create/write/unlink hier — dit is een
        # smoke-test, geen functionele test, en mag nooit tegen productie draaien.
        companies = client.companies.search_read(fields=["id", "name"], limit=1)
    except Exception as exc:  # noqa: BLE001 - we willen elke fout hier netjes rapporteren
        print(f"odoo_smoke: FAIL - JSON-2 read mislukt: {exc}", file=sys.stderr)
        return 3

    resolved_api = getattr(client, "api", "?")
    print(
        f"odoo_smoke: OK - pakket geïmporteerd, transport='{resolved_api}', "
        f"search_read('res.company', limit=1) gaf {len(companies)} record(en) terug."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
