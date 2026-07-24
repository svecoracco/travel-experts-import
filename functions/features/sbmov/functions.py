"""HTTP-triggers voor Self-billing Move — poort van
`travel-experts-backend/apps/main/app/sbmov/routes.py` naar Azure Functions
(`auth_level=FUNCTION`, zie `docs/contracts.md` §2.5-2.6 + §4).
"""

from __future__ import annotations

import json
import logging

import azure.functions as func

from features.sbmov.service import list_suppliers, move_partner_drafts

logger = logging.getLogger(__name__)

bp = func.Blueprint()


def _error(message: str, status_code: int) -> func.HttpResponse:
    return func.HttpResponse(
        json.dumps({"error": message}),
        status_code=status_code,
        mimetype="application/json",
    )


def _ok(payload: dict, status_code: int = 200) -> func.HttpResponse:
    return func.HttpResponse(
        json.dumps(payload),
        status_code=status_code,
        mimetype="application/json",
    )


@bp.function_name("SbmovSuppliers")
@bp.route(
    route="sbmov/suppliers", methods=["GET"], auth_level=func.AuthLevel.FUNCTION
)
def get_suppliers(req: func.HttpRequest) -> func.HttpResponse:
    """GET /sbmov/suppliers — zie docs/contracts.md §2.5.

    Query: company_id (int, verplicht).
    """
    company_id_raw = req.params.get("company_id")
    if not company_id_raw:
        return _error("company_id is required", 400)

    try:
        company_id = int(company_id_raw)
    except (TypeError, ValueError):
        return _error("company_id must be an integer", 400)

    try:
        result = list_suppliers(company_id)
        return _ok(result)
    except ValueError as e:
        return _error(str(e), 400)
    except RuntimeError as e:
        return _error(str(e), 500)
    except Exception as e:  # noqa: BLE001
        logger.exception("sbmov: unexpected error listing suppliers")
        return _error(f"Unexpected error: {e}", 500)


@bp.function_name("SbmovMove")
@bp.route(route="sbmov/move", methods=["POST"], auth_level=func.AuthLevel.FUNCTION)
def post_move(req: func.HttpRequest) -> func.HttpResponse:
    """POST /sbmov/move — zie docs/contracts.md §2.6.

    Body: { company_id: int, partner_id: int | null }.
    """
    try:
        data = req.get_json()
    except ValueError:
        data = {}

    company_id = data.get("company_id")
    if not company_id:
        return _error("company_id is required", 400)
    if "partner_id" not in data:
        return _error(
            "partner_id is required (use null for the no-partner bucket)", 400
        )

    partner_id = data.get("partner_id")
    if partner_id is not None:
        try:
            partner_id = int(partner_id)
        except (TypeError, ValueError):
            return _error("partner_id must be an integer or null", 400)

    try:
        result = move_partner_drafts(int(company_id), partner_id)
        return _ok(result)
    except ValueError as e:
        return _error(str(e), 400)
    except RuntimeError as e:
        return _error(str(e), 500)
    except Exception as e:  # noqa: BLE001
        logger.exception("sbmov: unexpected error moving drafts")
        return _error(f"Unexpected error: {e}", 500)
