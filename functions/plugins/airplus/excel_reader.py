"""Airplus Excel reader — poort van
`travel-experts-backend/apps/main/app/plugins/airplus/excel_reader.py`
(1-op-1, zuivere pandas-code, geen Odoo-toegang).
"""

from __future__ import annotations

from typing import Any

import pandas as pd

# Kolommen die als platte strings gelezen moeten worden (niet omgezet naar
# getallen/datums)
_TEXT_COLS = [
    "Invoice number",
    "File number",
    "Supplier code",
    "Currency",
    "VAT code",
    "Invoice Type",
    "Bookings ref",
    "Traveller",
    "Reference",
    "Supplier",
    "Agency",
]

# Verplichte kolommen voor validatie
REQUIRED_COLUMNS = [
    "Reference",
    "Invoice number",
    "File number",
    "Supplier code",
    "Bookings date",
    "Total amount",
    "Currency",
    "Ledger account",
]


def _to_str(x: Any) -> str:
    if x is None:
        return ""
    try:
        if pd.isna(x):
            return ""
    except (TypeError, ValueError):
        pass
    return str(x).strip()


def read_airplus_excel(path: str) -> pd.DataFrame:
    """Lees een Airplus Excel-export en retourneer een genormaliseerde DataFrame.

    - Kolomnamen worden gestript van omringende whitespace
    - Tekstkolommen blijven strings
    - Bookings date / Vertrekdatum worden geparsed als datums
    - Total amount wordt omgezet naar float
    """
    converters = {c: _to_str for c in _TEXT_COLS}
    df = pd.read_excel(path, converters=converters)

    # Normaliseer kolomnamen
    df.columns = [str(c).strip() for c in df.columns]

    # Parse datumkolommen
    for col in ("Bookings date", "Vertrekdatum"):
        if col in df.columns:
            df[col] = pd.to_datetime(df[col], dayfirst=True, errors="coerce")

    # Numeriek bedrag
    if "Total amount" in df.columns:
        df["Total amount"] = pd.to_numeric(df["Total amount"], errors="coerce")

    # Numerieke accountcode
    if "Ledger account" in df.columns:
        df["Ledger account"] = pd.to_numeric(df["Ledger account"], errors="coerce")

    return df


def validate_airplus_columns(path: str) -> tuple[bool, list[str]]:
    """Controleer dat het bestand alle verplichte kolommen heeft. Retourneert (ok, missing_cols)."""
    try:
        df = pd.read_excel(path, nrows=0)
        df.columns = [str(c).strip() for c in df.columns]
        missing = [c for c in REQUIRED_COLUMNS if c not in df.columns]
        return (len(missing) == 0), missing
    except Exception as exc:
        return False, [str(exc)]
