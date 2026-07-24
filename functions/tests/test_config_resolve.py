"""Unit-tests voor `config_resolve.py` — de officiële `resolve_config`-
precedentie + `build_import_config`-merge (fase 3). Volledig offline via een
in-memory `FakeConfigRepo` (geen echte SQL Server-verbinding, zie de
"Offline / infra-constraint"-alinea in de fase-3-opdracht).

Bewijst ook de consolidatie van de fase-2-interim `shared/config_store.py`:
`features/vat_return/service.py` en `features/sbmov/service.py` importeren nu
`resolve_config` rechtstreeks vanuit deze module (zie
`test_features_use_config_resolve` onderaan).
"""

from __future__ import annotations

from dataclasses import dataclass

from config_resolve import ConfigRow, build_import_config, resolve_config


@dataclass
class FakeConfigRepo:
    """In-memory dubbelganger van `PyodbcConfigRepo` — canned `(company_id,
    script_name, key) -> value`-mapping."""

    values: dict[tuple[int, str, str], object]
    all_rows: list[ConfigRow] | None = None

    def get_value(self, company_id: int, script_name: str, key: str):
        return self.values.get((company_id, script_name, key))

    def get_all_for_scope(self, company_id: int, script_name: str) -> list[ConfigRow]:
        if self.all_rows is not None:
            return [
                row
                for row in self.all_rows
                if row.company_id in (company_id, 0)
                and row.script_name in (script_name, "")
            ]
        return [
            ConfigRow(company_id=c, script_name=s, key=k, value=v)
            for (c, s, k), v in self.values.items()
            if c in (company_id, 0) and s in (script_name, "")
        ]


def test_resolve_config_prefers_script_level():
    repo = FakeConfigRepo(
        values={
            (1, "airplus", "journal_id"): 100,
            (1, "", "journal_id"): 200,
            (0, "", "journal_id"): 300,
        }
    )
    assert resolve_config(1, "airplus", "journal_id", repo=repo) == 100


def test_resolve_config_falls_back_to_company_level():
    repo = FakeConfigRepo(
        values={
            (1, "", "journal_id"): 200,
            (0, "", "journal_id"): 300,
        }
    )
    assert resolve_config(1, "airplus", "journal_id", repo=repo) == 200


def test_resolve_config_falls_back_to_global():
    repo = FakeConfigRepo(values={(0, "", "journal_id"): 300})
    assert resolve_config(1, "airplus", "journal_id", repo=repo) == 300


def test_resolve_config_falls_back_to_default():
    repo = FakeConfigRepo(values={})
    assert resolve_config(1, "airplus", "journal_id", default="fallback", repo=repo) == (
        "fallback"
    )


def test_build_import_config_merges_by_priority():
    """Script-niveau wint van company-niveau wint van globaal — per sleutel,
    onafhankelijk (poort van `apps/main/app/api/imports.py::build_import_config`)."""
    repo = FakeConfigRepo(
        values={
            (1, "airplus", "airplus_purchase_journal_id"): 100,  # script-niveau
            (1, "", "airplus_purchase_journal_id"): 999,  # company-niveau (verliest)
            (1, "", "shared_key"): 42,  # company-niveau (wint, geen script-override)
            (0, "", "global_only_key"): "global-value",  # globaal
        }
    )
    config = build_import_config(
        1, "airplus", repo=repo, sql_connection_string="Driver=dummy;"
    )
    assert config["airplus_purchase_journal_id"] == 100
    assert config["shared_key"] == 42
    assert config["global_only_key"] == "global-value"
    # SQL-lookup-defaults blijven aanwezig (poort van de bron-defaults):
    assert config["sql_connection_string"] == "Driver=dummy;"
    assert config["sql_db_timeout"] == 30
    assert config["sql_db_query_timeout"] == 45
    assert config["sql_db_chunk_size"] == 200
    # Dode legacy-sleutels (geen enkele geporte plugin leest ze) zijn NIET
    # overgenomen — zie de kop-comment in config_resolve.py.
    assert "odoo_url" not in config
    assert "odoo_username" not in config
    assert "bsp_doc_types" not in config


def test_build_import_config_other_companies_scripts_excluded():
    repo = FakeConfigRepo(
        values={
            (2, "airplus", "journal_id"): 999,  # ander bedrijf — mag niet lekken
            (1, "bsp", "journal_id"): 888,  # ander script — mag niet lekken
        }
    )
    config = build_import_config(
        1, "airplus", repo=repo, sql_connection_string="Driver=dummy;"
    )
    assert "journal_id" not in config


def test_features_use_config_resolve():
    """Regressiebewijs voor de fase-3-consolidatie: vat_return/sbmov-services
    importeren `resolve_config` rechtstreeks uit `config_resolve` (de interim
    `shared/config_store.py` is verwijderd)."""
    import config_resolve
    import features.sbmov.service as sbmov_service
    import features.vat_return.service as vat_return_service

    assert vat_return_service.resolve_config is config_resolve.resolve_config
    assert sbmov_service.resolve_config is config_resolve.resolve_config
