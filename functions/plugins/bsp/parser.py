"""BSP CSV parser — poort van
`travel-experts-backend/apps/main/app/plugins/bsp/parser.py` (1-op-1, zuivere
Python/csv-code, geen Odoo-toegang).
"""

from __future__ import annotations

import csv
import datetime as dt
import re
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Tuple

DEFAULT_DOC_TYPES = ("TKTT", "ADMA", "RFND", "CANX", "CANN", "EMDA", "EMDS", "SPCR")

# CSV-kolomnamen
COL_BSP = "BSP"
COL_AIRLINE_CODE = "Airline Code"
COL_AIRLINE_NAME = "Airline Name"
COL_DOC_TYPE = "Document Type"
COL_DOC_NUMBER = "Document Number"
COL_ISSUE_DATE = "Issue Date"
COL_PERIOD = "Period"
COL_CURRENCY = "Currency"
COL_CASH = "Cash"
COL_PAYMENT_CARD = "Payment Card"
COL_EASYPAY = "EasyPay"
COL_NET_TO_BE_PAID = "Net to be Paid"
COL_COMMENTS = "Comments"

REQUIRED_COLUMNS = (
    COL_DOC_TYPE,
    COL_DOC_NUMBER,
    COL_ISSUE_DATE,
    COL_CURRENCY,
    COL_CASH,
    COL_PAYMENT_CARD,
    COL_NET_TO_BE_PAID,
    COL_COMMENTS,
)

# Regex om card-brand + laatste 4 cijfers uit de Comments-kolom te halen
# Matcht: CC VI 492019XXXXXX2533 CA  of  CC AX 379345XXXXX6991 CA
CARD_CC_RE = re.compile(r"CC\s+([A-Z]{2})\s+\S*?(\d{4})\s+CA")


@dataclass(frozen=True)
class ParsedLine:
    doc_type: Optional[str]
    doc_number: Optional[str]  # 10-cijferig ticketnummer (was doc13)
    ticket_key: Optional[str]  # airline_code + doc_number voor SQL Server-lookup
    issue_date: Optional[str]  # ISO-formaat YYYY-MM-DD
    currency: Optional[str]
    total_amount: Optional[float]  # opgelost aankoopbedrag
    payment_ref: Optional[str]  # volledige CC-ref indien kaartbetaling
    card_ref_short: Optional[str]  # bv. "VI2533"
    card_prefix4: Optional[str]  # bv. "2533"
    airline_code: Optional[str]
    airline_name: Optional[str]
    period: Optional[str]  # bv. "2026011"
    payment_method: str  # "cash", "card", "split", "skip"
    card_amount: Optional[float]  # bedrag op card-GL (voor split-betalingen)
    cash_amount: Optional[float]  # bedrag op BSP Cash (voor split/cash-betalingen)
    raw_line: str


def validate_csv_columns(path: str) -> Tuple[bool, List[str]]:
    """Controleer dat verplichte kolommen bestaan in het CSV-bestand. Retourneert (valid, errors)."""
    with open(path, "r", encoding="utf-8-sig", errors="replace") as f:
        reader = csv.reader(f, delimiter=";")
        try:
            header = next(reader)
        except StopIteration:
            return False, ["Empty file"]

    header_stripped = [h.strip() for h in header]
    missing = [c for c in REQUIRED_COLUMNS if c not in header_stripped]
    if missing:
        return False, [f"Missing columns: {', '.join(missing)}"]
    return True, []


def count_data_rows(path: str) -> int:
    """Tel niet-header-rijen in de CSV."""
    with open(path, "r", encoding="utf-8-sig", errors="replace") as f:
        reader = csv.reader(f, delimiter=";")
        next(reader, None)  # sla header over
        return sum(1 for _ in reader)


def input_stem(path: str) -> str:
    return Path(path).stem


def parse_comment_card(
    comment: str,
) -> Tuple[Optional[str], Optional[str], Optional[str]]:
    """Extraheer kaartinfo uit de Comments-kolom.

    Retourneert (payment_ref, card_ref_short, card_prefix4).
    - "CC VI 492019XXXXXX2533 CA" → ("NCCVI2533", "VI2533", "2533")
    - "EX ... CC AX 379345XXXXX6991 CA" → ("NCCAX6991", "AX6991", "6991")
    - "CA ..." → (None, None, None)
    """
    m = CARD_CC_RE.search(comment)
    if not m:
        return None, None, None
    brand = m.group(1)
    last4 = m.group(2)
    card_ref_short = f"{brand}{last4}"
    # Bouw een payment_ref compatibel met de bestaande card_gl_from_payment-logica
    payment_ref = f"NCC{card_ref_short}"
    return payment_ref, card_ref_short, last4


def _parse_float(val: str) -> float:
    """Parse een float uit CSV, met lege strings als edge-case."""
    val = val.strip()
    if not val:
        return 0.0
    # EU-formaat (komma als decimaalteken)
    if "," in val and "." not in val:
        val = val.replace(",", ".")
    elif "," in val and "." in val:
        # bv. 1.234,56 → 1234.56
        val = val.replace(".", "").replace(",", ".")
    return float(val)


def resolve_amount(
    cash: float,
    card: float,
    easypay: float,
    net: float,
) -> Tuple[float, str, Optional[float], Optional[float]]:
    """Bepaal aankoopbedrag en betaalmethode.

    Retourneert (purchase_amount, payment_method, card_amount, cash_amount).

    payment_method is één van: "cash", "card", "split", "skip"
    """
    # Nulbedrag-rijen (CANX-voids, EX-uitwisselingen)
    if cash == 0.0 and card == 0.0 and net == 0.0:
        return 0.0, "skip", None, None

    # Cash-betaling (Card = 0)
    if cash != 0.0 and card == 0.0:
        # Uitzondering A: Cash != Net → gebruik Net (commissie afgetrokken)
        if abs(cash - net) > 0.001:
            return net, "cash", None, net
        return cash, "cash", None, cash

    # Card-betaling (Cash = 0)
    if card != 0.0 and cash == 0.0:
        # Uitzondering B: Card > 0 EN Net != 0 → split-betaling
        if abs(net) > 0.001:
            purchase_amount = round(card + net, 2)
            return purchase_amount, "split", card, net
        return card, "card", card, None

    # Beide niet-nul (niet gezien in de data, maar netjes afhandelen)
    if cash != 0.0 and card != 0.0:
        return round(cash + card, 2), "split", card, cash

    # Terugval: gebruik net
    if abs(net) > 0.001:
        return net, "cash", None, net

    return 0.0, "skip", None, None


def _parse_date(date_str: str) -> Optional[str]:
    """Parse een datumstring naar ISO-formaat YYYY-MM-DD. Ondersteunt DD/MM/YYYY en YYYY-MM-DD."""
    date_str = date_str.strip().strip('"')
    if not date_str:
        return None
    for fmt in ("%Y-%m-%d", "%d/%m/%Y"):
        try:
            d = dt.datetime.strptime(date_str, fmt).date()
            return d.isoformat()
        except ValueError:
            continue
    return None


def read_bsp_csv(path: str) -> List[ParsedLine]:
    """Lees een BSP-CSV-bestand en retourneer geparste lijnen."""
    results: List[ParsedLine] = []

    with open(path, "r", encoding="utf-8-sig", errors="replace") as f:
        reader = csv.DictReader(f, delimiter=";")
        for row in reader:
            raw_line = ";".join(row.get(k, "") for k in (row.keys() if row else []))

            doc_type = (row.get(COL_DOC_TYPE) or "").strip()
            doc_number = (row.get(COL_DOC_NUMBER) or "").strip()
            issue_date = _parse_date(row.get(COL_ISSUE_DATE, ""))
            currency = (row.get(COL_CURRENCY) or "").strip()
            period = (row.get(COL_PERIOD) or "").strip()
            airline_code = (row.get(COL_AIRLINE_CODE) or "").strip() or None
            airline_name = (row.get(COL_AIRLINE_NAME) or "").strip() or None
            comment = (row.get(COL_COMMENTS) or "").strip()

            cash = _parse_float(row.get(COL_CASH, "0"))
            card = _parse_float(row.get(COL_PAYMENT_CARD, "0"))
            easypay = _parse_float(row.get(COL_EASYPAY, "0"))
            net = _parse_float(row.get(COL_NET_TO_BE_PAID, "0"))

            purchase_amount, payment_method, card_amount, cash_amount = resolve_amount(
                cash,
                card,
                easypay,
                net,
            )

            payment_ref, card_ref_short, card_prefix4 = parse_comment_card(comment)

            # ticket_key: altijd 10 tekens — fetch_ticket_filenumbers kapt af tot
            # 10 en indexeert resultaten op de eerste 10 tekens van de DB, dus de
            # lookup moet matchen
            ticket_key = doc_number[:10] if doc_number else None

            results.append(
                ParsedLine(
                    doc_type=doc_type or None,
                    doc_number=doc_number or None,
                    ticket_key=ticket_key,
                    issue_date=issue_date,
                    currency=currency or None,
                    total_amount=purchase_amount if payment_method != "skip" else None,
                    payment_ref=payment_ref,
                    card_ref_short=card_ref_short,
                    card_prefix4=card_prefix4,
                    airline_code=airline_code,
                    airline_name=airline_name,
                    period=period or None,
                    payment_method=payment_method,
                    card_amount=card_amount,
                    cash_amount=cash_amount,
                    raw_line=raw_line,
                )
            )

    return results
