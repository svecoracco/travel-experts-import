"""IbanFirst transform: FX-koppeling, valuta-resolutie, statement-line-builder.

Poort van
`travel-experts-backend/apps/main/app/plugins/ibanfirst/transform.py`.
Odoo-toegang (`resolve_currency_id`) herschreven naar `odoo_conn`.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

import odoo_conn
from plugins.ibanfirst.csv_reader import (
    COL_COMM,
    COL_COUNTERPARTY,
    COL_CREDIT,
    COL_CURRENCY,
    COL_DATE,
    COL_DEBIT,
    COL_DESC,
    COL_ID,
    COL_INTERNAL_REF,
    COL_TYPE,
)

FX_KEYWORD = "buy & sell"
ROUND_DECIMALS = 2

# Regex: extraheer bedrag + 3-letterige valuta uit het begin van de beschrijving
# bv. "6377.0000 USD EURUSD 1.1834000" -> (6377.0000, USD)
# bv. "11579.7500 GBP EURGBP 0.8637000" -> (11579.75, GBP)
_FX_DESC_RE = re.compile(r"^\s*([0-9]+(?:[.,][0-9]+)?)\s+([A-Z]{3})\b", re.IGNORECASE)


@dataclass
class IbanFirstConfig:
    """Config voor een IbanFirst-importrun."""

    company_id: int
    journal_id: int  # opgelost uit journal_map + bestandsvaluta
    file_currency: str  # bv. "EUR", "GBP"


def _safe_str(x: Any) -> str:
    if pd.isna(x):
        return ""
    return str(x).strip()


def _is_empty(x: Any) -> bool:
    return pd.isna(x) or _safe_str(x) == ""


def _sign(x: float) -> float:
    return -1.0 if x < 0 else 1.0


# ---------------------------------------------------------------------------
# Valuta-resolutie
# ---------------------------------------------------------------------------


def resolve_currency_id(
    client: Any,
    iso_code: str,
    cache: Dict[str, int],
) -> Optional[int]:
    """Zoek res.currency op via ISO-naam (bv. 'USD'). Retourneert Odoo-ID of None."""
    code = iso_code.strip().upper()
    if not code:
        return None
    if code in cache:
        return cache[code]

    res = odoo_conn.search_read(
        client,
        "res.currency",
        [("name", "=", code)],
        ["id", "name"],
        limit=1,
    )
    if not res:
        return None
    cid = res[0]["id"]
    cache[code] = cid
    return cid


# ---------------------------------------------------------------------------
# Bestandsvaluta-detectie
# ---------------------------------------------------------------------------


def detect_file_currency(df: pd.DataFrame) -> str:
    """Detecteer de basisvaluta van het bestand uit de Valuta-kolom (meest voorkomende waarde)."""
    if COL_CURRENCY not in df.columns:
        return "EUR"
    vals = df[COL_CURRENCY].dropna().str.strip().str.upper()
    vals = vals[vals != ""]
    if vals.empty:
        return "EUR"
    return str(vals.mode().iloc[0])


# ---------------------------------------------------------------------------
# FX-extractie en -koppeling
# ---------------------------------------------------------------------------


def extract_fx_from_description(desc: str) -> Tuple[float, str]:
    """Extraheer (bedrag, valutacode) uit een buy & sell-beschrijving.

    Voorbeeld: "6377.0000 USD EURUSD 1.1834000" -> (6377.00, "USD")
    Retourneert (0.0, "") indien niets gevonden.
    """
    s = _safe_str(desc)
    if not s:
        return 0.0, ""

    m = _FX_DESC_RE.match(s)
    if not m:
        return 0.0, ""

    raw = m.group(1).replace(" ", "")
    if "," in raw and "." not in raw:
        raw = raw.replace(",", ".")
    try:
        val = round(float(raw), ROUND_DECIMALS)
    except ValueError:
        return 0.0, ""

    currency = m.group(2).upper()
    return val, currency


def pair_fx_rows(df: pd.DataFrame) -> pd.DataFrame:
    """Detecteer FX-rijen en koppel ze aan niet-FX-rijen op dezelfde datum + bedrag.

    Voegt kolommen toe: _fx_currency, _fx_amount
    """
    # Bereken bedrag
    df["_amount"] = np.where(df[COL_DEBIT] != 0.0, df[COL_DEBIT], df[COL_CREDIT])

    # Initialiseer FX-kolommen
    df["_fx_currency"] = ""
    df["_fx_amount"] = 0.0

    # Extraheer FX-info uit buy & sell-rijen
    fx_mask = df[COL_TYPE].map(lambda t: FX_KEYWORD.lower() in _safe_str(t).lower())

    for idx in df.index[fx_mask]:
        amount, currency = extract_fx_from_description(df.at[idx, COL_DESC])
        if amount != 0.0 and currency:
            df.at[idx, "_fx_currency"] = currency
            df.at[idx, "_fx_amount"] = amount

    # Absoluut bedrag voor koppeling
    df["_abs_amount"] = np.round(np.abs(df["_amount"]), ROUND_DECIMALS)

    # Groepeer op (datum, abs_amount) en kopieer FX-info naar gekoppelde rijen
    grouped: Dict[Any, list] = {}
    for idx, row in df.iterrows():
        if pd.isna(row[COL_DATE]):
            continue
        key = (row[COL_DATE].date(), row["_abs_amount"])
        grouped.setdefault(key, []).append(idx)

    for (_d, _abs_amt), idxs in grouped.items():
        if len(idxs) < 2:
            continue

        src_idxs = [
            i
            for i in idxs
            if _safe_str(df.at[i, "_fx_currency"]) != ""
            and float(df.at[i, "_fx_amount"]) != 0.0
        ]
        missing_idxs = [
            i
            for i in idxs
            if _safe_str(df.at[i, "_fx_currency"]) == ""
            and float(df.at[i, "_fx_amount"]) == 0.0
        ]

        if not src_idxs or not missing_idxs:
            continue

        src = src_idxs[0]
        src_fx_abs = abs(float(df.at[src, "_fx_amount"]))
        src_fx_currency = _safe_str(df.at[src, "_fx_currency"])

        for tgt in missing_idxs:
            tgt_amount = float(df.at[tgt, "_amount"])
            df.at[tgt, "_fx_currency"] = src_fx_currency
            df.at[tgt, "_fx_amount"] = src_fx_abs * _sign(tgt_amount)

    # Ruim hulpkolom op
    df.drop(columns=["_abs_amount"], inplace=True)

    return df


# ---------------------------------------------------------------------------
# Statement-line-builder
# ---------------------------------------------------------------------------


def build_statement_lines(
    df: pd.DataFrame,
    config: IbanFirstConfig,
    odoo_client: Any,
    currency_cache: Dict[str, int],
) -> List[Tuple[Dict[str, Any], Dict[str, Any]]]:
    """Zet de geparste + FX-gekoppelde DataFrame om naar Odoo-bank-statement-line-payloads.

    Retourneert een lijst van (payload, meta)-tuples.
    """
    lines: List[Tuple[Dict[str, Any], Dict[str, Any]]] = []

    for _, row in df.iterrows():
        if pd.isna(row[COL_DATE]):
            continue

        identificatie = _safe_str(row[COL_ID])
        date_str = row[COL_DATE].date().isoformat()
        amount = float(row["_amount"])
        counterparty = _safe_str(row.get(COL_COUNTERPARTY, ""))
        desc = _safe_str(row.get(COL_DESC, ""))
        comm = _safe_str(row.get(COL_COMM, ""))
        internal_ref = _safe_str(row.get(COL_INTERNAL_REF, ""))
        tx_type = _safe_str(row.get(COL_TYPE, ""))

        # Bouw label: beschrijving indien beschikbaar, anders type + tegenpartij
        if desc:
            label = desc
        else:
            parts = [p for p in [tx_type, counterparty, comm] if p]
            label = " - ".join(parts) if parts else "IbanFirst transaction"

        # Bouw narration (notities) met volledige context
        note_parts = []
        if tx_type:
            note_parts.append(f"Type: {tx_type}")
        if counterparty:
            note_parts.append(f"Counterparty: {counterparty}")
        if comm:
            note_parts.append(f"Comm: {comm}")
        narration = " | ".join(note_parts)

        payload: Dict[str, Any] = {
            "journal_id": config.journal_id,
            "date": date_str,
            "payment_ref": label,
            "ref": identificatie,
            "amount": round(amount, ROUND_DECIMALS),
        }

        if counterparty:
            payload["partner_name"] = counterparty

        if internal_ref:
            payload["narration"] = (
                f"Ref: {internal_ref} | {narration}"
                if narration
                else f"Ref: {internal_ref}"
            )
        elif narration:
            payload["narration"] = narration

        # FX-velden — enkel gezet wanneer secundaire valuta afwijkt van de bestandsvaluta
        fx_currency = _safe_str(row.get("_fx_currency", ""))
        fx_amount = float(row.get("_fx_amount", 0.0))

        if (
            fx_currency
            and fx_amount != 0.0
            and fx_currency.upper() != config.file_currency.upper()
        ):
            currency_id = resolve_currency_id(odoo_client, fx_currency, currency_cache)
            if currency_id:
                # Odoo vereist dat amount_currency hetzelfde teken heeft als amount
                signed_fx = abs(fx_amount) if amount >= 0 else -abs(fx_amount)
                payload["foreign_currency_id"] = currency_id
                payload["amount_currency"] = round(signed_fx, ROUND_DECIMALS)

        meta = {
            "identificatie": identificatie,
            "type": tx_type,
            "counterparty": counterparty,
        }

        lines.append((payload, meta))

    return lines
