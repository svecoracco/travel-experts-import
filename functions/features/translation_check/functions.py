"""HTTP-triggers voor analytic-account-vertaalconsistentie — poort van
`travel-experts-backend/apps/main/app/translation_check/routes.py` naar
Azure Functions (`auth_level=FUNCTION`, zie `docs/contracts.md` §2.7-2.8 + §4).

Admin-only (role-check gebeurt in Next.js vóór de server-side call — zie de
"Autorisatie"-alinea in `docs/contracts.md` §2; deze functie doet zelf geen
eigen user-auth/role-check, enkel de function-key op HTTP-niveau).
"""

from __future__ import annotations

import json
import logging

import azure.functions as func

from features.translation_check.service import apply_fixes, check_translations

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


@bp.function_name("TranslationCheckCheck")
@bp.route(
    route="translation-check/check",
    methods=["GET"],
    auth_level=func.AuthLevel.FUNCTION,
)
def check(req: func.HttpRequest) -> func.HttpResponse:
    """GET /translation-check/check — zie docs/contracts.md §2.7.

    Query: company_id (int, verplicht), plan_id (int, optioneel).
    """
    company_id_raw = req.params.get("company_id")
    plan_id_raw = req.params.get("plan_id")

    if not company_id_raw:
        return _error("company_id is required", 400)

    try:
        company_id = int(company_id_raw)
    except (TypeError, ValueError):
        return _error("company_id must be an integer", 400)

    plan_id = None
    if plan_id_raw:
        try:
            plan_id = int(plan_id_raw)
        except (TypeError, ValueError):
            return _error("plan_id must be an integer", 400)

    try:
        return _ok(check_translations(company_id, plan_id))
    except ValueError as e:
        return _error(str(e), 400)
    except RuntimeError as e:
        return _error(str(e), 500)
    except Exception as e:  # noqa: BLE001
        logger.exception("translation_check: unexpected error in check")
        return _error(f"Unexpected error: {e}", 500)


@bp.function_name("TranslationCheckFix")
@bp.route(
    route="translation-check/fix",
    methods=["POST"],
    auth_level=func.AuthLevel.FUNCTION,
)
def fix(req: func.HttpRequest) -> func.HttpResponse:
    """POST /translation-check/fix — zie docs/contracts.md §2.8.

    Body: { company_id: int, fixes: [{account_id, correct_name}, ...] }
    """
    try:
        data = req.get_json()
    except ValueError:
        data = {}

    company_id = data.get("company_id")
    fixes = data.get("fixes", [])

    if not company_id:
        return _error("company_id is required", 400)
    if not isinstance(fixes, list) or not fixes:
        return _error("fixes must be a non-empty list", 400)

    try:
        return _ok(apply_fixes(int(company_id), fixes))
    except ValueError as e:
        return _error(str(e), 400)
    except Exception as e:  # noqa: BLE001
        logger.exception("translation_check: unexpected error in fix")
        return _error(f"Unexpected error: {e}", 500)
