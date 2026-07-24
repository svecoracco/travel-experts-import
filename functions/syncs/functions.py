"""Timer- + HTTP-triggers voor de syncs — poort van
`travel-experts-backend/apps/syncs/functions.py` (1-op-1; al pakket-
gebaseerd in de bron). Cron-schedule-overrides komen uit `env.ENV`
(optionele velden, zie `env.py`) i.p.v. rechtstreeks `os.getenv(...)`.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from zoneinfo import ZoneInfo

import azure.functions as func

from env import ENV
from syncs.csv_blob_sync import (
    retry_failed_csv_blob_files,
    sync_csv_blob_files_to_db,
    sync_single_csv_blob_file_to_db,
)
from syncs.odoo_sync import odoo_sync_settings

bp = func.Blueprint()


def _local_hour_to_utc_cron(local_hour: int, tz_name: str = "Europe/Brussels") -> str:
    """Zet een lokaal uur om naar een UTC-cron-expressie (dagelijks op dat uur).

    Gebruikt de huidige UTC-offset voor de gegeven tijdzone zodat het schema
    correct is ongeacht zomer-/wintertijd bij app-startup.
    """
    now = datetime.now(ZoneInfo(tz_name))
    utc_offset_hours = int(now.utcoffset().total_seconds() // 3600)
    utc_hour = (local_hour - utc_offset_hours) % 24
    return f"0 0 {utc_hour} * * *"


# =========================================
# Odoo-settings-sync
# =========================================
ODOO_SETTINGS_CRON_SCHEDULE = ENV.odoo_settings_cron_schedule or _local_hour_to_utc_cron(
    1
)


@bp.function_name("SyncOdooSettingsCron")
@bp.timer_trigger(
    arg_name="timer",
    schedule=ODOO_SETTINGS_CRON_SCHEDULE,
    run_on_startup=False,
)
def sync_odoo_settings_cron(timer: func.TimerRequest):
    """Timer-trigger die dagelijks om 1:00 UTC (default) de Odoo-settings
    synchroniseert naar de SQL-database."""
    logging.info("Python timer trigger function started")
    odoo_sync_settings()
    logging.info("Odoo settings synced to SQL database successfully")
    logging.info("Python timer trigger function finished processing")


# =========================================
# CSV-blob-bestanden-sync
# =========================================
CSV_BLOB_FILES_CRON_SCHEDULE = ENV.csv_blob_files_cron_schedule or (
    _local_hour_to_utc_cron(2)
)
CSV_BLOB_FILES_RETRY_CRON_SCHEDULE = ENV.csv_blob_files_retry_cron_schedule or (
    _local_hour_to_utc_cron(5)
)
SYNC_SINGLE_CSV_BLOB_ROUTE = "sync/csv-blob"


@bp.function_name("SyncCsvBlobFilesCron")
@bp.timer_trigger(
    arg_name="timer",
    schedule=CSV_BLOB_FILES_CRON_SCHEDULE,
    run_on_startup=False,
)
def sync_csv_blob_files_cron(timer: func.TimerRequest):
    """Timer-trigger die dagelijks op het geschema-tijdstip CSV-blob-bestanden
    synchroniseert van Azure Blob Storage naar de SQL-database."""
    logging.info("Python timer trigger function started")
    sync_csv_blob_files_to_db()
    logging.info("CSV blob files synced to SQL database successfully")
    logging.info("Python timer trigger function finished processing")


@bp.function_name("RetryCsvBlobFilesCron")
@bp.timer_trigger(
    arg_name="timer",
    schedule=CSV_BLOB_FILES_RETRY_CRON_SCHEDULE,
    run_on_startup=False,
)
def retry_csv_blob_files_cron(timer: func.TimerRequest):
    """Timer-trigger die dagelijks om 5:00 UTC (default) faalde CSV-blob-
    bestanden herprobeert die tijdens de initiële sync om 2:00 mislukten."""
    logging.info("Python timer trigger function started (retry failed blobs)")
    retry_failed_csv_blob_files()
    logging.info("Retry of failed CSV blob files completed")
    logging.info("Python timer trigger function finished processing")


@bp.function_name("SyncSingleCsvBlobHttp")
@bp.route(
    route=SYNC_SINGLE_CSV_BLOB_ROUTE,
    methods=["GET"],
    auth_level=func.AuthLevel.FUNCTION,
)
def sync_single_csv_blob_http(req: func.HttpRequest) -> func.HttpResponse:
    """HTTP-trigger om precies één CSV-blob-bestand te synchroniseren naar de database.

    Query-params:
    - blobName (voorkeur) of name: het volledige blob-pad/naam binnen de container

    Voorbeeldverzoek:
    GET /sync/csv-blob?blobName=path/to/blobfile.csv

    Response:
    - 200 OK: {"ok": true, "blobName": "path/to/blobfile.csv"}
    - 400 Bad Request: {"ok": false, "error": "Error message"}
    - 500 Internal Server Error: {"ok": false, "blobName": "...",
      "error": "Internal error while syncing blob"}
    """

    blob_name = req.params.get("blobName") or req.params.get("name")
    if not blob_name:
        return func.HttpResponse(
            json.dumps(
                {
                    "ok": False,
                    "error": "Missing required query parameter: blobName (or name)",
                }
            ),
            status_code=400,
            mimetype="application/json",
        )

    try:
        blob_name = blob_name.strip().replace(
            "_", "/"
        )  # Sta underscores toe i.p.v. slashes in de blob-naam voor eenvoudigere URL-encoding
        logging.info(f"Received request to sync single CSV blob file: {blob_name}")
        sync_single_csv_blob_file_to_db(blob_name)
        return func.HttpResponse(
            json.dumps({"ok": True, "blobName": blob_name}),
            status_code=200,
            mimetype="application/json",
        )
    except ValueError as e:
        return func.HttpResponse(
            json.dumps({"ok": False, "blobName": blob_name, "error": str(e)}),
            status_code=400,
            mimetype="application/json",
        )
    except Exception:
        logging.exception("Error syncing single blob")
        return func.HttpResponse(
            json.dumps(
                {
                    "ok": False,
                    "blobName": blob_name,
                    "error": "Internal error while syncing blob",
                }
            ),
            status_code=500,
            mimetype="application/json",
        )
