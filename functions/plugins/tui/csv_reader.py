"""TUI-bestandslezer — ``#``-gescheiden ``.csv`` (geen header) of ``.txt``
(met headerrij). Poort van
`travel-experts-backend/apps/main/app/plugins/tui/csv_reader.py`
(1-op-1, zuivere pandas-code, geen Odoo-toegang).
"""

from __future__ import annotations

import pandas as pd

# Kolomnaam-mapping (0-geïndexeerde posities in de CSV)
COLUMN_NAMES = [
    "col_ignored_1",  # 0  - "1201"
    "col_group",  # 1  - Invoice-groeperingssleutel
    "col_comm_ref",  # 2  - Commissie-referentie
    "col_date1",  # 3  - Niet gebruikt
    "col_date2",  # 4  - Niet gebruikt
    "col_ticket_ref",  # 5  - Ticketreferentie voor SQL Server-lookup
    "col_departure",  # 6  - Vertrekdatum YYYYMMDD
    "col_description",  # 7  - Beschrijving of "Comm. NNN"
    "col_currency",  # 8  - Valutacode
    "col_ignored_2",  # 9  - Niet gebruikt
    "col_amount",  # 10 - Bedrag (punt-decimaal, kan negatief zijn)
    "col_ignored_3",  # 11 - Niet gebruikt
    "col_ignored_4",  # 12 - Niet gebruikt
    "col_ignored_5",  # 13 - Niet gebruikt
    "col_ignored_6",  # 14 - Niet gebruikt
    "col_ignored_7",  # 15 - "DOM"
]

EXPECTED_COL_COUNT = 16


def read_tui_csv(path: str) -> pd.DataFrame:
    """Lees een TUI ``#``-gescheiden bestand en retourneer een DataFrame.

    Ondersteunt twee varianten:
    - ``.csv``: geen headerrij — data begint op regel 1.
    - ``.txt``: heeft een tekst-headerrij (bv. ``supplierCode#agentCode#...``)
      die automatisch gedetecteerd en overgeslagen wordt. Kolomindices zijn
      identiek na het overslaan.

    Kolommen krijgen namen toegewezen uit :data:`COLUMN_NAMES`.
    ``col_amount`` wordt omgezet naar float; alle andere kolommen blijven strings.
    """
    df = pd.read_csv(
        path,
        sep="#",
        header=None,
        dtype=str,
        keep_default_na=False,
    )

    # Behandel bestanden met minder/meer kolommen dan verwacht
    if len(df.columns) < EXPECTED_COL_COUNT:
        for i in range(len(df.columns), EXPECTED_COL_COUNT):
            df[i] = ""
    elif len(df.columns) > EXPECTED_COL_COUNT:
        df = df.iloc[:, :EXPECTED_COL_COUNT]

    df.columns = COLUMN_NAMES

    # Sla headerrij over als het eerste veld niet-numeriek is.
    # CSV-bestanden beginnen met een numerieke waarde (bv. "1201"); TXT-bestanden
    # beginnen met een tekstlabel (bv. "supplierCode"), wat naar NaN wordt omgezet.
    if not df.empty and pd.isna(pd.to_numeric(df.iloc[0, 0], errors="coerce")):
        df = df.iloc[1:].reset_index(drop=True)

    # Zet bedrag om naar float
    df["col_amount"] = pd.to_numeric(df["col_amount"], errors="coerce").fillna(0.0)

    # Strip whitespace uit gebruikte tekstkolommen
    for col in [
        "col_group",
        "col_comm_ref",
        "col_ticket_ref",
        "col_departure",
        "col_description",
        "col_currency",
    ]:
        df[col] = df[col].str.strip()

    return df


def validate_tui_csv(path: str) -> tuple[bool, list[str]]:
    """Snelle validatie van een TUI-CSV-bestand.

    Retourneert ``(ok, error_messages)``.
    """
    errors: list[str] = []
    try:
        df = pd.read_csv(path, sep="#", header=None, dtype=str, nrows=5)
        if len(df.columns) < 11:
            errors.append(
                f"Expected at least 11 '#'-delimited columns, found {len(df.columns)}"
            )
    except Exception as exc:
        errors.append(f"Cannot parse file: {exc}")

    return (len(errors) == 0), errors
