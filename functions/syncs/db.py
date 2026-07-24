"""Raw-SQL-schrijfhulp (SQLAlchemy/pyodbc) voor de syncs — poort van
`travel-experts-backend/data/db.py`.

Odoo-onafhankelijk; leest de connection-string uit `env.ENV`
(`SQL_CONNECTION_STRING`) i.p.v. rechtstreeks `os.environ["SQL_CONNECTION_STRING"]`
(harde projectregel #8 — Één env-module). Lazy import van `env`/`sqlalchemy`
zodat het importeren van deze module geen SQL Server-omgeving vereist.
"""

from __future__ import annotations

import logging
import time
import urllib
from functools import lru_cache
from typing import Callable

import pandas as pd
import sqlalchemy as sql
from sqlalchemy import event


def _quote_sql_server_identifier(identifier: str) -> str:
    # Sta brede SQL Server-identifiers toe, maar voorkom statement-breakouts.
    # Bracket-quoting is veilig zolang ']' niet voorkomt.
    if identifier is None:
        raise ValueError("SQL identifier cannot be None")
    identifier = str(identifier).strip()
    if not identifier:
        raise ValueError("SQL identifier cannot be empty")
    if "]" in identifier or "\x00" in identifier:
        raise ValueError(f"Invalid SQL identifier: {identifier!r}")
    return f"[{identifier}]"


def _is_transient_db_error(exc: Exception) -> bool:
    message = str(exc).lower()
    transient_markers = (
        "08s01",
        "tcp provider",
        "communication link failure",
        "connection reset",
        "connection was forcibly closed",
        "timeout",
        "deadlock",
    )
    return any(marker in message for marker in transient_markers)


def _run_db_operation_with_retry(
    *,
    operation_name: str,
    fast_executemany: bool,
    operation: Callable[[], None],
    max_attempts: int = 3,
) -> None:
    last_exc: Exception | None = None
    for attempt in range(1, max_attempts + 1):
        try:
            operation()
            return
        except Exception as exc:
            last_exc = exc
            is_transient = _is_transient_db_error(exc)
            if (not is_transient) or attempt == max_attempts:
                raise

            backoff_seconds = min(2 ** (attempt - 1), 8)
            logging.warning(
                f"Transient database error during {operation_name} (attempt {attempt}/{max_attempts}): {exc}. "
                f"Retrying in {backoff_seconds}s."
            )

            try:
                get_sql_engine(fast_executemany=fast_executemany).dispose()
            except Exception:
                logging.info("Could not dispose SQL engine before retry")

            time.sleep(backoff_seconds)

    if last_exc is not None:
        raise last_exc


def dictfetchall(cursor: object) -> list:
    "Retourneer alle rijen van een cursor als dict"
    columns = [col[0] for col in cursor.description]
    return [dict(zip(columns, row)) for row in cursor.fetchall()]


@lru_cache(maxsize=2)
def _get_engine(*, fast_executemany: bool) -> sql.Engine:
    from env import ENV

    params = urllib.parse.quote_plus(ENV.sql_connection_string)
    connect_string = f"mssql+pyodbc:///?odbc_connect={params}"

    engine = sql.create_engine(
        connect_string,
        fast_executemany=fast_executemany,
        pool_pre_ping=True,
        future=True,
    )

    if fast_executemany:

        @event.listens_for(engine, "before_cursor_execute")
        def _set_fast_executemany(
            conn, cursor, statement, parameters, context, executemany
        ):
            if executemany:
                cursor.fast_executemany = True

    return engine


def get_sql_engine(*, fast_executemany: bool = True) -> sql.Engine:
    return _get_engine(fast_executemany=fast_executemany)


def write_df_to_db(
    df: pd.DataFrame, schema: str, db_table: str, if_exists: str = "append"
) -> None:
    """Schrijf een pandas DataFrame naar een SQL Server-databasetabel."""

    len_df = len(df)
    if len_df == 0:
        logging.info("DataFrame is empty. No records to write to database.")
        return

    if "Id" in df.columns:
        df = df.sort_values("Id")

    logging.info(
        f"Start writing DataFrame to database table {schema}.{db_table} with {len_df} records"
    )
    time_start = pd.Timestamp.now()

    def _write_operation() -> None:
        sql_engine = get_sql_engine(fast_executemany=True)
        df.to_sql(
            db_table,
            con=sql_engine,
            schema=schema,
            if_exists=if_exists,
            index=False,
            chunksize=None,
            method=None,
        )

    _run_db_operation_with_retry(
        operation_name=f"writing DataFrame to {schema}.{db_table}",
        fast_executemany=True,
        operation=_write_operation,
    )

    elapsed_sec = (pd.Timestamp.now() - time_start).total_seconds()
    logging.info(f"Finished writing in {elapsed_sec / 60:.2f} minutes")


def clean_db_table(schema: str, db_table: str) -> None:
    """Maak een SQL Server-databasetabel leeg via TRUNCATE."""
    try:
        logging.info(f"Start cleaning database table {schema}.{db_table}")
        quoted_schema = _quote_sql_server_identifier(schema)
        quoted_table = _quote_sql_server_identifier(db_table)

        def _clean_operation() -> None:
            sql_engine = get_sql_engine(fast_executemany=False)
            with sql_engine.begin() as connection:
                connection.execute(
                    sql.text(f"TRUNCATE TABLE {quoted_schema}.{quoted_table}")
                )

        _run_db_operation_with_retry(
            operation_name=f"cleaning table {schema}.{db_table}",
            fast_executemany=False,
            operation=_clean_operation,
        )

        logging.info(f"Cleaned database table {schema}.{db_table}")
    except Exception as e:
        if "because it does not exist or you do not have permissions" in str(e):
            logging.warning(
                f"Table {schema}.{db_table} does not exist or insufficient permissions to truncate. Skipping truncate."
            )
        else:
            logging.error(
                f"Error cleaning database table {schema}.{db_table}: {str(e)}"
            )
            raise


def rebuild_indexes_db_table(schema: str, db_table: str) -> None:
    """Herbouw alle indexen op een SQL Server-databasetabel."""
    try:
        logging.info(f"Start rebuilding indexes on database table {schema}.{db_table}")
        sql_engine = get_sql_engine(fast_executemany=False)
        quoted_schema = _quote_sql_server_identifier(schema)
        quoted_table = _quote_sql_server_identifier(db_table)
        with sql_engine.begin() as connection:
            connection.execute(
                sql.text(f"ALTER INDEX ALL on {quoted_schema}.{quoted_table} REBUILD")
            )
        logging.info(
            f"Finished rebuilding indexes on database table {schema}.{db_table}"
        )
    except Exception as e:
        logging.error(
            f"Error rebuilding indexes on database table {schema}.{db_table}: {str(e)}"
        )
        raise


def disable_nonclustered_indexes_except_constraints(schema: str, db_table: str) -> None:
    """Schakel nonclustered-indexen uit behalve die achter PK/UNIQUE-constraints."""
    try:
        logging.info(
            f"Start disabling nonclustered indexes on {schema}.{db_table} (excluding PK/UQ)"
        )
        sql_engine = get_sql_engine(fast_executemany=False)

        stmt = sql.text(
            """
        DECLARE @sql nvarchar(max) = N'';

        SELECT @sql = @sql + N'
        ALTER INDEX ' + QUOTENAME(i.name) + N' ON ' + QUOTENAME(s.name) + N'.' + QUOTENAME(t.name) + N' DISABLE;'
        FROM sys.indexes i
        JOIN sys.tables t  ON t.object_id = i.object_id
        JOIN sys.schemas s ON s.schema_id = t.schema_id
        WHERE s.name = :schema
          AND t.name = :table
          AND i.type_desc = 'NONCLUSTERED'
          AND i.is_primary_key = 0
          AND i.is_unique_constraint = 0
          AND i.name IS NOT NULL;

        IF (@sql <> N'')
            EXEC sp_executesql @sql;
        """
        )

        with sql_engine.begin() as connection:
            connection.execute(stmt, {"schema": schema, "table": db_table})

        logging.info(f"Finished disabling nonclustered indexes on {schema}.{db_table}")
    except Exception as e:
        logging.error(f"Error disabling indexes on {schema}.{db_table}: {str(e)}")
        raise
