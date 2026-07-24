"""functions/import_processor.py — queue-trigger die de import-pipeline
verwerkt (fase 3). Poort van
`travel-experts-backend/apps/main/app/jobs/runner.py::run_import_async`.

**Herontwerp t.o.v. de bron** (plan §Belangrijke bron → doel-verschillen #4,
docs/contracts.md §1/§3):

- Threading + in-memory `_progress`-store → Azure Storage Queue-trigger +
  `[{DB_SCHEMA}].[import_jobs]` als bron van waarheid. Elke `on_progress`-
  callback (`(phase, current, total, message)`, zie `plugins/base.py`)
  schrijft (throttled) naar `progress_phase/progress_current/progress_total/
  progress_message`; `status`/`started_at`/`completed_at`/`result_summary`/
  `skip_report_path` idem.
- Queue-payload is NIET de configbron (beslissing #2): alleen
  `{jobId, companyId, script, blobRef}` (camelCase, docs/contracts.md §1)
  komt van de queue; alles anders (plugin-config, `dry_run`,
  `accounting_date`, `original_entry_ref`, `blob_ref`, `file_name`) wordt via
  `jobId` uit `import_jobs` gelezen (nooit uit de payload — voorkomt drift bij
  herlevering).
- **Idempotentie/job-status-guard**: `status` gaat alleen `queued` → `running`
  als de huidige status nog `queued` is (atomische, geguarde `UPDATE ...
  WHERE status = 'queued'`). Is de status al `running`/`completed`/`failed`
  (queue-redelivery) → **skip**, geen herverwerking, geen dubbele boeking.
  `payment_reference`-idempotentie aan Odoo-zijde (fase 2, per plugin) blijft
  de secundaire/aanvullende guard.
- Bestand: `job.file_path` (lokale schijf) → `job.blob_ref` (Azure Blob-naam
  in dezelfde container als de CSV-blob-sync, `BLOB_CONTAINER_NAME` — zie
  `local.settings.template.json`). De queue-functie downloadt de blob naar een
  tijdelijk lokaal bestand (plugins verwachten een `pathlib.Path`, zie
  `plugins/*/plugin.py::validate_file/parse`) en ruimt dat na afloop op.
- Skip-report: de bron schreef naar lokale schijf (`UPLOAD_DIR`, een
  stateless Function-instance kent dat pad niet betrouwbaar bij een latere
  download). **Ontwerpbeslissing (te bevestigen op het sync-punt/Track B)**:
  het skip-report-Excel-bestand wordt naar dezelfde Blob-container geschreven
  (`skip-reports/{job_id}_skip_report.xlsx`) en die blob-naam in
  `skip_report_path` bewaard — zelfde patroon als `blob_ref`. Dit wijzigt de
  KOLOM niet (nog steeds een opaque `NVARCHAR(500)`-string,
  `docs/contracts.md` §3 legt de interpretatie niet vast), maar Track B moet
  dit weten bij het bouwen van de download-route (fase 5). Zie het
  fase-3-eindrapport.

**Offline-testbaarheid** (fase-2/3 "Offline / infra-constraint"): de DB-
toegang (`ImportJobRepo`), de Odoo-connectie (`get_odoo_client`), de
plugin-config (`build_import_config`) en de blob-download/skip-report-write
zijn allemaal dependency-injectable parameters van `run_import_job()` — tests
injecteren fakes (`tools.fake_odoo_client.FakeOdooClient`, een in-memory
`ImportJobRepo`, een download-fn die een lokale fixture teruggeeft) i.p.v.
echte Azure/SQL Server/Odoo-omgevingen. Alleen het *module-laden* zelf
(queue-trigger-registratie, zie onderaan) vereist — net als
`syncs/functions.py` (cron-schedules) — de volledige Track-A-env, omdat de
`queue_name` op decoratie-tijd uit `env.ENV` komt.
"""

from __future__ import annotations

import io
import json
import logging
import tempfile
import time
import traceback
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Callable, NotRequired, Protocol, TypedDict

import azure.functions as func

import odoo_conn
from config_resolve import build_import_config
from plugins import discover_plugins, get_plugin
from plugins.base import ImportPlugin, ProgressCallback

logger = logging.getLogger(__name__)

# Registreer de 8 plugins in de registry (`plugins.get_plugin(name)`) bij het
# laden van deze module — de bron-registry (`plugins/__init__.py`) wordt
# elders nergens aangeroepen (de parity-harness importeert de plugin-klassen
# rechtstreeks, buiten de registry om). Dit is de ENE plek in `functions/` die
# de queue-verwerking daadwerkelijk via de naam (`job.plugin_name`/
# `script`) laat dispatchen.
discover_plugins()


# ---------------------------------------------------------------------------
# Queue-message-payload (docs/contracts.md §1) — camelCase, Python-kant.
# ---------------------------------------------------------------------------


class ImportQueueMessage(TypedDict):
    jobId: int
    companyId: int
    script: str
    blobRef: str
    enqueuedAt: NotRequired[str]


# ---------------------------------------------------------------------------
# ImportJobRepo — dunne, mockbare DB-toegangslaag voor `import_jobs`
# (docs/contracts.md §3 — dezelfde velden die Track B via Prisma leest).
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ImportJobRow:
    id: int
    plugin_name: str
    company_id: int
    status: str
    file_name: str
    blob_ref: str
    dry_run: bool
    accounting_date: date | None
    original_entry_ref: str | None


class ImportJobRepo(Protocol):
    def get_job(self, job_id: int) -> ImportJobRow | None: ...

    def try_mark_running(self, job_id: int) -> bool:
        """Atomische job-status-guard: `queued` → `running`.

        Retourneert True als DEZE aanroep de transitie deed (dus: ga
        verwerken); False als de status al iets anders dan `queued` was
        (queue-redelivery of dubbele trigger) — de caller MOET dan skippen,
        geen enkele schrijfactie meer doen."""
        ...

    def update_progress(
        self, job_id: int, phase: str, current: int, total: int, message: str
    ) -> None: ...

    def mark_completed(
        self, job_id: int, result_summary: str, skip_report_path: str | None
    ) -> None: ...

    def mark_failed(self, job_id: int, result_summary: str, message: str) -> None: ...


def _utcnow_naive() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


class PyodbcImportJobRepo:
    """Default `ImportJobRepo`: raw SQL/pyodbc tegen
    `[{DB_SCHEMA}].[import_jobs]` (docs/contracts.md §6) — via
    `shared.sql_server.open_write_cursor` (zelfde pyodbc-connectiemechaniek
    als `config_resolve.PyodbcConfigRepo` en de ticket-lookups)."""

    def _write_cursor(self):
        from env import ENV
        from shared.sql_server import open_write_cursor

        return open_write_cursor(ENV.sql_connection_string), ENV.db_schema

    def get_job(self, job_id: int) -> ImportJobRow | None:
        cursor_cm, schema = self._write_cursor()
        with cursor_cm as cursor:
            cursor.execute(
                f"""
                SELECT id, plugin_name, company_id, status, file_name, blob_ref,
                       dry_run, accounting_date, original_entry_ref
                FROM [{schema}].[import_jobs]
                WHERE id = ?
                """,
                (job_id,),
            )
            row = cursor.fetchone()
            if row is None:
                return None
            (
                row_id,
                plugin_name,
                company_id,
                status,
                file_name,
                blob_ref,
                dry_run,
                accounting_date,
                original_entry_ref,
            ) = row
            return ImportJobRow(
                id=row_id,
                plugin_name=plugin_name,
                company_id=company_id,
                status=status,
                file_name=file_name,
                blob_ref=blob_ref,
                dry_run=bool(dry_run),
                accounting_date=accounting_date,
                original_entry_ref=original_entry_ref,
            )

    def try_mark_running(self, job_id: int) -> bool:
        cursor_cm, schema = self._write_cursor()
        now = _utcnow_naive()
        with cursor_cm as cursor:
            cursor.execute(
                f"""
                UPDATE [{schema}].[import_jobs]
                SET status = 'running', started_at = ?, updated_at = ?
                WHERE id = ? AND status = 'queued'
                """,
                (now, now, job_id),
            )
            return cursor.rowcount == 1

    def update_progress(
        self, job_id: int, phase: str, current: int, total: int, message: str
    ) -> None:
        cursor_cm, schema = self._write_cursor()
        with cursor_cm as cursor:
            cursor.execute(
                f"""
                UPDATE [{schema}].[import_jobs]
                SET progress_phase = ?, progress_current = ?, progress_total = ?,
                    progress_message = ?, updated_at = ?
                WHERE id = ?
                """,
                (phase, current, total, message, _utcnow_naive(), job_id),
            )

    def mark_completed(
        self, job_id: int, result_summary: str, skip_report_path: str | None
    ) -> None:
        cursor_cm, schema = self._write_cursor()
        now = _utcnow_naive()
        with cursor_cm as cursor:
            cursor.execute(
                f"""
                UPDATE [{schema}].[import_jobs]
                SET status = 'completed', completed_at = ?, updated_at = ?,
                    result_summary = ?, skip_report_path = ?,
                    progress_phase = 'done', progress_current = 0, progress_total = 0,
                    progress_message = 'Import completed'
                WHERE id = ?
                """,
                (now, now, result_summary, skip_report_path, job_id),
            )

    def mark_failed(self, job_id: int, result_summary: str, message: str) -> None:
        cursor_cm, schema = self._write_cursor()
        now = _utcnow_naive()
        with cursor_cm as cursor:
            cursor.execute(
                f"""
                UPDATE [{schema}].[import_jobs]
                SET status = 'failed', completed_at = ?, updated_at = ?,
                    result_summary = ?,
                    progress_phase = 'failed', progress_message = ?
                WHERE id = ?
                """,
                (now, now, result_summary, message, job_id),
            )


# ---------------------------------------------------------------------------
# Throttled progress-writer — "Throttle de DB-writes (niet elke rij)".
# ---------------------------------------------------------------------------


class _ThrottledProgressWriter:
    """Wrapt `ImportJobRepo.update_progress` als een `ProgressCallback`
    (`plugins/base.py`). Schrijft altijd bij een fase-wissel of wanneer
    `current >= total > 0` (een "klaar met deze fase"-signaal); anders
    hoogstens elke `min_interval_seconds`. Een falende progress-write mag de
    import zelf nooit laten crashen (best-effort telemetrie, geen
    correctheids-eis)."""

    def __init__(
        self,
        repo: ImportJobRepo,
        job_id: int,
        min_interval_seconds: float = 2.0,
    ) -> None:
        self._repo = repo
        self._job_id = job_id
        self._min_interval = min_interval_seconds
        self._last_write = 0.0
        self._last_phase: str | None = None

    def __call__(self, phase: str, current: int, total: int, message: str) -> None:
        now = time.monotonic()
        phase_changed = phase != self._last_phase
        reached_total = bool(total) and current >= total
        if (
            not phase_changed
            and not reached_total
            and (now - self._last_write) < self._min_interval
        ):
            return
        self._last_write = now
        self._last_phase = phase
        try:
            self._repo.update_progress(self._job_id, phase, current, total, message)
        except Exception:  # noqa: BLE001 - telemetrie mag de import niet breken
            logger.warning(
                "import_processor: kon progress niet wegschrijven voor job %s",
                self._job_id,
                exc_info=True,
            )


# ---------------------------------------------------------------------------
# Blob-download (upload-bestand) + skip-report-upload — zie kop-comment.
# ---------------------------------------------------------------------------


def _download_blob_to_temp(blob_ref: str) -> Path:
    from env import ENV
    from syncs.azure_blob import get_blob_container_client, get_blob_service_client

    service_client = get_blob_service_client()
    container_client = get_blob_container_client(service_client, ENV.blob_container_name)
    blob_client = container_client.get_blob_client(blob_ref)

    suffix = Path(blob_ref).suffix
    fd, tmp_name = tempfile.mkstemp(suffix=suffix)
    tmp_path = Path(tmp_name)
    with open(fd, "wb") as fh:
        download = blob_client.download_blob()
        download.readinto(fh)
    return tmp_path


def _write_skip_report_to_blob(
    job_id: int,
    rows: list[dict],
    extra_data: dict | None = None,
) -> str | None:
    """Schrijf het skip-report-Excel-bestand naar de gedeelde blob-container
    i.p.v. lokale schijf (zie kop-comment — ontwerpbeslissing, sync-punt met
    Track B nodig). Faalt de write, dan gaat de import zelf NIET onderuit
    (matcht het "don't fail the import" -gedrag van de bron)."""
    try:
        import pandas as pd

        from env import ENV
        from syncs.azure_blob import get_blob_container_client, get_blob_service_client

        buffer = io.BytesIO()
        with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
            df = pd.DataFrame(rows)
            df.to_excel(writer, index=False, sheet_name="Skip Report")

            if extra_data:
                for sheet_name, sheet_rows in extra_data.items():
                    if sheet_rows:
                        edf = pd.DataFrame(sheet_rows)
                        safe_name = sheet_name[:31]  # Excel-sheetnaam max 31 tekens
                        edf.to_excel(writer, index=False, sheet_name=safe_name)

        blob_name = f"skip-reports/{job_id}_skip_report.xlsx"
        service_client = get_blob_service_client()
        container_client = get_blob_container_client(service_client, ENV.blob_container_name)
        container_client.upload_blob(name=blob_name, data=buffer.getvalue(), overwrite=True)
        return blob_name
    except Exception:  # noqa: BLE001 - zie kop-comment, matcht bron-gedrag
        logger.exception(
            "import_processor: kon skip-report niet wegschrijven voor job %s", job_id
        )
        return None


# ---------------------------------------------------------------------------
# Pipeline: validate → parse → connect → build_moves → execute → skip-report.
# ---------------------------------------------------------------------------


def run_import_job(
    msg: ImportQueueMessage,
    *,
    job_repo: ImportJobRepo | None = None,
    get_odoo_client: Callable[[], Any] | None = None,
    get_plugin_fn: Callable[[str], ImportPlugin] | None = None,
    build_config_fn: Callable[..., dict[str, Any]] | None = None,
    download_file_fn: Callable[[str], Path] | None = None,
    write_skip_report_fn: Callable[[int, list[dict], dict | None], str | None]
    | None = None,
    cleanup_downloaded_file: bool = True,
) -> None:
    """Verwerk één import-job. Poort van `runner.py::run_import_async`
    (pipeline ongewijzigd: validate → parse → connect → build_moves →
    execute → skip-report), herontworpen voor de queue + `import_jobs`
    (zie kop-comment).

    Alle DB-/Odoo-/blob-toegang is dependency-injectable — zie de
    "Offline-testbaarheid"-alinea in de kop-comment.
    """
    job_repo = job_repo or PyodbcImportJobRepo()
    get_odoo_client = get_odoo_client or odoo_conn.get_client
    get_plugin_fn = get_plugin_fn or get_plugin
    build_config_fn = build_config_fn or build_import_config
    download_file_fn = download_file_fn or _download_blob_to_temp
    write_skip_report_fn = write_skip_report_fn or _write_skip_report_to_blob

    job_id = msg["jobId"]

    job = job_repo.get_job(job_id)
    if job is None:
        logger.error("import_processor: job %s bestaat niet — skip", job_id)
        return

    # Job-status-guard (idempotentie/herlevering, docs/contracts.md §1/§3):
    # alleen verwerken als DEZE aanroep de queued -> running-transitie deed.
    if not job_repo.try_mark_running(job_id):
        logger.info(
            "import_processor: job %s heeft status %r (niet 'queued') — "
            "skip (queue-redelivery-guard)",
            job_id,
            job.status,
        )
        return

    on_progress: ProgressCallback = _ThrottledProgressWriter(job_repo, job_id)
    on_progress("starting", 0, 0, "Initialising...")

    downloaded_path: Path | None = None
    try:
        plugin = get_plugin_fn(job.plugin_name)
        config = build_config_fn(job.company_id, job.plugin_name)

        # Injecteer per-job-opties in de config (zoals de bron) — deze komen
        # uit `import_jobs` (via `jobId`), NIET uit de queue-payload.
        if job.accounting_date:
            config["accounting_date"] = job.accounting_date.isoformat()
        if job.original_entry_ref:
            config["original_entry_ref"] = job.original_entry_ref

        # 1. Validate
        on_progress("validating", 0, 0, "Validating file...")
        downloaded_path = download_file_fn(job.blob_ref)
        validation = plugin.validate_file(downloaded_path)
        if not validation.valid:
            result = {
                "error": "File validation failed",
                "validation_errors": validation.errors,
            }
            job_repo.mark_failed(job_id, json.dumps(result), "Validation failed")
            return

        # 2. Parse
        on_progress("parsing", 0, 0, "Parsing file...")
        parsed = plugin.parse(downloaded_path, config)

        # 3. Connect to Odoo
        on_progress("connecting", 0, 0, "Connecting to Odoo...")
        odoo_client = get_odoo_client()

        # 4. Build moves
        on_progress("building", 0, 0, "Building moves...")
        moves = plugin.build_moves(
            parsed, odoo_client, config, job.company_id, on_progress=on_progress
        )

        # 5. Execute
        on_progress("executing", 0, len(moves), "Executing...")
        result = plugin.execute(
            moves,
            odoo_client,
            job.company_id,
            dry_run=job.dry_run,
            auto_post=True,
            auto_reconcile=True,
            on_progress=on_progress,
        )

        # 6. Skip-report (indien nodig)
        skip_report_path = None
        if result.skip_report_rows or result.extra_report_data:
            skip_report_path = write_skip_report_fn(
                job_id, result.skip_report_rows, result.extra_report_data or None
            )

        # 7. Persist het resultaat
        needs_review = len(result.extra_report_data.get("Needs Review", []))
        result_dict = {
            "created": result.created,
            "skipped": result.skipped,
            "errors": result.errors,
            "needs_review": needs_review,
            "items_processed": result.items_processed,
            "log_messages": result.log_messages,
        }
        job_repo.mark_completed(job_id, json.dumps(result_dict), skip_report_path)
        logger.info(
            "Import job %s completed: created=%s skipped=%s errors=%s",
            job_id,
            result.created,
            result.skipped,
            result.errors,
        )

    except Exception as exc:  # noqa: BLE001 - matcht de brede except in de bron
        logger.exception("Import job %s failed", job_id)
        result = {"error": str(exc), "traceback": traceback.format_exc()}
        try:
            job_repo.mark_failed(job_id, json.dumps(result), str(exc))
        except Exception:  # noqa: BLE001
            logger.exception(
                "import_processor: kon de fout niet persisteren voor job %s", job_id
            )
    finally:
        if cleanup_downloaded_file and downloaded_path is not None:
            try:
                downloaded_path.unlink(missing_ok=True)
            except Exception:  # noqa: BLE001 - opruimen mag nooit de job breken
                pass


# ---------------------------------------------------------------------------
# Queue-trigger-registratie (Blueprint, patroon zoals de fase-2-Blueprints).
# ---------------------------------------------------------------------------

# Na de offline-testbare pipeline-code hierboven, exact zoals
# `syncs/functions.py` (cron-schedules): de queue-naam wordt op decoratie-tijd
# bepaald, dus het LADEN van deze module vereist de volledige Track-A-env
# (zie ook `function_app.py`-kop-comment).
from env import ENV  # noqa: E402

_QUEUE_NAME = ENV.azure_queue_import_jobs_name or "import-jobs"

# Naam van de app-setting(-groep) die de queue-trigger-binding gebruikt om
# met Azure Storage Queues te verbinden. Dit is een DECLARATIEVE binding: de
# Functions-host (niet onze Python-code) leest de bijbehorende app-setting(s)
# rechtstreeks — vandaar geen `env.ENV`-veld hiervoor (zie de env.py-
# kop-comment bij `azure_queue_import_jobs_name`). "AzureWebJobsStorage" is
# hier bewust hergebruikt (al aanwezig in `local.settings.template.json` voor
# lokale Azurite-tests, zie de "func start"-verificatie in het
# fase-3-eindrapport); OF de mens/gate zet in productie een eigen
# `AzureWebJobsStorage`-App-Setting die WEL naar dezelfde gedeelde
# storage-account wijst als web's `AZURE_QUEUE_ACCOUNT_URL`/
# `AZURE_QUEUE_ACCOUNT_CREDENTIAL` (docs/contracts.md §1) — zie het
# fase-3-eindrapport voor deze open infra-beslissing.
_QUEUE_CONNECTION_SETTING = "AzureWebJobsStorage"

bp = func.Blueprint()


@bp.function_name("ImportProcessor")
@bp.queue_trigger(
    arg_name="msg",
    queue_name=_QUEUE_NAME,
    connection=_QUEUE_CONNECTION_SETTING,
)
def process_import_job(msg: func.QueueMessage) -> None:
    """Queue-trigger-entrypoint — zie docs/contracts.md §1.

    Fouten bij het parsen van het bericht zelf (corrupt JSON, ontbrekende
    verplichte velden) worden bewust NIET hier gevangen: die laten we
    doorstromen naar het Functions-platform, dat de standaard
    retry-/poison-queue-afhandeling toepast (`maxDequeueCount` in
    `host.json`, default 5) — er is dan geen `jobId` om een `failed`-status
    op te schrijven. Fouten NA succesvolle payload-parsing (dus binnen de
    eigenlijke import-pipeline) worden wel gevangen door `run_import_job()`
    en resulteren in `status='failed'` op de job-rij zelf.
    """
    body = msg.get_body().decode("utf-8")
    payload: ImportQueueMessage = json.loads(body)
    run_import_job(payload)
