"""Payload-parity-harness (fase 2.4).

Draait **oud** (`travel-experts-backend`, read-only bronrepo) en **nieuw**
(`functions/`) `build_moves()` op dezelfde BTS-achtige input (voor alle 8
plugins een minimale synthetische fixture — zie
`tests/fixtures/generate_fixtures.py`; voor `vivawallet` was een échte
sample-file beschikbaar, maar die bleek klant-PII te bevatten en is bewust
NIET gebruikt, zie de kop-comment in `generate_fixtures.py`) en diff't de
resulterende payloads (incl. `payment_reference`/`ref`). Injecteert overal een
`FakeOdooClient` (`tools/fake_odoo_client.py`) met canned lookups — draait
dus **volledig offline**; er wordt **nooit** naar een echte Odoo-server
gepost (harde projectregel #2), niet eens gelezen.

Gebruik:
    functions/.venv/Scripts/python.exe -m tools.parity_harness

Retourneert exit-code 0 als alle geregistreerde plugins pariteit hebben,
1 als er verschillen zijn.

Belangrijke opmerking (zie ook pyproject.toml per-file-ignore): dit bestand
moet de OUDE Flask-backend importeren om `build_moves()` van de broncode
rechtstreeks te kunnen aanroepen. `app.config.Config` (bronrepo) leest
`SQL_CONNECTION_STRING` op class-body-eval-tijd en zou zonder een (niet per se
geldige) waarde al bij import crashen — dit is een eenmalige bootstrap-vereiste
van de bronrepo's Flask-app-factory, losstaand van Track A's eigen
env-module/-regels (harde projectregel #8 geldt voor `functions/`-runtime-
config, niet voor het one-off importeren van de read-only bronrepo hier).
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

FUNCTIONS_DIR = Path(__file__).resolve().parent.parent
FIXTURES_DIR = FUNCTIONS_DIR / "tests" / "fixtures"
OLD_BACKEND_ROOT = Path(r"C:\github\travel-experts\travel-experts-backend")
OLD_BACKEND_MAIN = OLD_BACKEND_ROOT / "apps" / "main"


def _bootstrap_old_backend_import() -> None:
    """Zet sys.path + de minimale dummy-env zodat `import app.plugins...`
    (de OUDE bronrepo) lukt, zonder een echte SQL Server/Odoo-omgeving.
    Alleen nodig om de bron-`build_moves()` rechtstreeks aan te roepen ter
    vergelijking — er wordt nergens mee geschreven of verbonden."""
    main_str = str(OLD_BACKEND_MAIN)
    if main_str not in sys.path:
        sys.path.insert(0, main_str)
    # app.config.Config bouwt een SQLAlchemy-URL uit deze var op import-tijd
    # (geen echte connectie — puur URL-parsing). Zie de kop-comment hierboven.
    os.environ.setdefault("SQL_CONNECTION_STRING", "Driver=dummy;Server=dummy;")


from tools.fake_odoo_client import FakeOdooClient  # noqa: E402


@dataclass
class ParityCase:
    plugin: str
    fixture: Path
    config: dict[str, Any]
    company_id: int
    canned: dict[str, list[dict[str, Any]]]
    old_module: str
    old_class: str
    new_module: str
    new_class: str


def _base_canned() -> dict[str, list[dict[str, Any]]]:
    """Canned Odoo-records die door meerdere plugins gedeeld worden."""
    return {
        "account.analytic.plan": [{"id": 1, "name": "File number"}],
        "res.currency": [{"id": 1, "name": "EUR"}],
    }


def _cases() -> list[ParityCase]:
    cases: list[ParityCase] = []

    # --- airplus ---
    canned = _base_canned()
    canned["res.partner"] = [{"id": 10, "ref": "SUP001", "name": "Airplus Supplier"}]
    canned["account.account"] = [
        {"id": 20, "code": "604000", "name": "Travel costs"},
        {"id": 21, "code": "580000", "name": "Airplus payout"},
        {"id": 22, "code": "440000", "name": "Suppliers"},
    ]
    cases.append(
        ParityCase(
            plugin="airplus",
            fixture=FIXTURES_DIR / "airplus_sample.xlsx",
            config={
                "airplus_purchase_journal_id": 100,
                "airplus_payment_journal_id": 101,
                "airplus_payout_glaccount": 580000,
                "airplus_suppliers_glaccount": 440000,
            },
            company_id=1,
            canned=canned,
            old_module="app.plugins.airplus.plugin",
            old_class="AirplusPlugin",
            new_module="plugins.airplus.plugin",
            new_class="AirplusPlugin",
        )
    )

    # --- divers ---
    canned = _base_canned()
    canned["res.partner"] = [{"id": 30, "ref": "SUP002", "name": "Divers Supplier"}]
    canned["account.account"] = [{"id": 40, "code": "612000", "name": "Divers costs"}]
    cases.append(
        ParityCase(
            plugin="divers",
            fixture=FIXTURES_DIR / "divers_sample.xlsx",
            config={"divers_purchase_journal_id": 102},
            company_id=1,
            canned=canned,
            old_module="app.plugins.divers.plugin",
            old_class="DiversPlugin",
            new_module="plugins.divers.plugin",
            new_class="DiversPlugin",
        )
    )

    # --- commission ---
    canned = _base_canned()
    canned["res.partner"] = [{"id": 50, "ref": "COMMSUP", "name": "Commission Supplier NV"}]
    canned["account.account"] = [{"id": 60, "code": "613500", "name": "Commission costs"}]
    cases.append(
        ParityCase(
            plugin="commission",
            fixture=FIXTURES_DIR / "commission_sample.xlsx",
            config={
                "supplier_ref": "COMMSUP",
                "purchase_journal_id": 103,
                "line_account_id": 613500,
            },
            company_id=1,
            canned=canned,
            old_module="app.plugins.commission.plugin",
            old_class="CommissionPlugin",
            new_module="plugins.commission.plugin",
            new_class="CommissionPlugin",
        )
    )

    # --- rail ---
    canned = _base_canned()
    canned["res.partner"] = [{"id": 70, "ref": "RAILSUP", "name": "Rail Supplier"}]
    canned["account.account"] = [{"id": 80, "code": "604500", "name": "Rail costs"}]
    cases.append(
        ParityCase(
            plugin="rail",
            fixture=FIXTURES_DIR / "rail_sample.xlsx",
            config={
                "supplier_ref": "RAILSUP",
                "purchase_journal_id": 104,
                "line_account_id": 604500,
                # bts_table/bts_ticket_col/bts_dnr_col bewust leeg: skip de
                # (niet-Odoo) SQL Server-lookup volledig, offline-veilig.
            },
            company_id=1,
            canned=canned,
            old_module="app.plugins.rail.plugin",
            old_class="RailPlugin",
            new_module="plugins.rail.plugin",
            new_class="RailPlugin",
        )
    )

    # --- bsp ---
    canned = _base_canned()
    canned["account.account"] = [
        {"id": 90, "code": "604000", "name": "BSP line"},
        {"id": 91, "code": "440000", "name": "BSP clearing"},
        {"id": 92, "code": "580800", "name": "BSP cash"},
        {"id": 93, "code": "580100", "name": "BSP card VI2533"},
    ]
    cases.append(
        ParityCase(
            plugin="bsp",
            fixture=FIXTURES_DIR / "bsp_sample.csv",
            config={
                "partner_id": 1,
                "currency_id": 1,
                "journal_id": 105,
                "misc_journal_id": 106,
                "line_account_id": 604000,
                "misc_clearing_account_id": 440000,
                "cash_account_id": 580800,
                "card_suffix_map": {"2533": 580100},
                # bts_table/bts_ticket_col bewust leeg: skip de SQL Server-lookup.
            },
            company_id=1,
            canned=canned,
            old_module="app.plugins.bsp.plugin",
            old_class="BspPlugin",
            new_module="plugins.bsp.plugin",
            new_class="BspPlugin",
        )
    )

    # --- ibanfirst --- (geen Odoo-lookups nodig: geen FX-rij in de fixture)
    cases.append(
        ParityCase(
            plugin="ibanfirst",
            fixture=FIXTURES_DIR / "ibanfirst_sample.csv",
            config={"journal_map": {"EUR": 107}},
            company_id=1,
            canned=_base_canned(),
            old_module="app.plugins.ibanfirst.plugin",
            old_class="IbanFirstPlugin",
            new_module="plugins.ibanfirst.plugin",
            new_class="IbanFirstPlugin",
        )
    )

    # --- tui ---
    canned = _base_canned()
    canned["account.account"] = [
        {"id": 110, "code": "604100", "name": "TUI tickets"},
        {"id": 111, "code": "613000", "name": "TUI commission"},
    ]
    cases.append(
        ParityCase(
            plugin="tui",
            fixture=FIXTURES_DIR / "DOM_JET_GRP1_20260215_REF001_sample.csv",
            config={
                "tui_purchase_journal": 108,
                "tui_supplier_id": 120,
                "tui_glaccount_ticket": 604100,
                "tui_glaccount_comm": 613000,
                "tui_table": "unused_offline",
                "tui_ticket_col": "unused_offline",
                # sql_connection_string bewust leeg: skip de SQL Server-lookup.
            },
            company_id=1,
            canned=canned,
            old_module="app.plugins.tui.plugin",
            old_class="TuiPlugin",
            new_module="plugins.tui.plugin",
            new_class="TuiPlugin",
        )
    )

    # --- vivawallet --- (synthetische fixture, zie generate_fixtures.py)
    canned = _base_canned()
    canned["account.account"] = [
        {"id": 130, "code": "550100", "name": "Vivawallet"},
        {"id": 131, "code": "652000", "name": "Vivawallet costs"},
        {"id": 132, "code": "400000", "name": "Clients"},
        {"id": 133, "code": "499100", "name": "Suspense"},
    ]
    cases.append(
        ParityCase(
            plugin="vivawallet",
            fixture=FIXTURES_DIR / "vivawallet_sample.xlsx",
            config={
                "journal_id": 109,
                "account_vivawallet": 550100,
                "account_costs": 652000,
                "account_clients": 400000,
                "account_suspense": 499100,
            },
            company_id=1,
            canned=canned,
            old_module="app.plugins.vivawallet.plugin",
            old_class="VivawalletPlugin",
            new_module="plugins.vivawallet.plugin",
            new_class="VivawalletPlugin",
        )
    )

    return cases


def _import_class(module_name: str, class_name: str) -> type:
    import importlib

    module = importlib.import_module(module_name)
    return getattr(module, class_name)


def _normalize_payload(payload: dict) -> Any:
    """Maak een payload structureel vergelijkbaar (tuples/lists genormaliseerd)."""
    import json

    return json.loads(json.dumps(payload, default=str, sort_keys=True))


def diff_moves(old_moves: list, new_moves: list) -> list[str]:
    """Vergelijk twee lijsten MovePayload-achtige objecten (duck-typed:
    `.payload`/`.move_type`/`.ref`). Retourneert een lijst verschillen (leeg
    = pariteit)."""
    diffs: list[str] = []
    if len(old_moves) != len(new_moves):
        diffs.append(f"aantal moves verschilt: oud={len(old_moves)} nieuw={len(new_moves)}")

    for i, (om, nm) in enumerate(zip(old_moves, new_moves)):
        if om.move_type != nm.move_type:
            diffs.append(f"move[{i}].move_type: oud={om.move_type!r} nieuw={nm.move_type!r}")
        if om.ref != nm.ref:
            diffs.append(f"move[{i}].ref: oud={om.ref!r} nieuw={nm.ref!r}")
        old_p = _normalize_payload(om.payload)
        new_p = _normalize_payload(nm.payload)
        if old_p != new_p:
            diffs.append(f"move[{i}].payload verschilt:\n  oud={old_p}\n  nieuw={new_p}")

    return diffs


def run_case(case: ParityCase) -> list[str]:
    old_cls = _import_class(case.old_module, case.old_class)
    new_cls = _import_class(case.new_module, case.new_class)

    old_plugin = old_cls()
    new_plugin = new_cls()

    old_parsed = old_plugin.parse(case.fixture, case.config)
    new_parsed = new_plugin.parse(case.fixture, case.config)

    old_client = FakeOdooClient(case.canned)
    new_client = FakeOdooClient(case.canned)

    old_moves = old_plugin.build_moves(old_parsed, old_client, case.config, case.company_id)
    new_moves = new_plugin.build_moves(new_parsed, new_client, case.config, case.company_id)

    return diff_moves(old_moves, new_moves)


def main() -> int:
    _bootstrap_old_backend_import()

    cases = _cases()
    any_diff = False
    for case in cases:
        try:
            diffs = run_case(case)
        except Exception as exc:  # noqa: BLE001 - harnas moet per-plugin blijven doorlopen
            print(f"[{case.plugin}] FOUT tijdens vergelijking: {exc}")
            any_diff = True
            continue
        if diffs:
            any_diff = True
            print(f"[{case.plugin}] VERSCHIL(LEN):")
            for d in diffs:
                print(f"  - {d}")
        else:
            print(f"[{case.plugin}] OK — payload-pariteit bevestigd (0 verschillen)")

    return 1 if any_diff else 0


if __name__ == "__main__":
    raise SystemExit(main())
