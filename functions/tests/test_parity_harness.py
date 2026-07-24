"""Pytest-wrapper rond de payload-parity-harness (fase 2.4).

Draait volledig offline (FakeOdooClient, canned lookups) — zie
`tools/parity_harness.py` en `tools/fake_odoo_client.py`. **Nooit** een
verbinding naar een echte Odoo-server (harde projectregel #2).

Vereist wél leestoegang tot de read-only bronrepo
(`C:\\github\\travel-experts\\travel-experts-backend`) om de OUDE
`build_moves()` rechtstreeks te kunnen aanroepen ter vergelijking — buiten
deze specifieke machine/sessie (bv. in een generieke CI-runner zonder die
bronrepo-checkout) worden deze tests netjes geskipt in plaats van te falen.
"""

from __future__ import annotations

import pytest

from tools.parity_harness import OLD_BACKEND_MAIN, _bootstrap_old_backend_import, _cases, run_case

pytestmark = pytest.mark.skipif(
    not OLD_BACKEND_MAIN.exists(),
    reason=(
        "read-only bronrepo travel-experts-backend niet gevonden op dit pad "
        "— parity-vergelijking tegen de oude build_moves() is hier niet mogelijk"
    ),
)


@pytest.fixture(scope="module", autouse=True)
def _bootstrap():
    _bootstrap_old_backend_import()


@pytest.mark.parametrize("case", _cases(), ids=lambda c: c.plugin)
def test_plugin_payload_parity(case):
    diffs = run_case(case)
    assert diffs == [], f"[{case.plugin}] payload-verschillen tussen oud en nieuw:\n" + "\n".join(diffs)
