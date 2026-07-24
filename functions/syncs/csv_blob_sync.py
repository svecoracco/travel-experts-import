"""CSV-blob-naar-SQL-Server-sync — poort van
`travel-experts-backend/jobs/blob_csv_sync.py`.

Het hardgecodeerde schema `"bts"` is vervangen door `env.ENV.db_schema`
(harde projectregel #7). `BLOB_CONTAINER_NAME` komt uit `env.ENV` i.p.v.
rechtstreeks `os.getenv(...)` (harde projectregel #8).
"""

from __future__ import annotations

import logging
import os
import re
import tempfile
from datetime import datetime, timezone

import numpy as np
import pandas as pd

from syncs.azure_blob import (
    get_blob_container_client,
    get_blob_container_files,
    get_blob_service_client,
)
from syncs.db import (
    clean_db_table,
    disable_nonclustered_indexes_except_constraints,
    rebuild_indexes_db_table,
    write_df_to_db,
)
from syncs.queries import get_failed_blob_names, upsert_csv_blob_sync_log

illegal_re = re.compile(r"[\x00-\x08\x0B\x0C\x0E-\x1F]")


def clean_str(x):
    if x is None:
        return x
    s = str(x)
    return illegal_re.sub("", s)


def create_csv_blob_file_table_name(blob_name: str) -> str:
    logging.info(f"Creating table name from blob name: {blob_name}")
    base_name = os.path.splitext(blob_name.replace("/", "_"))[0]
    parts = base_name.split("_dbo.")
    if len(parts) != 2:
        raise ValueError(f"Invalid blob name format: {blob_name}")
    prefix = parts[0]
    table_name = f"tbl_{prefix}".lower()
    logging.info(f"Created table name: {table_name} from blob name: {blob_name}")
    return table_name


def create_staging_table_name(table_name: str) -> str:
    return f"{table_name}_staging"


def _sync_csv_blob_to_db(
    *, container_client, blob_name: str, triggered_by: str = "cron"
) -> None:
    from env import ENV

    if not str(blob_name).endswith(".csv"):
        raise ValueError(f"Blob file is not a CSV: {blob_name}")

    started_at = datetime.now(timezone.utc)
    logging.info(f"Processing blob file: {blob_name}")
    table_name = create_csv_blob_file_table_name(blob_name)
    schema = ENV.db_schema

    logging.info(f"Downloading blob file: {blob_name}")
    blob_client = container_client.get_blob_client(blob_name)

    try:
        blob_props = blob_client.get_blob_properties()
        blob_size = getattr(blob_props, "size", None)
        if blob_size is not None:
            logging.info(f"Blob size (bytes): {blob_size} for {blob_name}")
    except Exception:
        logging.info(f"Could not read blob properties for {blob_name}")

    download_stream = blob_client.download_blob()

    logging.info(
        f"Writing DataFrame to SQL staging table {schema}.{table_name} for blob file: {blob_name}"
    )
    clean_db_table(schema, table_name)
    disable_nonclustered_indexes_except_constraints(schema, table_name)

    logging.info(f"Reading blob file into DataFrame (chunked): {blob_name}")
    tmp_path: str | None = None
    total_rows = 0
    try:
        with tempfile.NamedTemporaryFile(mode="wb", delete=False, suffix=".csv") as tmp:
            tmp_path = tmp.name
            for chunk in download_stream.chunks():
                tmp.write(chunk)

        first_chunk = True
        for df_chunk in pd.read_csv(
            tmp_path,
            sep=",",
            skiprows=0,
            header=0,
            delim_whitespace=False,
            decimal=".",
            skipinitialspace=True,
            skip_blank_lines=False,
            engine="python",
            on_bad_lines="error",
            escapechar="\\",
            quotechar='"',
            encoding="utf-8-sig",
            doublequote=False,
            chunksize=100000,
        ):
            if first_chunk:
                logging.info(f"Cleaning DataFrame columns for blob file: {blob_name}")
                first_chunk = False

            df_chunk.columns = [c.strip() for c in df_chunk.columns]
            df_chunk.columns = [clean_str(c) for c in df_chunk.columns]
            df_chunk = df_chunk.replace(r"^\\s*$", np.nan, regex=True)
            total_rows += len(df_chunk)

            write_df_to_db(
                df=df_chunk,
                schema=schema,
                db_table=table_name,
                if_exists="append",
            )
        logging.info(f"Blob file {blob_name} synced to table {table_name} successfully")
        rebuild_indexes_db_table(schema, table_name)

        upsert_csv_blob_sync_log(
            blob_name=blob_name,
            status="success",
            triggered_by=triggered_by,
            started_at=started_at,
            finished_at=datetime.now(timezone.utc),
            row_count=total_rows,
        )
    except Exception as e:
        logging.exception(f"Error syncing blob file {blob_name}: {e}")
        upsert_csv_blob_sync_log(
            blob_name=blob_name,
            status="error",
            triggered_by=triggered_by,
            started_at=started_at,
            finished_at=datetime.now(timezone.utc),
            error_message=str(e)[:4000],
        )
        try:
            clean_db_table(schema, table_name)
        except Exception:
            logging.exception(
                f"Error cleaning table {table_name} after failed sync of blob file {blob_name}"
            )
    finally:
        if tmp_path and os.path.exists(tmp_path):
            try:
                os.remove(tmp_path)
            except Exception:
                logging.info(f"Could not remove temp file {tmp_path}")


def sync_single_csv_blob_file_to_db(blob_name: str) -> bool:
    from env import ENV

    blob_service_client = get_blob_service_client()
    container_client = get_blob_container_client(
        blob_service_client, ENV.blob_container_name
    )

    _sync_csv_blob_to_db(
        container_client=container_client, blob_name=blob_name, triggered_by="manual"
    )
    return True


def sync_csv_blob_files_to_db() -> bool:
    from env import ENV

    blob_service_client = get_blob_service_client()
    container_client = get_blob_container_client(
        blob_service_client, ENV.blob_container_name
    )

    blobs = list(get_blob_container_files(container_client))
    logging.info(f"Found {len(blobs)} blob files in container {ENV.blob_container_name}")

    for blob in blobs:
        try:
            if not str(blob.name).endswith(".csv"):
                logging.info(f"Skipping non-CSV blob file: {blob.name}")
                continue

            _sync_csv_blob_to_db(
                container_client=container_client,
                blob_name=blob.name,
                triggered_by="cron",
            )
        except Exception:
            logging.exception(f"Error syncing blob file {blob.name}")
            continue
    logging.info("Blob files sync completed")
    return True


def retry_failed_csv_blob_files() -> bool:
    """Herprobeer blob-bestanden met status 'error' in het sync-log."""
    from env import ENV

    failed_blobs = get_failed_blob_names()
    if not failed_blobs:
        logging.info("No failed blob files to retry")
        return True

    logging.info(f"Retrying {len(failed_blobs)} failed blob file(s)")

    blob_service_client = get_blob_service_client()
    container_client = get_blob_container_client(
        blob_service_client, ENV.blob_container_name
    )

    for blob_name in failed_blobs:
        try:
            _sync_csv_blob_to_db(
                container_client=container_client,
                blob_name=blob_name,
                triggered_by="cron",
            )
        except Exception:
            logging.exception(f"Retry failed for blob file {blob_name}")
            continue

    logging.info("Retry of failed blob files completed")
    return True
