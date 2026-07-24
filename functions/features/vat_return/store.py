"""`vat_return_entries`-audittrail (raw SQL/pyodbc) — poort van het
SQLAlchemy-model `app.models.vat_return_entry.VatReturnEntry`
(`travel-experts-backend/apps/main/app/models/vat_return_entry.py`).

Track A heeft geen Prisma-toegang (dat is Track B); deze module schrijft/leest
`[{DB_SCHEMA}].[vat_return_entries]` rechtstreeks via pyodbc, exact zoals
`docs/contracts.md` §6 voorschrijft ("expliciete raw-SQL-kwalificatie"). Kolom-
vorm gespiegeld op `prisma/schema.prisma::VatReturnEntry` /
`prisma/ddl/schema.template.sql`.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional


@dataclass
class VatReturnEntryRow:
    id: int
    company_id: int
    period: str
    odoo_move_id: int
    odoo_move_name: Optional[str]
    ref: str
    created_by: Optional[str]
    created_at: Optional[datetime]
    total_amount: Optional[float]
    line_count: Optional[int]
    dismissed: bool
    dismissed_by: Optional[str]
    dismissed_at: Optional[datetime]


def _connect():
    """Lazy-import pyodbc + env — zie `config_resolve.py` voor dezelfde rationale
    (lazy DB-imports zodat het importeren van deze module geen Track-A-omgeving
    vereist)."""
    import pyodbc

    from env import ENV

    return pyodbc.connect(ENV.sql_connection_string), ENV.db_schema


def get_active_entry(company_id: int, period: str) -> Optional[VatReturnEntryRow]:
    """Zoek de actieve (niet-dismissed) correctie-entry voor company/period."""
    conn, schema = _connect()
    try:
        cursor = conn.cursor()
        cursor.execute(
            f"""
            SELECT TOP 1 id, company_id, period, odoo_move_id, odoo_move_name,
                   ref, created_by, created_at, total_amount, line_count,
                   dismissed, dismissed_by, dismissed_at
            FROM [{schema}].[vat_return_entries]
            WHERE company_id = ? AND period = ? AND dismissed = 0
            ORDER BY id DESC
            """,
            (company_id, period),
        )
        row = cursor.fetchone()
        if row is None:
            return None
        return VatReturnEntryRow(*row)
    finally:
        conn.close()


def insert_entry(
    company_id: int,
    period: str,
    odoo_move_id: int,
    odoo_move_name: Optional[str],
    ref: str,
    created_by: Optional[str],
    total_amount: Optional[float],
    line_count: Optional[int],
) -> int:
    """Maak een nieuwe audittrail-rij aan; retourneert het nieuwe id."""
    conn, schema = _connect()
    try:
        cursor = conn.cursor()
        cursor.execute(
            f"""
            INSERT INTO [{schema}].[vat_return_entries]
                (company_id, period, odoo_move_id, odoo_move_name, ref,
                 created_by, created_at, total_amount, line_count, dismissed)
            OUTPUT INSERTED.id
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 0)
            """,
            (
                company_id,
                period,
                odoo_move_id,
                odoo_move_name,
                ref,
                created_by,
                datetime.now(timezone.utc).replace(tzinfo=None),
                total_amount,
                line_count,
            ),
        )
        new_id = cursor.fetchone()[0]
        conn.commit()
        return int(new_id)
    finally:
        conn.close()


def dismiss_entry(entry_id: int, dismissed_by: Optional[str]) -> None:
    """Markeer een audittrail-rij als dismissed (lock released)."""
    conn, schema = _connect()
    try:
        cursor = conn.cursor()
        cursor.execute(
            f"""
            UPDATE [{schema}].[vat_return_entries]
            SET dismissed = 1, dismissed_by = ?, dismissed_at = ?
            WHERE id = ?
            """,
            (
                dismissed_by,
                datetime.now(timezone.utc).replace(tzinfo=None),
                entry_id,
            ),
        )
        conn.commit()
    finally:
        conn.close()
