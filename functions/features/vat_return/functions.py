"""HTTP-triggers voor VAT Return — poort van
`travel-experts-backend/apps/main/app/vat_return/routes.py` (Flask-blueprint)
naar Azure Functions (`auth_level=FUNCTION`, zie `docs/contracts.md` §2.1-2.4
+ §4).

Foutvorm uniform: `{"error": "..."}` met 400/404/409/500 — zie contracts.md §2.
`RuntimeError` → 500, `ValueError` → 400, onverwachte fouten → 500.
"""

from __future__ import annotations

import json
import logging

import azure.functions as func

from features.vat_return.service import (
    book_correction_entry,
    check_existing_entry,
    dismiss_entry,
    fetch_vat_return_data,
)

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


@bp.function_name("VatReturnData")
@bp.route(
    route="vat-return/data", methods=["GET"], auth_level=func.AuthLevel.FUNCTION
)
def get_vat_return_data(req: func.HttpRequest) -> func.HttpResponse:
    """GET /vat-return/data — zie docs/contracts.md §2.1.

    Query: company_id (int, verplicht), period (string YYYY-MM, verplicht).
    """
    company_id_raw = req.params.get("company_id")
    period = req.params.get("period")

    if not company_id_raw:
        return _error("company_id is required", 400)
    if not period:
        return _error("period is required (YYYY-MM)", 400)

    try:
        company_id = int(company_id_raw)
    except (TypeError, ValueError):
        return _error("company_id must be an integer", 400)

    parts = period.split("-")
    if len(parts) != 2 or not parts[0].isdigit() or not parts[1].isdigit():
        return _error("period must be in YYYY-MM format", 400)
    month = int(parts[1])
    if month < 1 or month > 12:
        return _error("Invalid month in period", 400)

    try:
        result = fetch_vat_return_data(company_id, period)
        return _ok(result)
    except ValueError as e:
        return _error(str(e), 400)
    except RuntimeError as e:
        return _error(str(e), 500)
    except Exception as e:  # noqa: BLE001 - uniforme foutvorm, zie contracts.md §2
        logger.exception("vat_return: unexpected error in get_vat_return_data")
        return _error(f"Unexpected error: {e}", 500)


@bp.function_name("VatReturnCheck")
@bp.route(
    route="vat-return/check", methods=["GET"], auth_level=func.AuthLevel.FUNCTION
)
def check_vat_return(req: func.HttpRequest) -> func.HttpResponse:
    """GET /vat-return/check — zie docs/contracts.md §2.2.

    Query: company_id (int, verplicht), period (string, verplicht).
    """
    company_id_raw = req.params.get("company_id")
    period = req.params.get("period")

    if not company_id_raw:
        return _error("company_id is required", 400)
    if not period:
        return _error("period is required (YYYY-MM)", 400)

    try:
        company_id = int(company_id_raw)
    except (TypeError, ValueError):
        return _error("company_id must be an integer", 400)

    try:
        result = check_existing_entry(company_id, period)
        return _ok(result)
    except Exception as e:  # noqa: BLE001
        logger.exception("vat_return: unexpected error in check_vat_return")
        return _error(f"Unexpected error: {e}", 500)


@bp.function_name("VatReturnDismiss")
@bp.route(
    route="vat-return/dismiss", methods=["POST"], auth_level=func.AuthLevel.FUNCTION
)
def dismiss_vat_return(req: func.HttpRequest) -> func.HttpResponse:
    """POST /vat-return/dismiss — zie docs/contracts.md §2.3.

    Body: { company_id, period }.

    Contract-drift (zie eindrapport): het bevroren §2.3-contract kent geen
    `dismissed_by`-veld; hier wordt het als OPTIONEEL body-veld gelezen zodat
    de audittrail-attributie niet stilzwijgend verloren gaat totdat de
    orchestrator dit met Track B afstemt.
    """
    try:
        data = req.get_json()
    except ValueError:
        data = {}

    company_id = data.get("company_id")
    period = data.get("period")
    dismissed_by = data.get("dismissed_by")

    if not company_id:
        return _error("company_id is required", 400)
    if not period:
        return _error("period is required (YYYY-MM)", 400)

    try:
        result = dismiss_entry(int(company_id), period, dismissed_by)
        if "error" in result:
            return _ok(result, 404)
        return _ok(result)
    except Exception as e:  # noqa: BLE001
        logger.exception("vat_return: unexpected error in dismiss_vat_return")
        return _error(f"Unexpected error: {e}", 500)


@bp.function_name("VatReturnBook")
@bp.route(
    route="vat-return/book", methods=["POST"], auth_level=func.AuthLevel.FUNCTION
)
def book_vat_return(req: func.HttpRequest) -> func.HttpResponse:
    """POST /vat-return/book — zie docs/contracts.md §2.4.

    Body: {
        company_id, period,
        correction_lines: [{description, grid, amount, tag_id}],
        start_data: {vat_code: {grid: balance}}  (optioneel)
    }

    Contract-drift (zie eindrapport): `created_by` wordt als OPTIONEEL
    body-veld gelezen (zelfde reden als bij dismiss hierboven).
    """
    try:
        data = req.get_json()
    except ValueError:
        data = {}

    company_id = data.get("company_id")
    period = data.get("period")
    correction_lines = data.get("correction_lines", [])
    start_data = data.get("start_data")
    created_by = data.get("created_by")

    if not company_id:
        return _error("company_id is required", 400)
    if not period:
        return _error("period is required (YYYY-MM)", 400)
    if not correction_lines:
        return _error("correction_lines is required", 400)

    try:
        result = book_correction_entry(
            int(company_id), period, correction_lines, start_data, created_by
        )
        if "error" in result:
            return _ok(result, 409)
        return _ok(result)
    except ValueError as e:
        return _error(str(e), 400)
    except RuntimeError as e:
        return _error(str(e), 500)
    except Exception as e:  # noqa: BLE001
        logger.exception("vat_return: unexpected error in book_vat_return")
        return _error(f"Unexpected error: {e}", 500)
