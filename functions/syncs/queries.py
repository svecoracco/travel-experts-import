"""Queries voor het sync-proces — poort van
`travel-experts-backend/apps/syncs/queries.py`.

Leest `SQL_CONNECTION_STRING` uit `env.ENV` i.p.v. rechtstreeks
`os.environ.get("SQLConnectionString")` (harde projectregel #8; de bron
gebruikte bovendien een andere env-var-naam — geünificeerd op
`SQL_CONNECTION_STRING`, zie `local.settings.template.json`).

`synced_to_table`-parameter is verwijderd — zie de reconciliatie-noot in
`models.py` (die kolom bestaat niet in de echte gedeployde DDL).
"""

from __future__ import annotations

import urllib
from datetime import datetime
from typing import Optional

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from syncs.models import CsvBlobSyncLog


def _make_session():
    from env import ENV

    params = urllib.parse.quote_plus(ENV.sql_connection_string)
    connect_string = f"mssql+pyodbc:///?odbc_connect={params}"
    engine = create_engine(connect_string)
    return sessionmaker(bind=engine)()


def upsert_csv_blob_sync_log(
    blob_name: str,
    status: str,
    triggered_by: str,
    started_at: datetime,
    finished_at: Optional[datetime] = None,
    error_message: Optional[str] = None,
    row_count: Optional[int] = None,
) -> None:
    """Insert of update een sync-log-entry voor een blob-bestand (één rij per blob)."""
    session = _make_session()
    try:
        existing = (
            session.query(CsvBlobSyncLog)
            .filter(CsvBlobSyncLog.blob_name == blob_name)
            .first()
        )
        if existing:
            existing.status = status
            existing.started_at = started_at
            existing.finished_at = finished_at
            existing.error_message = error_message
            existing.row_count = row_count
            existing.triggered_by = triggered_by
        else:
            new_entry = CsvBlobSyncLog(
                blob_name=blob_name,
                status=status,
                started_at=started_at,
                finished_at=finished_at,
                error_message=error_message,
                row_count=row_count,
                triggered_by=triggered_by,
            )
            session.add(new_entry)
        session.commit()
    except Exception as e:
        session.rollback()
        raise e
    finally:
        session.close()


def get_failed_blob_names() -> list[str]:
    """Retourneer blob-namen waar status 'error' is (niet pending/in-progress)."""
    session = _make_session()
    try:
        rows = (
            session.query(CsvBlobSyncLog.blob_name)
            .filter(CsvBlobSyncLog.status == "error")
            .all()
        )
        return [r.blob_name for r in rows]
    finally:
        session.close()
