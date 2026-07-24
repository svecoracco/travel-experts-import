"""Divers Excel reader — poort van
`travel-experts-backend/apps/main/app/plugins/divers/excel_reader.py`
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
    "Vat code",
    "Reference",
    "Traveller",
    "Agent code",
    "Supplier",
]

# Verplichte kolommen voor validatie (lowercase voor case-insensitieve matching)
REQUIRED_COLUMNS = [
    "reference",
    "invoice date",
    "invoice number",
    "net amount",
    "ledger account",
    "vat code",
    "file number",
    "supplier code",
]

# Canonieke kolomnaam-mapping: lowercase-gestript → canonieke naam
_CANONICAL = {
    "reference": "Reference",
    "invoice date": "Invoice date",
    "invoice number": "Invoice number",
    "net amount": "net amount",
    "ledger account": "ledger account",
    "vat code": "Vat code",
    "file number": "File number",
    "supplier code": "Supplier code",
    "traveller": "Traveller",
    "departure date": "Departure date",
    "agent code": "Agent code",
    "supplier": "Supplier",
}


def _to_str(x: Any) -> str:
    if x is None:
        return ""
    try:
        if pd.isna(x):
            return ""
    except (TypeError, ValueError):
        pass
    return str(x).strip()


def _normalize_columns(df: pd.DataFrame) -> pd.DataFrame:
    """Hernoem kolommen naar canonieke namen via case-insensitieve matching."""
    raw_cols = {str(c).strip().lower(): str(c).strip() for c in df.columns}
    rename_map = {}
    for raw_lower, raw_orig in raw_cols.items():
        canonical = _CANONICAL.get(raw_lower)
        if canonical and raw_orig != canonical:
            rename_map[raw_orig] = canonical
    if rename_map:
        df = df.rename(columns=rename_map)
    return df


def read_divers_excel(path: str) -> pd.DataFrame:
    """Lees een Divers Excel-export en retourneer een genormaliseerde DataFrame.

    - Kolomnamen worden genormaliseerd naar canonieke namen (case-insensitief)
    - Tekstkolommen blijven strings
    - Invoice date / Departure date worden geparsed als datums met dayfirst=True
    - net amount wordt omgezet naar float
    - ledger account wordt omgezet naar numeriek
    """
    converters = {c: _to_str for c in _TEXT_COLS}
    df = pd.read_excel(path, converters=converters)

    # Strip whitespace uit kolomnamen
    df.columns = [str(c).strip() for c in df.columns]

    # Normaliseer naar canonieke namen
    df = _normalize_columns(df)

    # Parse datumkolommen met dayfirst=True (Europees formaat)
    for col in ("Invoice date", "Departure date"):
        if col in df.columns:
            df[col] = pd.to_datetime(df[col], dayfirst=True, errors="coerce")

    # Numeriek bedrag
    if "net amount" in df.columns:
        df["net amount"] = pd.to_numeric(df["net amount"], errors="coerce")

    # Numerieke accountcode
    if "ledger account" in df.columns:
        df["ledger account"] = pd.to_numeric(df["ledger account"], errors="coerce")

    return df


def validate_divers_columns(path: str) -> tuple[bool, list[str]]:
    """Controleer dat het bestand alle verplichte kolommen heeft (case-insensitief).

    Retourneert (ok, missing_cols).
    """
    try:
        df = pd.read_excel(path, nrows=0)
        actual = {str(c).strip().lower() for c in df.columns}
        missing = [c for c in REQUIRED_COLUMNS if c not in actual]
        return (len(missing) == 0), missing
    except Exception as exc:
        return False, [str(exc)]
