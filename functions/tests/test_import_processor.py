"""Unit-tests voor `import_processor.py` (fase 3) — volledig offline.

`import_processor.py` leest `env.ENV.azure_queue_import_jobs_name` op
MODULE-laadtijd (exact zoals `syncs/functions.py` de cron-schedules leest,
zie de kop-comment in `import_processor.py`), dus deze tests herladen de
module NA het zetten van een volledige dummy-Track-A-env via
`monkeypatch.setenv` — zelfde patroon als `tests/test_env.py`.

Test expliciet (zie de fase-3-opdracht "Offline / infra-constraint"):
job-status-guard (skip bij niet-`queued`), progress-mapping (throttled
writes), plugin-dispatch, fout → `failed`.
"""

from __future__ import annotations

import importlib
import sys
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from plugins.base import (
    ExecutionResult,
    ImportPlugin,
    MovePayload,
    ParsedData,
    PluginMeta,
    ValidationResult,
)
from tools.fake_odoo_client import FakeOdooClient

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

FIXTURE_PATH = Path(__file__).resolve().parent / "fixtures" / "airplus_sample.xlsx"


def _import_processor_module(monkeypatch, azure_queue_import_jobs_name: str | None = None):
    sys.modules.pop("import_processor", None)
    sys.modules.pop("env", None)
    for key, value in _REQUIRED_VARS.items():
        monkeypatch.setenv(key, value)
    if azure_queue_import_jobs_name is None:
        monkeypatch.delenv("AZURE_QUEUE_IMPORT_JOBS_NAME", raising=False)
    else:
        monkeypatch.setenv("AZURE_QUEUE_IMPORT_JOBS_NAME", azure_queue_import_jobs_name)
    return importlib.import_module("import_processor")


# ---------------------------------------------------------------------------
# Queue-naam-default (docs/contracts.md §1: "default import-jobs")
# ---------------------------------------------------------------------------


def test_queue_name_defaults_to_import_jobs(monkeypatch):
    mod = _import_processor_module(monkeypatch)
    assert mod._QUEUE_NAME == "import-jobs"


def test_queue_name_reads_env_override(monkeypatch):
    mod = _import_processor_module(monkeypatch, azure_queue_import_jobs_name="custom-jobs")
    assert mod._QUEUE_NAME == "custom-jobs"


# ---------------------------------------------------------------------------
# Fakes voor de pipeline-tests
# ---------------------------------------------------------------------------


class _FakePlugin(ImportPlugin):
    def __init__(
        self,
        moves: list[MovePayload] | None = None,
        validation_errors: list[str] | None = None,
        fail_in: str | None = None,
    ) -> None:
        self._moves = moves if moves is not None else []
        self._validation_errors = validation_errors or []
        self._fail_in = fail_in  # 'build_moves' of 'execute'

    def get_meta(self) -> PluginMeta:
        return PluginMeta(name="fake", display_name="Fake", accepted_extensions=[".xlsx"])

    def validate_file(self, file_path: Path) -> ValidationResult:
        if self._validation_errors:
            return ValidationResult(valid=False, errors=self._validation_errors)
        return ValidationResult(valid=True)

    def parse(self, file_path: Path, config: dict) -> ParsedData:
        return ParsedData(items=[{"a": 1}])

    def build_moves(self, parsed, odoo_client, config, company_id, on_progress=None):
        if on_progress:
            on_progress("building", 1, 1, "built")
        if self._fail_in == "build_moves":
            raise RuntimeError("boom during build_moves")
        return self._moves

    def execute(
        self,
        moves,
        odoo_client,
        company_id,
        dry_run=False,
        auto_post=True,
        auto_reconcile=True,
        on_progress=None,
    ) -> ExecutionResult:
        if self._fail_in == "execute":
            raise RuntimeError("boom during execute")
        if on_progress:
            on_progress("executing", len(moves), len(moves), "done")
        return ExecutionResult(
            created=len(moves), skipped=0, errors=0, items_processed=len(moves)
        )


@dataclass
class _FakeImportJobRepo:
    """In-memory dubbelganger van `ImportJobRepo` — bewaart status in een
    muteerbare dict zodat de job-status-guard end-to-end getest kan worden."""

    job: object
    initial_status: str = "queued"
    _status: str = field(init=False, default="")
    progress_calls: list = field(default_factory=list)
    completed_calls: list = field(default_factory=list)
    failed_calls: list = field(default_factory=list)

    def __post_init__(self):
        self._status = self.initial_status

    def get_job(self, job_id):
        return self.job if job_id == self.job.id else None

    def try_mark_running(self, job_id):
        if self._status != "queued":
            return False
        self._status = "running"
        return True

    def update_progress(self, job_id, phase, current, total, message):
        self.progress_calls.append((phase, current, total, message))

    def mark_completed(self, job_id, result_summary, skip_report_path):
        self._status = "completed"
        self.completed_calls.append((result_summary, skip_report_path))

    def mark_failed(self, job_id, result_summary, message):
        self._status = "failed"
        self.failed_calls.append((result_summary, message))


def _make_job_row(mod, **overrides):
    defaults = dict(
        id=1,
        plugin_name="fake",
        company_id=1,
        status="queued",
        file_name="sample.xlsx",
        blob_ref="uploads/sample.xlsx",
        dry_run=False,
        accounting_date=None,
        original_entry_ref=None,
    )
    defaults.update(overrides)
    return mod.ImportJobRow(**defaults)


# ---------------------------------------------------------------------------
# Job-status-guard
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("existing_status", ["running", "completed", "failed"])
def test_run_import_job_skips_when_status_not_queued(monkeypatch, existing_status):
    mod = _import_processor_module(monkeypatch)
    job = _make_job_row(mod)
    repo = _FakeImportJobRepo(job=job, initial_status=existing_status)

    mod.run_import_job(
        {"jobId": 1, "companyId": 1, "script": "fake", "blobRef": "uploads/sample.xlsx"},
        job_repo=repo,
        get_odoo_client=lambda: FakeOdooClient(),
        get_plugin_fn=lambda name: _FakePlugin(),
        build_config_fn=lambda company_id, plugin_name: {},
        download_file_fn=lambda blob_ref: FIXTURE_PATH,
        write_skip_report_fn=lambda job_id, rows, extra: None,
        cleanup_downloaded_file=False,
    )

    assert repo._status == existing_status  # ongewijzigd — geen herverwerking
    assert repo.progress_calls == []
    assert repo.completed_calls == []
    assert repo.failed_calls == []


def test_run_import_job_skips_when_job_not_found(monkeypatch):
    mod = _import_processor_module(monkeypatch)
    job = _make_job_row(mod)
    repo = _FakeImportJobRepo(job=job, initial_status="queued")

    mod.run_import_job(
        {"jobId": 999, "companyId": 1, "script": "fake", "blobRef": "x"},
        job_repo=repo,
        get_odoo_client=lambda: FakeOdooClient(),
        get_plugin_fn=lambda name: _FakePlugin(),
        build_config_fn=lambda company_id, plugin_name: {},
        download_file_fn=lambda blob_ref: FIXTURE_PATH,
        write_skip_report_fn=lambda job_id, rows, extra: None,
        cleanup_downloaded_file=False,
    )

    assert repo._status == "queued"  # nooit aangeraakt


# ---------------------------------------------------------------------------
# Progress-mapping + plugin-dispatch (gelukkig pad)
# ---------------------------------------------------------------------------


def test_run_import_job_success_writes_progress_and_completes(monkeypatch):
    mod = _import_processor_module(monkeypatch)
    job = _make_job_row(mod)
    repo = _FakeImportJobRepo(job=job, initial_status="queued")

    dispatched_plugin_names: list[str] = []

    def _get_plugin_fn(name: str) -> ImportPlugin:
        dispatched_plugin_names.append(name)
        return _FakePlugin(moves=[MovePayload(payload={}, move_type="entry", ref="R1")])

    mod.run_import_job(
        {"jobId": 1, "companyId": 1, "script": "fake", "blobRef": "uploads/sample.xlsx"},
        job_repo=repo,
        get_odoo_client=lambda: FakeOdooClient(),
        get_plugin_fn=_get_plugin_fn,
        build_config_fn=lambda company_id, plugin_name: {"company_id": company_id},
        download_file_fn=lambda blob_ref: FIXTURE_PATH,
        write_skip_report_fn=lambda job_id, rows, extra: None,
        cleanup_downloaded_file=False,
    )

    assert dispatched_plugin_names == ["fake"]  # plugin-dispatch op job.plugin_name
    assert repo._status == "completed"
    assert len(repo.completed_calls) == 1
    result_summary, skip_report_path = repo.completed_calls[0]
    assert '"created": 1' in result_summary
    assert skip_report_path is None

    # Progress-mapping: elke fase-transitie moet doorkomen (throttling
    # onderdrukt alleen *herhaalde* schrijfacties binnen dezelfde fase — zie
    # test_throttled_progress_writer_* hieronder). De `_FakePlugin` doet zelf
    # ook een `on_progress("building"/"executing", ...)`-call (net als de
    # échte plugins) — die komt door omdat `current >= total` (zie
    # `test_throttled_progress_writer_always_flushes_when_total_reached`),
    # dus "building"/"executing" mogen twee keer voorkomen.
    phases = [call[0] for call in repo.progress_calls]
    assert phases[:4] == ["starting", "validating", "parsing", "connecting"]
    assert "building" in phases
    assert "executing" in phases
    assert phases.index("building") < phases.index("executing")


def test_run_import_job_validation_failure_marks_failed(monkeypatch):
    mod = _import_processor_module(monkeypatch)
    job = _make_job_row(mod)
    repo = _FakeImportJobRepo(job=job, initial_status="queued")

    mod.run_import_job(
        {"jobId": 1, "companyId": 1, "script": "fake", "blobRef": "uploads/sample.xlsx"},
        job_repo=repo,
        get_odoo_client=lambda: FakeOdooClient(),
        get_plugin_fn=lambda name: _FakePlugin(validation_errors=["bad file"]),
        build_config_fn=lambda company_id, plugin_name: {},
        download_file_fn=lambda blob_ref: FIXTURE_PATH,
        write_skip_report_fn=lambda job_id, rows, extra: None,
        cleanup_downloaded_file=False,
    )

    assert repo._status == "failed"
    assert len(repo.failed_calls) == 1
    result_summary, message = repo.failed_calls[0]
    assert "File validation failed" in result_summary
    assert message == "Validation failed"


@pytest.mark.parametrize("fail_in", ["build_moves", "execute"])
def test_run_import_job_pipeline_exception_marks_failed(monkeypatch, fail_in):
    mod = _import_processor_module(monkeypatch)
    job = _make_job_row(mod)
    repo = _FakeImportJobRepo(job=job, initial_status="queued")

    mod.run_import_job(
        {"jobId": 1, "companyId": 1, "script": "fake", "blobRef": "uploads/sample.xlsx"},
        job_repo=repo,
        get_odoo_client=lambda: FakeOdooClient(),
        get_plugin_fn=lambda name: _FakePlugin(fail_in=fail_in),
        build_config_fn=lambda company_id, plugin_name: {},
        download_file_fn=lambda blob_ref: FIXTURE_PATH,
        write_skip_report_fn=lambda job_id, rows, extra: None,
        cleanup_downloaded_file=False,
    )

    assert repo._status == "failed"
    assert len(repo.failed_calls) == 1
    result_summary, message = repo.failed_calls[0]
    assert "boom" in message
    assert "boom" in result_summary


def test_run_import_job_injects_accounting_date_and_original_entry_ref(monkeypatch):
    """Per-job-opties komen uit `import_jobs` (via `jobId`), NIET uit de
    queue-payload — docs/contracts.md §1."""
    import datetime

    mod = _import_processor_module(monkeypatch)
    job = _make_job_row(
        mod,
        accounting_date=datetime.date(2026, 2, 15),
        original_entry_ref="ORIG-42",
    )
    repo = _FakeImportJobRepo(job=job, initial_status="queued")

    seen_configs: list[dict] = []

    def _get_plugin_fn(name):
        return _FakePlugin()

    def _build_config_fn(company_id, plugin_name):
        cfg: dict = {}
        seen_configs.append(cfg)
        return cfg

    mod.run_import_job(
        {"jobId": 1, "companyId": 1, "script": "fake", "blobRef": "uploads/sample.xlsx"},
        job_repo=repo,
        get_odoo_client=lambda: FakeOdooClient(),
        get_plugin_fn=_get_plugin_fn,
        build_config_fn=_build_config_fn,
        download_file_fn=lambda blob_ref: FIXTURE_PATH,
        write_skip_report_fn=lambda job_id, rows, extra: None,
        cleanup_downloaded_file=False,
    )

    assert repo._status == "completed"
    (config,) = seen_configs
    assert config["accounting_date"] == "2026-02-15"
    assert config["original_entry_ref"] == "ORIG-42"


# ---------------------------------------------------------------------------
# Throttled progress-writer (losstaand van de volledige pipeline)
# ---------------------------------------------------------------------------


def test_throttled_progress_writer_suppresses_rapid_same_phase_calls(monkeypatch):
    mod = _import_processor_module(monkeypatch)

    calls: list[tuple] = []

    class _Repo:
        def update_progress(self, job_id, phase, current, total, message):
            calls.append((job_id, phase, current, total, message))

    writer = mod._ThrottledProgressWriter(_Repo(), job_id=1, min_interval_seconds=100.0)
    writer("executing", 1, 10, "row 1")
    writer("executing", 2, 10, "row 2")  # binnen het interval — onderdrukt
    writer("executing", 3, 10, "row 3")  # idem

    assert len(calls) == 1
    assert calls[0][2] == 1  # alleen de EERSTE 'executing'-call kwam door


def test_throttled_progress_writer_always_flushes_on_phase_change(monkeypatch):
    mod = _import_processor_module(monkeypatch)

    calls: list[tuple] = []

    class _Repo:
        def update_progress(self, job_id, phase, current, total, message):
            calls.append((job_id, phase, current, total, message))

    writer = mod._ThrottledProgressWriter(_Repo(), job_id=1, min_interval_seconds=100.0)
    writer("building", 0, 0, "start building")
    writer("executing", 0, 5, "start executing")  # fase-wissel — nooit onderdrukt

    assert [c[1] for c in calls] == ["building", "executing"]


def test_throttled_progress_writer_always_flushes_when_total_reached(monkeypatch):
    mod = _import_processor_module(monkeypatch)

    calls: list[tuple] = []

    class _Repo:
        def update_progress(self, job_id, phase, current, total, message):
            calls.append((job_id, phase, current, total, message))

    writer = mod._ThrottledProgressWriter(_Repo(), job_id=1, min_interval_seconds=100.0)
    writer("executing", 1, 5, "row 1")
    writer("executing", 5, 5, "klaar")  # current >= total — altijd doorlaten

    assert [c[2] for c in calls] == [1, 5]


def test_throttled_progress_writer_survives_repo_write_failure(monkeypatch):
    """Een falende progress-write mag de import zelf nooit laten crashen
    (best-effort telemetrie) — zie de kop-comment in `import_processor.py`."""
    mod = _import_processor_module(monkeypatch)

    class _FailingRepo:
        def update_progress(self, job_id, phase, current, total, message):
            raise RuntimeError("SQL Server unavailable")

    writer = mod._ThrottledProgressWriter(_FailingRepo(), job_id=1)
    writer("starting", 0, 0, "should not raise")  # mag geen exceptie geven


# ---------------------------------------------------------------------------
# De echte queue-trigger-entrypoint (JSON-decodering van `func.QueueMessage`)
# ---------------------------------------------------------------------------


def test_process_import_job_decodes_queue_message_and_dispatches(monkeypatch):
    """Volledige end-to-end van de queue-trigger-functie zelf (niet alleen
    `run_import_job()`) — bewijst dat de JSON-decodering van
    `func.QueueMessage.get_body()` correct doorspeelt naar de pipeline, met
    alle DB-/Odoo-/blob-afhankelijkheden gemonkeypatcht (geen echte
    Azure/SQL Server-omgeving nodig)."""
    import json

    import azure.functions as func

    mod = _import_processor_module(monkeypatch)
    job = _make_job_row(mod)
    repo = _FakeImportJobRepo(job=job, initial_status="queued")

    monkeypatch.setattr(mod, "PyodbcImportJobRepo", lambda: repo)
    monkeypatch.setattr(mod.odoo_conn, "get_client", lambda: FakeOdooClient())
    monkeypatch.setattr(mod, "get_plugin", lambda name: _FakePlugin())
    monkeypatch.setattr(
        mod, "build_import_config", lambda company_id, plugin_name: {}
    )
    monkeypatch.setattr(mod, "_download_blob_to_temp", lambda blob_ref: FIXTURE_PATH)
    monkeypatch.setattr(mod, "_write_skip_report_to_blob", lambda *a, **kw: None)
    # `cleanup_downloaded_file` heeft geen override-parameter op het
    # trigger-entrypoint zelf — voorkom dat de gedeelde fixture verwijderd
    # wordt door `Path.unlink` een no-op te maken voor dit pad.
    monkeypatch.setattr(Path, "unlink", lambda self, missing_ok=False: None)

    body = json.dumps(
        {"jobId": 1, "companyId": 1, "script": "fake", "blobRef": "uploads/sample.xlsx"}
    ).encode("utf-8")
    queue_message = func.QueueMessage(body=body)

    mod.process_import_job(queue_message)

    assert repo._status == "completed"
    assert len(repo.completed_calls) == 1
