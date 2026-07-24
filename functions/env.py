"""functions/env.py — het ENE env-module voor Track A (harde projectregel #8).

Parseert en valideert **alle** environment-variabelen die `functions/`
gebruikt, **bij import** van deze module, en exporteert één bevroren
(`frozen=True`) dataclass-instantie (`ENV`). Nergens anders in `functions/`
(shared/plugins/features/syncs/tools, ook niet in tests) mag een directe
`os.environ`/`os.getenv`-referentie voorkomen — zie `pyproject.toml`
(`tool.ruff.lint.flake8-tidy-imports.banned-api`) voor de lint-afdwinging.

Ontwerpbeslissing (belangrijk voor de rest van de port): deze module wordt
UITSLUITEND geïmporteerd door de code die daadwerkelijk verbinding maakt met
Odoo/SQL Server/Azure Blob (`odoo_conn.get_client()`, `syncs/*`,
`features/vat_return/store.py`, ...). De pure transform-/`build_moves`-code in
`shared/` en `plugins/*` blijft **env-vrij**: die krijgt de Odoo-client (of een
fake/mock in tests en de parity-harness) als parameter aangereikt
(dependency-injectie) en importeert deze module niet. Zo kunnen
`ruff check .` en `pytest -q` voor de pure code draaien zonder dat er ooit een
Odoo-/SQL-/Azure-omgeving hoeft te bestaan — zie de fase-2-opdracht
("Offline / infra-constraint").

Geen enkele variabele hieronder heeft een default: ontbreekt er één, dan
crasht het proces **vóór het eerste gebruik** (harde projectregels #7/#8) —
nooit een stille fallback naar het verkeerde schema of de verkeerde Odoo-
omgeving.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass

# Schema-identifiers worden geïnterpoleerd in raw SQL (nooit parametriseerbaar
# als identifier) — dus hard valideren tegen een veilig patroon vóór gebruik.
# Zie docs/contracts.md §6.
_SCHEMA_RE = re.compile(r"^[a-z][a-z0-9_]*$")


class MissingEnvError(RuntimeError):
    """Een verplichte environment-variabele ontbreekt of is leeg."""


def _require(name: str) -> str:
    value = os.environ.get(name)
    if value is None or not value.strip():
        raise MissingEnvError(
            f"Ontbrekende verplichte environment-variabele: {name!r}. "
            "Zet deze in App Settings / local.settings.json vóórdat de "
            "Functions-host start — zie docs/contracts.md §6 en de harde "
            "projectregels #7/#8 in het plan. Geen stille fallback."
        )
    return value.strip()


def _optional(name: str) -> str | None:
    """Voor genuinely optionele, niet-tenant-kritische instellingen (bv. de
    cron-schedule-overrides in `syncs/functions.py`) — in tegenstelling tot
    `_require()` crasht dit NIET bij afwezigheid. Blijft de ENIGE plek die
    `os.environ` aanraakt (harde projectregel #8); de caller bepaalt zijn
    eigen fallback-gedrag, dit is geen tenant-/schema-beslissing (regel #7
    is hier niet van toepassing)."""
    value = os.environ.get(name)
    return value.strip() if value and value.strip() else None


@dataclass(frozen=True)
class TrackAEnv:
    """Bevroren, gevalideerde env-snapshot voor Track A (`functions/`)."""

    # Odoo (het gedeelde `odoo`-pakket, JSON-2) — unificeer op deze vier namen,
    # zie plan-verschil #5. `ODOO_DB`/`ODOO_USERNAME`/`ODOO_PASSWORD` bestaan
    # NIET meer.
    odoo_url: str
    odoo_database: str
    odoo_api_key: str
    odoo_user: str

    # Tenant-schema (connection-string-route voor web; expliciete raw-SQL-
    # kwalificatie hier) — zie docs/contracts.md §5-§6.
    db_schema: str

    # Raw-SQL-toegang (csv_blob_sync_log, vat_return_entries, staging-
    # tabellen/ticket-lookups) — pyodbc-connection-string.
    sql_connection_string: str

    # Azure Blob Storage (geüploade importbestanden + CSV-blob-sync).
    azure_storage_account_url: str
    blob_container_name: str
    azure_tenant_id: str
    azure_client_id: str
    azure_client_secret: str

    # Optionele cron-schedule-overrides voor de timer-syncs (`syncs/functions.py`).
    # None = de caller berekent zijn eigen (tijdzone-afhankelijke) default —
    # dit is bewust GEEN `_require()`-veld: het is scheduling-configuratie,
    # geen tenant-/schema-beslissing (harde projectregel #7 is hier niet van
    # toepassing; #8 wel — vandaar toch via deze ene env-module).
    odoo_settings_cron_schedule: str | None
    csv_blob_files_cron_schedule: str | None
    csv_blob_files_retry_cron_schedule: str | None

    # Queue-naam voor de import-queue-trigger (`import_processor.py`, fase 3).
    # None = de caller valt terug op de contract-default "import-jobs" (zie
    # docs/contracts.md §1: "default `import-jobs` als niet gezet") — dit is
    # GEEN tenant-/schema-beslissing (regel #7 niet van toepassing), dus net
    # als de cron-schedules hierboven bewust een `_optional()`-veld. De
    # storage-account-connectie zelf (`AZURE_QUEUE_ACCOUNT_URL`/
    # `AZURE_QUEUE_ACCOUNT_CREDENTIAL`) wordt NIET door deze module gelezen:
    # de queue-trigger is een declaratieve binding die zijn connectie-string
    # rechtstreeks van het Functions-platform (App Settings) krijgt, niet via
    # onze eigen Python-code — zie `import_processor.py` kop-comment.
    azure_queue_import_jobs_name: str | None


def _build_env() -> TrackAEnv:
    db_schema = _require("DB_SCHEMA")
    if not _SCHEMA_RE.match(db_schema):
        raise MissingEnvError(
            f"DB_SCHEMA={db_schema!r} is geen veilige SQL-identifier "
            f"(verwacht patroon {_SCHEMA_RE.pattern!r}). Weiger te starten — "
            "een ongevalideerd schema mag nooit als identifier in raw SQL "
            "geïnterpoleerd worden (harde projectregel #7)."
        )

    return TrackAEnv(
        odoo_url=_require("ODOO_URL"),
        odoo_database=_require("ODOO_DATABASE"),
        odoo_api_key=_require("ODOO_API_KEY"),
        odoo_user=_require("ODOO_USER"),
        db_schema=db_schema,
        sql_connection_string=_require("SQL_CONNECTION_STRING"),
        azure_storage_account_url=_require("AZURE_STORAGE_ACCOUNT_URL"),
        blob_container_name=_require("BLOB_CONTAINER_NAME"),
        azure_tenant_id=_require("AZURE_TENANT_ID"),
        azure_client_id=_require("AZURE_CLIENT_ID"),
        azure_client_secret=_require("AZURE_CLIENT_SECRET"),
        odoo_settings_cron_schedule=_optional("ODOO_SETTINGS_CRON_SCHEDULE"),
        csv_blob_files_cron_schedule=_optional("CSV_BLOB_FILES_CRON_SCHEDULE"),
        csv_blob_files_retry_cron_schedule=_optional(
            "CSV_BLOB_FILES_RETRY_CRON_SCHEDULE"
        ),
        azure_queue_import_jobs_name=_optional("AZURE_QUEUE_IMPORT_JOBS_NAME"),
    )


ENV: TrackAEnv = _build_env()
