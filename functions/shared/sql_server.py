"""SQL Server ticket/file-number-lookups — poort van
`travel-experts-backend/apps/main/app/shared/sql_server.py`.

Geen Odoo-toegang hier — puur SQL Server (pyodbc via SQLAlchemy) tegen de
BTS-staging-tabellen (BABTS/AABTS/SWED/ITA, verschil #7 in het plan). De
connection-string komt bij elke aanroep uit het meegegeven `db_cfg`-dict
(business-config uit `app_config`, via de plugin's `config`-parameter) —
**niet** rechtstreeks uit env; deze module importeert `env` dus niet en
blijft 1-op-1 t.o.v. de bron (geen client-naam, geen gedragswijziging).
"""

from __future__ import annotations

import logging
from contextlib import contextmanager
from typing import Dict, Iterable, List

from sqlalchemy import create_engine
from sqlalchemy.engine import URL


def _chunked(items: List[str], size: int) -> Iterable[List[str]]:
    for i in range(0, len(items), size):
        yield items[i : i + size]


def expand_combined_ticket(ticket_ref: str) -> List[str]:
    """Expandeer een gecombineerd ticket zoals ``0826330738683-84-85`` naar losse tickets.

    Het basisnummer is alles vóór de eerste ``-``. Elk volgend deel vervangt
    de **laatste N cijfers** van de basis (waarbij N = len(suffix)).

    Retourneert een lijst van volledig geëxpandeerde ticketstrings.
    """
    parts = ticket_ref.split("-")
    if len(parts) <= 1:
        return [ticket_ref]

    base = parts[0].strip()
    if not base:
        return [ticket_ref]

    expanded = [base]
    for suffix in parts[1:]:
        suffix = suffix.strip()
        if not suffix or not suffix.isdigit():
            continue
        n = len(suffix)
        if n >= len(base):
            expanded.append(suffix)
        else:
            expanded.append(base[:-n] + suffix)

    return expanded


def _validate_db_cfg(db_cfg: Dict[str, str]) -> List[str]:
    return (
        []
        if str(db_cfg.get("sql_connection_string", "")).strip()
        else ["sql_connection_string"]
    )


def _connect_timeout(db_cfg: Dict[str, str]) -> int:
    return int(db_cfg.get("timeout") or 10)


def _query_timeout(db_cfg: Dict[str, str]) -> int:
    return int(db_cfg.get("query_timeout") or 120)


def _display_db_target(sql_connection_string: str) -> str:
    target = "<sql-server>"
    for part in sql_connection_string.split(";"):
        item = part.strip()
        lower = item.lower()
        if lower.startswith("server="):
            target = item.split("=", 1)[1].strip()
            break
    return target


@contextmanager
def _open_connection(sql_connection_string: str, connect_timeout: int, query_timeout: int):
    """Genereer een SQLAlchemy/pyodbc raw-connectie (`engine.raw_connection()`).

    Losgetrokken uit `_open_cursor()` zodat andere modules in `functions/`
    (bv. `config_resolve.py`/`import_processor.py`, via
    `open_cursor_for_connection_string()`/`open_write_cursor()` hieronder) de
    **zelfde** pyodbc-connectiemechaniek (`pool_pre_ping`, connect-/query-
    timeouts) hergebruiken in plaats van een tweede ad-hoc `pyodbc.connect(...)`
    ergens anders in de codebase te introduceren — zie de fase-3-opdracht
    ("Herbruik `functions/shared/sql_server.py` voor de pyodbc-connectie").
    """
    if not sql_connection_string:
        raise ValueError("Missing sql_connection_string")

    engine = create_engine(
        URL.create("mssql+pyodbc", query={"odbc_connect": sql_connection_string}),
        pool_pre_ping=True,
        pool_recycle=1800,
        connect_args={"timeout": connect_timeout},
    )

    connection = engine.raw_connection()
    try:
        try:
            connection.timeout = query_timeout
        except Exception:
            pass
        yield connection
    finally:
        connection.close()
        engine.dispose()


@contextmanager
def _open_cursor(db_cfg: Dict[str, str]):
    sql_connection_string = str(db_cfg.get("sql_connection_string", "")).strip()
    connect_timeout = _connect_timeout(db_cfg)
    query_timeout = _query_timeout(db_cfg)

    with _open_connection(sql_connection_string, connect_timeout, query_timeout) as connection:
        cursor = connection.cursor()
        try:
            cursor.timeout = query_timeout
        except Exception:
            pass
        yield cursor


@contextmanager
def open_cursor_for_connection_string(
    sql_connection_string: str,
    *,
    timeout: int = 10,
    query_timeout: int = 120,
):
    """Publieke, generieke lees-variant van `_open_cursor()` voor callers die
    geen ticket-lookup-`db_cfg`-vorm hebben maar rechtstreeks een connection-
    string — gebruikt door `config_resolve.py` (`[{schema}].[app_config]`,
    read-only precedentie-lookups). Geen auto-commit (matcht `_open_cursor`).
    """
    with _open_cursor(
        {
            "sql_connection_string": sql_connection_string,
            "timeout": timeout,
            "query_timeout": query_timeout,
        }
    ) as cursor:
        yield cursor


@contextmanager
def open_write_cursor(
    sql_connection_string: str,
    *,
    timeout: int = 10,
    query_timeout: int = 120,
):
    """Schrijvende variant: commit bij succesvolle exit, rollback bij exceptie.

    Gebruikt door `import_processor.py` (`[{schema}].[import_jobs]` status-/
    progress-/resultaatschrijven, zie docs/contracts.md §3) — zelfde
    connectiemechaniek (`pool_pre_ping`, timeouts) als de read-only ticket-
    lookups hierboven, nu met expliciete transactie-afhandeling omdat writes
    hier — in tegenstelling tot de ticket-lookups — wél nodig zijn.
    """
    with _open_connection(sql_connection_string, timeout, query_timeout) as connection:
        cursor = connection.cursor()
        try:
            cursor.timeout = query_timeout
        except Exception:
            pass
        try:
            yield cursor
            connection.commit()
        except Exception:
            try:
                connection.rollback()
            except Exception:
                pass
            raise


def fetch_ticket_filenumbers(
    db_cfg: Dict[str, str],
    tickets: Iterable[str],
    table: str,
    ticket_col: str,
    chunk_size: int = 500,
) -> Dict[str, str]:
    ticket_list = [str(t).strip()[:10] for t in tickets if str(t).strip()]
    if not ticket_list:
        return {}

    missing = _validate_db_cfg(db_cfg)
    if missing:
        logging.warning(
            "DB lookup skipped: missing config keys: %s", ", ".join(missing)
        )
        return {}

    sql_connection_string = str(db_cfg.get("sql_connection_string", "")).strip()
    connect_timeout = _connect_timeout(db_cfg)
    if db_cfg.get("chunk_size"):
        try:
            chunk_size = int(db_cfg.get("chunk_size") or chunk_size)
        except ValueError:
            pass

    results: Dict[str, str] = {}
    if not table:
        logging.warning("DB lookup skipped: missing table name.")
        return {}
    if not ticket_col:
        logging.warning("DB lookup skipped: missing ticket column name.")
        return {}
    logging.info(
        "[db_lookup] Connecting to %s (timeout=%ss)",
        _display_db_target(sql_connection_string),
        connect_timeout,
    )
    with _open_cursor(db_cfg) as cursor:
        for chunk in _chunked(ticket_list, chunk_size):
            conditions = " OR ".join([f"{ticket_col} LIKE ?"] * len(chunk))
            query = f"SELECT {ticket_col}, FileNumber FROM {table} WHERE {conditions}"
            params = [t + "%" for t in chunk]
            cursor.execute(query, params)
            for ticket, filenumber in cursor.fetchall():
                if ticket is None or filenumber is None:
                    continue
                ticket_key = str(ticket).strip()[:10]
                if ticket_key and ticket_key not in results:
                    results[ticket_key] = str(filenumber).strip()
    return results


def fetch_ticket_filenumbers_prefixed(
    db_cfg: Dict[str, str],
    prefixed_tickets: Iterable[str],
    table: str,
    ticket_col: str,
    chunk_size: int = 500,
) -> Dict[str, str]:
    """Zoek file numbers via volledige prefixed ticketstrings (geen truncatie).

    In tegenstelling tot :func:`fetch_ticket_filenumbers` worden invoer en
    resultaatsleutels NIET afgekapt tot 10 tekens. Gebruikt door Pass 2 van de
    BSP-lookup-keten zodat airline-code-prefixed strings alle cijfers
    behouden — anders botsen opeenvolgende ticketnummers die de eerste 7
    cijfers delen na het 3-cijferige airline-prefix. Resultaten zijn
    geïndexeerd op de **invoer-prefix** die een rij matchte.
    """
    ticket_list = sorted({str(t).strip() for t in prefixed_tickets if str(t).strip()})
    if not ticket_list:
        return {}

    missing = _validate_db_cfg(db_cfg)
    if missing:
        logging.warning(
            "DB lookup skipped: missing config keys: %s", ", ".join(missing)
        )
        return {}

    if not table or not ticket_col:
        return {}

    sql_connection_string = str(db_cfg.get("sql_connection_string", "")).strip()
    connect_timeout = _connect_timeout(db_cfg)
    if db_cfg.get("chunk_size"):
        try:
            chunk_size = int(db_cfg.get("chunk_size") or chunk_size)
        except ValueError:
            pass

    results: Dict[str, str] = {}
    logging.info(
        "[db_lookup_prefixed] Connecting to %s (timeout=%ss)",
        _display_db_target(sql_connection_string),
        connect_timeout,
    )
    with _open_cursor(db_cfg) as cursor:
        for chunk in _chunked(ticket_list, chunk_size):
            conditions = " OR ".join([f"{ticket_col} LIKE ?"] * len(chunk))
            query = f"SELECT {ticket_col}, FileNumber FROM {table} WHERE {conditions}"
            params = [t + "%" for t in chunk]
            cursor.execute(query, params)
            for db_ticket, filenumber in cursor.fetchall():
                if db_ticket is None or filenumber is None:
                    continue
                db_value = str(db_ticket).strip()
                fn = str(filenumber).strip()
                for prefix in chunk:
                    if prefix not in results and db_value.startswith(prefix):
                        results[prefix] = fn
    return results


def fetch_ticket_filenumbers_by_variants(
    db_cfg: Dict[str, str],
    tickets: Iterable[str],
    table: str,
    ticket_col: str,
    chunk_size: int = 500,
    min_length: int = 9,
) -> Dict[str, str]:
    """Zoek file numbers via exacte IN-matches op leading-zero-varianten.

    In tegenstelling tot :func:`fetch_ticket_filenumbers` worden invoer-
    tickets NIET afgekapt. Voor elk ticket worden alle varianten gegenereerd
    door telkens één leidende ``"0"`` te strippen tot de string niet meer met
    ``"0"`` begint of de lengte ``min_length`` bereikt. Alle varianten worden
    met exacte ``IN``-matches bevraagd; bij meerdere varianten-hits van
    hetzelfde ticket wint de kortste variant (meeste nullen gestript).

    Retourneert een ``Dict`` geïndexeerd op het **originele** invoerticket.
    """
    # Bouw variant_map: variant -> origineel invoerticket (geen truncatie)
    variant_map: Dict[str, str] = {}
    for raw in tickets:
        t = str(raw).strip()
        if not t:
            continue
        current = t
        while True:
            variant_map[current] = t
            if not current.startswith("0") or len(current) == min_length:
                break
            current = current[1:]

    if not variant_map:
        return {}

    missing = _validate_db_cfg(db_cfg)
    if missing:
        logging.warning(
            "DB lookup skipped: missing config keys: %s", ", ".join(missing)
        )
        return {}

    sql_connection_string = str(db_cfg.get("sql_connection_string", "")).strip()
    connect_timeout = _connect_timeout(db_cfg)
    if db_cfg.get("chunk_size"):
        try:
            chunk_size = int(db_cfg.get("chunk_size") or chunk_size)
        except ValueError:
            pass

    if not table:
        logging.warning("DB lookup skipped: missing table name.")
        return {}
    if not ticket_col:
        logging.warning("DB lookup skipped: missing ticket column name.")
        return {}

    # best_match: origineel -> (kortste_variant_len, filenumber)
    best_match: Dict[str, tuple] = {}
    all_variants = list(variant_map.keys())

    logging.info(
        "[db_lookup_variants] Connecting to %s (timeout=%ss) — %d variant(s) for %d ticket(s)",
        _display_db_target(sql_connection_string),
        connect_timeout,
        len(all_variants),
        len({v for v in variant_map.values()}),
    )
    with _open_cursor(db_cfg) as cursor:
        for chunk in _chunked(all_variants, chunk_size):
            placeholders = ", ".join(["?"] * len(chunk))
            query = f"SELECT {ticket_col}, FileNumber FROM {table} WHERE {ticket_col} IN ({placeholders})"
            cursor.execute(query, chunk)
            for db_ticket, filenumber in cursor.fetchall():
                if db_ticket is None or filenumber is None:
                    continue
                variant = str(db_ticket).strip()
                original = variant_map.get(variant)
                if original is None:
                    continue
                fn = str(filenumber).strip()
                vlen = len(variant)
                if original not in best_match or vlen < best_match[original][0]:
                    best_match[original] = (vlen, fn)

    return {orig: fn for orig, (_, fn) in best_match.items()}


def fetch_ticket_filenumbers_by_dnr(
    db_cfg: Dict[str, str],
    rows: Iterable[Dict[str, str]],
    table: str,
    dnr_col: str,
    ticket_col: str,
    chunk_size: int = 500,
    excluded_filenumber: str = "99999999",
    diagnostics: Dict[str, str] | None = None,
) -> Dict[str, str]:
    """Zoek file numbers via DNR met een ticket-gebaseerde tiebreaker.

    Primaire lookup: exacte match op *dnr_col*. Alle rijen worden opgehaald,
    ook die met FileNumber == *excluded_filenumber* (default ``"99999999"``).
    Resolutie per DNR:

    1. Splits rijen in *preferred* (FileNumber != excluded) en *excluded*
       (FileNumber == excluded).
    2. Zonder preferred-rijen → Needs Review (excluded-only DNR's worden
       nooit opgelost).
    3. Binnen de preferred-groep:
       - Exact 1 unieke FileNumber → gebruik die direct.
       - 2+ unieke FileNumbers → tiebreaker via *ticket_col*:
         - len == 12 → moet exact gelijk zijn aan *issue_id*
         - len == 9  → moet gelijk zijn aan ``issue_id[2:11]``
         - Exact 1 match → gebruik die FileNumber; anders → Needs Review.

    Retourneert ``Dict[str, str]`` geïndexeerd op de DNR-string.
    """
    # Bouw {dnr: issue_id} — eerste occurrence wint bij duplicaten
    dnr_to_issue: Dict[str, str] = {}
    for r in rows:
        dnr = str(r.get("dnr", "") or "").strip()
        if dnr and dnr not in dnr_to_issue:
            dnr_to_issue[dnr] = str(r.get("issue_id", "") or "").strip()

    if not dnr_to_issue:
        return {}

    missing = _validate_db_cfg(db_cfg)
    if missing:
        logging.warning(
            "DB lookup skipped: missing config keys: %s", ", ".join(missing)
        )
        return {}

    sql_connection_string = str(db_cfg.get("sql_connection_string", "")).strip()
    connect_timeout = _connect_timeout(db_cfg)
    if db_cfg.get("chunk_size"):
        try:
            chunk_size = int(db_cfg.get("chunk_size") or chunk_size)
        except ValueError:
            pass

    if not table:
        logging.warning("DB lookup skipped: missing table name.")
        return {}
    if not dnr_col:
        logging.warning("DB lookup skipped: missing DNR column name.")
        return {}
    if not ticket_col:
        logging.warning("DB lookup skipped: missing ticket column name.")
        return {}

    # Verzamel alle (ticket_col_value, filenumber)-paren per DNR (alle
    # FileNumbers, inclusief excluded)
    dnr_candidates: Dict[str, List[tuple]] = {dnr: [] for dnr in dnr_to_issue}
    dnr_list = list(dnr_to_issue.keys())

    logging.info(
        "[db_lookup_dnr] Connecting to %s (timeout=%ss) — %d DNR(s)",
        _display_db_target(sql_connection_string),
        connect_timeout,
        len(dnr_list),
    )
    with _open_cursor(db_cfg) as cursor:
        for chunk in _chunked(dnr_list, chunk_size):
            placeholders = ", ".join(["?"] * len(chunk))
            query = (
                f"SELECT {dnr_col}, {ticket_col}, FileNumber FROM {table} "
                f"WHERE {dnr_col} IN ({placeholders})"
            )
            cursor.execute(query, list(chunk))
            for db_dnr, db_ticket, filenumber in cursor.fetchall():
                if db_dnr is None or filenumber is None:
                    continue
                dnr = str(db_dnr).strip()
                if dnr in dnr_candidates:
                    db_ticket_str = (
                        str(db_ticket).strip() if db_ticket is not None else ""
                    )
                    fn = str(filenumber).strip()
                    dnr_candidates[dnr].append((db_ticket_str, fn))

    def _tiebreak(candidates: List[tuple], issue_id: str) -> "str | None":
        """1 unieke FileNumber → retourneer die; 2+ → probeer ticket-tiebreaker; anders None."""
        unique_fns = list(dict.fromkeys(fn for _, fn in candidates))
        if len(unique_fns) == 1:
            return unique_fns[0]
        matched_fns = []
        for db_ticket_str, fn in candidates:
            tlen = len(db_ticket_str)
            if tlen == 12 and db_ticket_str == issue_id:
                matched_fns.append(fn)
            elif tlen == 9 and db_ticket_str == issue_id[2:11]:
                matched_fns.append(fn)
        return matched_fns[0] if len(matched_fns) == 1 else None

    # Resolve: gebruik alleen niet-excluded FileNumbers; DNR's met uitsluitend
    # de excluded FileNumber → Needs Review
    results: Dict[str, str] = {}
    for dnr, candidates in dnr_candidates.items():
        if not candidates:
            if diagnostics is not None:
                diagnostics[dnr] = "DNR not found in SQL Server DB"
            continue
        issue_id = dnr_to_issue[dnr]
        preferred = [(t, fn) for t, fn in candidates if fn != excluded_filenumber]
        if preferred:
            fn = _tiebreak(preferred, issue_id)
            if fn is None and diagnostics is not None:
                unique_fns = list(dict.fromkeys(f for _, f in preferred))
                diagnostics[dnr] = (
                    f"Multiple FileNumbers found, tiebreaker failed "
                    f"({len(unique_fns)} candidates: {', '.join(unique_fns)})"
                )
        else:
            # Alle rijen hebben de excluded FileNumber — altijd Needs Review
            fn = None
            if diagnostics is not None:
                diagnostics[dnr] = (
                    f"Only excluded FileNumber ({excluded_filenumber}) found"
                )
        if fn is not None:
            results[dnr] = fn

    return results


def fetch_raw_tickets(
    db_cfg: Dict[str, str],
    prefixes: Iterable[str],
    table: str,
    ticket_col: str,
    chunk_size: int = 500,
) -> List[tuple]:
    """Zoek in SQL Server via korte prefixen en retourneer **volledige** ticketstrings.

    In tegenstelling tot :func:`fetch_ticket_filenumbers` worden resultaten
    NIET afgekapt tot 10 tekens, zodat gecombineerde tickets zoals
    ``0826330738683-84-85`` intact terugkomen voor Python-side expansie.

    Retourneert een lijst van ``(full_ticket, filenumber)``-tuples.
    """
    prefix_list = sorted({str(p).strip() for p in prefixes if str(p).strip()})
    if not prefix_list:
        return []

    missing = _validate_db_cfg(db_cfg)
    if missing:
        logging.warning(
            "DB lookup skipped: missing config keys: %s", ", ".join(missing)
        )
        return []

    if not table or not ticket_col:
        return []

    sql_connection_string = str(db_cfg.get("sql_connection_string", "")).strip()
    connect_timeout = _connect_timeout(db_cfg)
    if db_cfg.get("chunk_size"):
        try:
            chunk_size = int(db_cfg.get("chunk_size") or chunk_size)
        except ValueError:
            pass

    results: List[tuple] = []
    logging.info(
        "[db_lookup_raw] Connecting to %s (timeout=%ss)",
        _display_db_target(sql_connection_string),
        connect_timeout,
    )
    with _open_cursor(db_cfg) as cursor:
        for chunk in _chunked(prefix_list, chunk_size):
            conditions = " OR ".join([f"{ticket_col} LIKE ?"] * len(chunk))
            query = f"SELECT {ticket_col}, FileNumber FROM {table} WHERE {conditions}"
            params = [p + "%" for p in chunk]
            cursor.execute(query, params)
            for ticket, filenumber in cursor.fetchall():
                if ticket is None or filenumber is None:
                    continue
                results.append((str(ticket).strip(), str(filenumber).strip()))
    return results
