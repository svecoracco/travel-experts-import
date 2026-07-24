"""functions/config_resolve.py — officiële `resolve_config`-precedentie +
plugin-configmerge voor de queue-import-pipeline (fase 3).

Port van:
- `resolve_config()` uit
  `travel-experts-backend/apps/main/app/models/app_config.py`
  (script → company → global → default).
- `build_import_config()` uit
  `travel-experts-backend/apps/main/app/api/imports.py` (env-defaults +
  DB-backed `app_config`-overlay met dezelfde script/company/global-
  precedentie, per config-sleutel).

**Consolidatie (fase-3-opdracht)**: dit bestand is nu de ENE plek voor
`resolve_config`-precedentie in `functions/`. De fase-2-interim
`shared/config_store.py` (opgeleverd omdat `vat_return`/`sbmov` al vóór fase 3
bestonden en config-resolutie nodig hadden) is verwijderd; `features/vat_return`
en `features/sbmov` importeren nu rechtstreeks vanuit deze module. Zie ook het
fase-3-eindrapport.

**Bron van config = de DB** (`app_config`), NIET de queue-payload (beslissing
#2 in het plan, docs/contracts.md §1) — de queue-functie
(`import_processor.py`) geeft alleen `company_id`/`script` als lookup-sleutel
mee.

Offline-testbaarheid (zie de fase-2/3-opdracht "Offline / infra-constraint"):
DB-toegang zit achter het dunne `ConfigRepo`-protocol hieronder; tests
injecteren een `FakeConfigRepo` in plaats van een echte SQL Server-verbinding.
De default-implementatie (`PyodbcConfigRepo`) doet een **lazy import** van
`env`/`shared.sql_server` (pas bij het eerste daadwerkelijke DB-gebruik), dus
het simpelweg *importeren* van deze module vereist geen Track-A-omgeving.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Protocol


@dataclass(frozen=True)
class ConfigRow:
    """Eén `app_config`-rij, geparsed (`value` al door `json.loads` gehaald
    waar mogelijk — zelfde gedrag als `AppConfig.get_value()` in de bron)."""

    company_id: int
    script_name: str
    key: str
    value: Any


class ConfigRepo(Protocol):
    """Dunne, mockbare DB-toegangslaag — zie de kop-comment hierboven."""

    def get_value(self, company_id: int, script_name: str, key: str) -> Any | None:
        """Enkelvoudige key-lookup (voor `resolve_config()`)."""
        ...

    def get_all_for_scope(self, company_id: int, script_name: str) -> list[ConfigRow]:
        """Alle rijen die in aanmerking komen voor `build_import_config()`:
        `(company_id == company_id OR company_id == 0) AND
         (script_name == script_name OR script_name == '')`."""
        ...


def _parse_value(raw_value: Any) -> Any:
    try:
        return json.loads(raw_value)
    except (json.JSONDecodeError, ValueError, TypeError):
        return raw_value


class PyodbcConfigRepo:
    """Default `ConfigRepo`: raw SQL/pyodbc tegen `[{DB_SCHEMA}].[app_config]`
    (docs/contracts.md §6) — via `shared.sql_server.open_cursor_for_connection_string`
    (zelfde pyodbc-connectiemechaniek als de ticket-lookups en de andere
    raw-SQL-modules in `functions/`)."""

    def _cursor(self):
        from env import ENV  # lazy: vereist een volledige Track-A-omgeving
        from shared.sql_server import open_cursor_for_connection_string

        return open_cursor_for_connection_string(ENV.sql_connection_string), ENV.db_schema

    def get_value(self, company_id: int, script_name: str, key: str) -> Any | None:
        cursor_cm, schema = self._cursor()
        with cursor_cm as cursor:
            cursor.execute(
                f"SELECT [value] FROM [{schema}].[app_config] "
                "WHERE company_id = ? AND script_name = ? AND [key] = ?",
                (company_id, script_name, key),
            )
            row = cursor.fetchone()
            if row is None:
                return None
            return _parse_value(row[0])

    def get_all_for_scope(self, company_id: int, script_name: str) -> list[ConfigRow]:
        cursor_cm, schema = self._cursor()
        rows: list[ConfigRow] = []
        with cursor_cm as cursor:
            cursor.execute(
                f"SELECT company_id, script_name, [key], [value] FROM [{schema}].[app_config] "
                "WHERE (company_id = ? OR company_id = 0) "
                "AND (script_name = ? OR script_name = '')",
                (company_id, script_name),
            )
            for row_company_id, row_script_name, row_key, row_value in cursor.fetchall():
                rows.append(
                    ConfigRow(
                        company_id=row_company_id,
                        script_name=row_script_name,
                        key=row_key,
                        value=_parse_value(row_value),
                    )
                )
        return rows


_DEFAULT_REPO = PyodbcConfigRepo()


def resolve_config(
    company_id: int,
    script_name: str,
    key: str,
    default: Any = None,
    *,
    repo: ConfigRepo | None = None,
) -> Any:
    """Resolve config-waarde met prioriteit: script > company > global > default.

    Spiegelt `AppConfig`-precedentie 1-op-1 (zie kop-comment). `repo` is
    injecteerbaar voor offline tests; default = `PyodbcConfigRepo()`.
    """
    repo = repo or _DEFAULT_REPO

    # 1. Script-niveau
    val = repo.get_value(company_id, script_name, key)
    if val is not None:
        return val

    # 2. Company-niveau
    val = repo.get_value(company_id, "", key)
    if val is not None:
        return val

    # 3. Globaal
    val = repo.get_value(0, "", key)
    if val is not None:
        return val

    # 4. Default
    return default


def _row_priority(row: ConfigRow, company_id: int, script_name: str) -> int:
    """Hogere waarde = hogere prioriteit — 1-op-1 poort van
    `apps/main/app/api/imports.py::_row_priority`."""
    if row.company_id == company_id and row.script_name == script_name:
        return 3  # script-niveau
    if row.company_id == company_id and row.script_name == "":
        return 2  # company-niveau
    if row.company_id == 0 and row.script_name == "":
        return 1  # globaal
    return 0


def build_import_config(
    company_id: int,
    plugin_name: str,
    *,
    repo: ConfigRepo | None = None,
    sql_connection_string: str | None = None,
) -> dict[str, Any]:
    """Bouw het config-dict dat een import-plugin verwacht: SQL-lookup-
    defaults (voor `shared/sql_server.py`-tickup-lookups) + de DB-backed
    `app_config`-overlay (script > company > global-precedentie per sleutel) —
    poort van `apps/main/app/api/imports.py::build_import_config`.

    **Afwijking t.o.v. de bron (bewust, functioneel identiek)**: de legacy
    `odoo_url`/`odoo_db`/`odoo_username`/`odoo_password`-sleutels worden NIET
    overgenomen — grep tegen zowel de bronrepo als de geporte
    `plugins/`/`shared/`-code bevestigt dat geen enkele plugin ze leest (de
    Odoo-toegang loopt uitsluitend via de los meegegeven `odoo_client`-
    parameter, zie `plugins/base.py::ImportPlugin.build_moves/execute`).
    `bsp_doc_types` is om dezelfde reden weggelaten (ook in de bron al dode
    config — geen enkele bsp-call-site leest die sleutel). Zie het
    fase-3-eindrapport.

    `sql_connection_string` is optioneel injecteerbaar (offline tests);
    default = `env.ENV.sql_connection_string` (lazy import).
    """
    repo = repo or _DEFAULT_REPO

    if sql_connection_string is None:
        from env import ENV  # lazy: alleen nodig voor de SQL-lookup-defaults

        sql_connection_string = ENV.sql_connection_string

    config: dict[str, Any] = {
        "sql_connection_string": sql_connection_string,
        "sql_db_timeout": 30,
        "sql_db_query_timeout": 45,
        "sql_db_chunk_size": 200,
    }

    rows = repo.get_all_for_scope(company_id, plugin_name)
    priorities: dict[str, int] = {}
    for row in rows:
        row_priority = _row_priority(row, company_id, plugin_name)
        if row_priority >= priorities.get(row.key, 0):
            config[row.key] = row.value
            priorities[row.key] = row_priority

    return config
