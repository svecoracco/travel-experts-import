"""Tests voor `env.py` — de ene Track-A-env-module (harde projectregels #7/#8).

Gebruikt `monkeypatch.setenv`/`delenv` (pytest's eigen mechanisme) om de
omgeving voor een *herladen* van de module te sturen — dit is geen directe
`os.environ`-referentie in onze eigen code (zie de banned-api-regel in
`pyproject.toml`), maar test-infrastructuur die precies daarvoor bedoeld is.
"""

from __future__ import annotations

import importlib
import sys

import pytest

_REQUIRED_VARS = {
    "ODOO_URL": "https://example.odoo.com",
    "ODOO_DATABASE": "exampledb",
    "ODOO_API_KEY": "key123",
    "ODOO_USER": "api-user",
    "DB_SCHEMA": "bts",
    "SQL_CONNECTION_STRING": "Driver=dummy;Server=dummy;",
    "AZURE_STORAGE_ACCOUNT_URL": "https://example.blob.core.windows.net/",
    "BLOB_CONTAINER_NAME": "imports",
    "AZURE_TENANT_ID": "tenant-id",
    "AZURE_CLIENT_ID": "client-id",
    "AZURE_CLIENT_SECRET": "client-secret",
}


def _reload_env_with(monkeypatch, overrides: dict[str, str | None]):
    sys.modules.pop("env", None)
    for key, value in _REQUIRED_VARS.items():
        monkeypatch.setenv(key, value)
    for key, value in overrides.items():
        if value is None:
            monkeypatch.delenv(key, raising=False)
        else:
            monkeypatch.setenv(key, value)
    return importlib.import_module("env")


def test_env_loads_with_all_required_vars_set(monkeypatch):
    mod = _reload_env_with(monkeypatch, {})
    assert mod.ENV.odoo_url == "https://example.odoo.com"
    assert mod.ENV.db_schema == "bts"
    assert mod.ENV.odoo_settings_cron_schedule is None


def test_env_crashes_before_use_when_odoo_url_missing(monkeypatch):
    with pytest.raises(Exception):  # noqa: B017 - MissingEnvError, geïmporteerd via de module zelf
        _reload_env_with(monkeypatch, {"ODOO_URL": None})


def test_env_rejects_unsafe_schema_identifier(monkeypatch):
    with pytest.raises(Exception):  # noqa: B017
        _reload_env_with(monkeypatch, {"DB_SCHEMA": "bts; DROP TABLE users;--"})


def test_env_rejects_uppercase_schema(monkeypatch):
    with pytest.raises(Exception):  # noqa: B017
        _reload_env_with(monkeypatch, {"DB_SCHEMA": "BTS"})
