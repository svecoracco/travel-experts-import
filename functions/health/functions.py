"""Triviale health/ping-trigger (Fase 1-scaffold).

Bewijst dat de Functions-host "leeg-maar-gezond" draait zonder het private
`odoo`-pakket te laden — dit bestand (en alles wat het importeert) bevat GEEN
`from odoo import ...`. Geen business-logica, geen DB-/Odoo-toegang.

Zie docs/contracts.md §2.9 voor de contract-vorm.
"""

from __future__ import annotations

import json

import azure.functions as func

bp = func.Blueprint()


@bp.function_name("Ping")
@bp.route(route="ping", methods=["GET"], auth_level=func.AuthLevel.FUNCTION)
def ping(req: func.HttpRequest) -> func.HttpResponse:
    """GET /ping — triviale health-check.

    Response 200: {"ok": true, "service": "travel-experts-import-functions"}
    """
    return func.HttpResponse(
        json.dumps({"ok": True, "service": "travel-experts-import-functions"}),
        status_code=200,
        mimetype="application/json",
    )
