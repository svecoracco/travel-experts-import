"""Rail Excel reader — poort van
`travel-experts-backend/apps/main/app/plugins/rail/excel_reader.py`
(1-op-1, zuivere pandas-code, geen Odoo-toegang).
"""

from __future__ import annotations

import logging
from typing import List, Tuple

import pandas as pd

logger = logging.getLogger(__name__)

REQUIRED_COLUMNS = [
    "ISSUE_ID",
    "PNR",
    "TRANSACTION_PRICE",
    "NET_AMOUNT",
    "OFFICIAL_DOC_NUMBER",
]


def _parse_amount(val: object) -> float:
    """Parse een celwaarde naar float, met Europese komma-decimalen."""
    if val is None:
        return 0.0
    if isinstance(val, (int, float)):
        return float(val)
    s = str(val).strip().replace(" ", "").replace(",", ".")
    try:
        return float(s)
    except (ValueError, TypeError):
        return 0.0


def validate_rail_columns(path: str) -> Tuple[bool, List[str]]:
    """Controleer dat alle verplichte kolommen aanwezig zijn. Retourneert (ok, missing)."""
    try:
        df = pd.read_excel(path, nrows=0, dtype=str)
        cols = set(c.strip() for c in df.columns)
        missing = [c for c in REQUIRED_COLUMNS if c not in cols]
        return (len(missing) == 0, missing)
    except Exception as exc:
        return False, [str(exc)]


def count_data_rows(path: str) -> int:
    """Tel rijen waar OFFICIAL_DOC_NUMBER niet leeg is (= datarijen)."""
    try:
        df = pd.read_excel(path, dtype=str)
        df.columns = [c.strip() for c in df.columns]
        if "OFFICIAL_DOC_NUMBER" not in df.columns:
            return 0
        mask = df["OFFICIAL_DOC_NUMBER"].notna() & (
            df["OFFICIAL_DOC_NUMBER"].astype(str).str.strip() != ""
        )
        return int(mask.sum())
    except Exception:
        return 0


def read_rail_excel(path: str) -> pd.DataFrame:
    """Lees een Rail-ticket Excel-export en retourneer uitsluitend datarijen.

    Datarijen worden herkend aan een niet-lege OFFICIAL_DOC_NUMBER-waarde.
    Bedragkolommen worden geparsed naar float in twee hulpkolommen:
      _net_amount, _transaction_price
    """
    df = pd.read_excel(path, dtype=str)
    df.columns = [c.strip() for c in df.columns]

    if "OFFICIAL_DOC_NUMBER" not in df.columns:
        raise ValueError("Missing required column: OFFICIAL_DOC_NUMBER")

    # Behoud alleen datarijen (niet-lege OFFICIAL_DOC_NUMBER)
    mask = df["OFFICIAL_DOC_NUMBER"].notna() & (
        df["OFFICIAL_DOC_NUMBER"].astype(str).str.strip() != ""
    )
    df = df[mask].copy()
    df.reset_index(drop=True, inplace=True)

    # Parse bedragen naar numerieke hulpkolommen
    df["_net_amount"] = df["NET_AMOUNT"].apply(_parse_amount)
    df["_transaction_price"] = df["TRANSACTION_PRICE"].apply(_parse_amount)

    logger.info("[rail] read_rail_excel: %d data rows from %s", len(df), path)
    return df
