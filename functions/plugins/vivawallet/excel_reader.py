"""Vivawallet Excel reader — poort van
`travel-experts-backend/apps/main/app/plugins/vivawallet/excel_reader.py`
(1-op-1, zuivere pandas-code, geen Odoo-toegang).
"""

from __future__ import annotations

from typing import Any

import pandas as pd

TEXT_COLS = ["Order Code", "Merchant Reference", "Transaction ID", "Card Type"]
REQUIRED_COLS = ["Merchant Reference", "Amount", "NetAmount", "Clearance Date"]


def _to_str(x: Any) -> str:
    if pd.isna(x):
        return ""
    s = str(x).strip()
    # Vivawallet exporteert formule-cellen zoals ="8400165" — strip de wrapper
    if len(s) >= 3 and s.startswith('="') and s.endswith('"'):
        return s[2:-1].strip()
    return s


def read_viva_excel(path: str) -> pd.DataFrame:
    converters = {c: _to_str for c in TEXT_COLS}
    # data_only=False zorgt dat formuletekst (="waarde") leesbaar is wanneer
    # gecachete waarden ontbreken
    df = pd.read_excel(
        path,
        converters=converters,
        engine="openpyxl",
        engine_kwargs={"data_only": False},
    )

    missing = [c for c in REQUIRED_COLS if c not in df.columns]
    if missing:
        raise ValueError(f"Required columns not found in file: {', '.join(missing)}")

    # Normaliseer datetime-kolommen
    for col in ["Date", "Clearance Date"]:
        if col in df.columns:
            df[col] = pd.to_datetime(df[col], errors="coerce")

    # Zorg dat Time een string is (HH:MM:SS) voor sortering; indien het een
    # datetime/time-object is, zet het om naar string
    if "Time" in df.columns:
        df["Time"] = df["Time"].apply(_to_str)

    # Carry-forward voor beschrijvingsvelden (Merchant Reference niet forward-fillen)
    for c in ["Card Type", "Order Code"]:
        if c in df.columns:
            df[c] = df[c].replace("", pd.NA).ffill().fillna("")

    # Sorteer op Clearance Date dan Time (zoals afgesproken)
    if "Clearance Date" in df.columns and "Time" in df.columns:
        df = df.sort_values(
            by=["Clearance Date", "Time"], ascending=[True, True], kind="mergesort"
        ).reset_index(drop=True)

    return df
