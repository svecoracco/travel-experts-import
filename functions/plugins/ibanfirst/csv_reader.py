"""IbanFirst CSV reader met EU/US-getalformaat-auto-detectie — poort van
`travel-experts-backend/apps/main/app/plugins/ibanfirst/csv_reader.py`
(1-op-1, zuivere pandas/numpy-code, geen Odoo-toegang).
"""

from __future__ import annotations

import numpy as np
import pandas as pd

# IbanFirst Nederlandstalige kolomnamen
COL_ID = "Identificatie"
COL_DATE = "Boekingdatum"
COL_VALUE_DATE = "Valutadatum"
COL_STATE = "Toestand"
COL_TYPE = "Type"
COL_COUNTERPARTY = "Tegenpartij"
COL_DESC = "Beschrijving"
COL_COMM = "Communicatie"
COL_INTERNAL_REF = "Interne referentie"
COL_DEBIT = "Gedebiteerd bedrag"
COL_CREDIT = "Gecrediteerd bedrag"
COL_CURRENCY = "Valuta"
COL_INITIAL_AMOUNT = "Beginbedrag"
COL_INITIAL_CURRENCY = "Initial amount currency"
COL_FEES_AMOUNT = "Deducted fees amount"
COL_FEES_CURRENCY = "Deducted fees amount currency"

REQUIRED_COLUMNS = [COL_ID, COL_DATE, COL_CURRENCY]


def parse_number_series(series: pd.Series) -> pd.Series:
    """Parse numerieke strings in EU/US-formaten naar float.

    Ondersteunt: 1.234,56 / 1,234.56 / 620,88 / 620.88 / -12.10
    """
    s = series.map(lambda v: "" if pd.isna(v) else str(v)).str.strip()
    s = s.str.replace(" ", "", regex=False)

    def _to_float(v: str) -> float:
        if v == "" or v.lower() == "nan":
            return np.nan
        if "." in v and "," in v:
            if v.rfind(",") > v.rfind("."):
                v = v.replace(".", "").replace(",", ".")  # EU: 1.234,56
            else:
                v = v.replace(",", "")  # US: 1,234.56
        elif "," in v and "." not in v:
            v = v.replace(",", ".")  # decimale komma: 620,88
        try:
            return float(v)
        except ValueError:
            return np.nan

    return s.map(_to_float).fillna(0.0)


def read_ibanfirst_csv(path: str) -> pd.DataFrame:
    """Lees een IbanFirst enhanced CSV-bestand en retourneer een geparste DataFrame.

    Retourneert DataFrame met:
    - Boekingdatum geparsed als datetime
    - Gedebiteerd bedrag / Gecrediteerd bedrag geparsed als float
    - Alle overige kolommen als strings
    """
    df = pd.read_csv(path, sep=None, engine="python", dtype=str)

    # Zorg dat verplichte kolommen bestaan
    missing = [c for c in REQUIRED_COLUMNS if c not in df.columns]
    if missing:
        raise ValueError(f"Missing required columns: {', '.join(missing)}")

    # Minstens één bedrag-kolom moet bestaan
    has_debit = COL_DEBIT in df.columns
    has_credit = COL_CREDIT in df.columns
    if not has_debit and not has_credit:
        raise ValueError(f"CSV must have '{COL_DEBIT}' and/or '{COL_CREDIT}' columns")

    # Zorg dat optionele kolommen bestaan als leeg
    for col in [
        COL_TYPE,
        COL_COUNTERPARTY,
        COL_DESC,
        COL_COMM,
        COL_INTERNAL_REF,
        COL_DEBIT,
        COL_CREDIT,
    ]:
        if col not in df.columns:
            df[col] = ""

    # Parse datum
    df[COL_DATE] = pd.to_datetime(df[COL_DATE], errors="coerce")

    # Parse bedragen
    df[COL_DEBIT] = parse_number_series(df[COL_DEBIT])
    df[COL_CREDIT] = parse_number_series(df[COL_CREDIT])

    return df
