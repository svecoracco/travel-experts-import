"""TUI import plugin — config, bestandsnaam-parsing, SQL-lookup en payload-builders.

Poort van
`travel-experts-backend/apps/main/app/plugins/tui/transform.py`.
Odoo-toegang (`lookup_currencies`) herschreven naar `odoo_conn`.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

import odoo_conn

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class TuiConfig:
    """Runtime-config voor de TUI-plugin, opgelost uit bts.app_config."""

    company_id: int
    purchase_journal_id: int  # tui_purchase_journal
    supplier_id: int  # tui_supplier_id (Odoo-partner-ID)
    glaccount_ticket: int  # GL-accountcode voor ticketlijnen
    glaccount_comm: int  # GL-accountcode voor commissielijnen
    vatcode: Optional[str]  # BTW-codenaam (bv. "0% MN") of None
    tui_table: str  # SQL Server-tabelnaam
    tui_ticket_col: str  # SQL Server-kolom voor ticketnummer
    accounting_date: Optional[str]  # Optionele YYYY-MM-DD-override


def _req_int(cfg: Dict[str, Any], key: str, company_id: int) -> int:
    val = cfg.get(key)
    if val is None or str(val).strip() == "":
        raise ValueError(
            f"Missing required TUI config key '{key}' for company_id={company_id}. "
            "Set it via Settings → Config."
        )
    return int(val)


def _req_str(cfg: Dict[str, Any], key: str, company_id: int) -> str:
    val = cfg.get(key)
    if val is None or str(val).strip() == "":
        raise ValueError(
            f"Missing required TUI config key '{key}' for company_id={company_id}. "
            "Set it via Settings → Config."
        )
    return str(val).strip()


def build_tui_config(cfg: Dict[str, Any], company_id: int) -> TuiConfig:
    return TuiConfig(
        company_id=company_id,
        purchase_journal_id=_req_int(cfg, "tui_purchase_journal", company_id),
        supplier_id=_req_int(cfg, "tui_supplier_id", company_id),
        glaccount_ticket=_req_int(cfg, "tui_glaccount_ticket", company_id),
        glaccount_comm=_req_int(cfg, "tui_glaccount_comm", company_id),
        vatcode=str(cfg.get("tui_vatcode") or "").strip() or None,
        tui_table=_req_str(cfg, "tui_table", company_id),
        tui_ticket_col=_req_str(cfg, "tui_ticket_col", company_id),
        accounting_date=str(cfg.get("accounting_date") or "").strip() or None,
    )


# ---------------------------------------------------------------------------
# Bestandsnaam-parsing
# ---------------------------------------------------------------------------


def parse_tui_filename(filename: str) -> tuple[str, str]:
    """Extraheer invoice_date en ref uit een TUI-bestandsnaam.

    Bestandsnaam-masker: ``DOM_JET_{group}_{datum}_{ref}_...``

    - Positie 3 (0-geïndexeerd na split op ``_``): datum YYYYMMDD → YYYY-MM-DD
    - Positie 4: factuurnummer → ``TUI-{position4}``

    Retourneert ``(invoice_date, tui_ref)``.
    Werpt ``ValueError`` als de bestandsnaam niet geparsed kan worden.
    """
    stem = filename.rsplit(".", 1)[0] if "." in filename else filename
    parts = stem.split("_")

    if len(parts) < 5:
        raise ValueError(
            f"Cannot parse TUI filename '{filename}': "
            f"expected at least 5 '_'-delimited parts, got {len(parts)}"
        )

    date_str = parts[3]
    if len(date_str) != 8 or not date_str.isdigit():
        raise ValueError(
            f"Cannot parse date from filename part '{date_str}' — expected YYYYMMDD"
        )

    invoice_date = f"{date_str[:4]}-{date_str[4:6]}-{date_str[6:8]}"
    tui_ref = f"TUI-{parts[4]}"

    return invoice_date, tui_ref


# ---------------------------------------------------------------------------
# Rij-classificatie
# ---------------------------------------------------------------------------

_COMM_RE = re.compile(r"^(?:C\.\s*)?Comm\.", re.IGNORECASE)
_COMM_NUM_RE = re.compile(r"Comm\.\s*(\d+)", re.IGNORECASE)


def is_commission_row(description: str) -> bool:
    """Retourneer True als de rij een commissielijn is (beschrijving begint met 'Comm.')."""
    return bool(_COMM_RE.match(description.strip()))


def extract_commission_number(description: str) -> Optional[str]:
    """Extraheer het numerieke deel uit een commissiebeschrijving zoals 'Comm. 102297620'."""
    m = _COMM_NUM_RE.search(description)
    return m.group(1) if m else None


def strip_leading_zeros(ticket_ref: str) -> str:
    """Strip leidende nullen uit een ticketreferentie via int-conversie."""
    try:
        return str(int(ticket_ref))
    except (ValueError, TypeError):
        return ticket_ref.strip()


# ---------------------------------------------------------------------------
# Label-builders
# ---------------------------------------------------------------------------


def build_ticket_label(
    ticket_ref_stripped: str,
    description: str,
    departure: str,
) -> str:
    """Bouw het label voor een ticket-invoice-lijn."""
    return f"TUI - Ticket {ticket_ref_stripped} - {description} departure: {departure}"


def build_commission_label(description: str, comm_ref: str) -> str:
    """Bouw het label voor een commissie-invoice-lijn."""
    return f"TUI - {description} Invoice: {comm_ref}"


# ---------------------------------------------------------------------------
# Valuta-lookup
# ---------------------------------------------------------------------------


def lookup_currencies(client: Any, names: List[str]) -> Dict[str, int]:
    """Bulk-lookup res.currency via naam. Retourneert {currency_name: currency_id}."""
    names = [n for n in names if n]
    if not names:
        return {}
    rows = odoo_conn.search_read(
        client,
        "res.currency",
        [("name", "in", names)],
        ["id", "name"],
    )
    return {str(r["name"]).strip(): int(r["id"]) for r in rows}


# ---------------------------------------------------------------------------
# Invoice-payload-builder
# ---------------------------------------------------------------------------


def build_invoice_payload(
    cfg: TuiConfig,
    col_group: str,
    tui_ref: str,
    invoice_date: str,
    currency_id: Optional[int],
    rows: List[Dict[str, Any]],
    ticket_gl_account_id: int,
    comm_gl_account_id: int,
    tax_id: Optional[int],
    analytic_map: Dict[str, int],
) -> Dict[str, Any]:
    """Bouw één purchase-invoice-payload (in_invoice of in_refund) voor een groep.

    Parameters
    ----------
    rows : lijst van dicts, elk met sleutels:
        col_amount, col_ticket_ref, col_departure, col_description,
        col_comm_ref, _is_commission, _search_key
    analytic_map : {search_key: analytic_account_id}
    """
    # Bepaal move_type uit het netto-groepsbedrag
    net_amount = sum(r["col_amount"] for r in rows)
    is_refund = net_amount < 0
    move_type = "in_refund" if is_refund else "in_invoice"

    # Bouw payment_reference (idempotentiesleutel)
    file_ref = tui_ref.replace("TUI-", "", 1)  # rauwe ref uit bestandsnaam
    payment_reference = f"TUI-{file_ref}-{col_group}"
    ref = f"TUI-{file_ref}-{col_group}"

    # Datumverwerking
    accounting_date = cfg.accounting_date or invoice_date

    # Bouw invoice-lijnen
    invoice_lines = []
    for row in rows:
        amount = row["col_amount"]
        is_comm = row["_is_commission"]

        # Voor in_refund: negeer bedragen (Odoo keert de boeking al om)
        price_unit = -amount if is_refund else amount

        # Bepaal GL-account
        account_id = comm_gl_account_id if is_comm else ticket_gl_account_id

        # Bouw label
        if is_comm:
            name = build_commission_label(row["col_description"], row["col_comm_ref"])
        else:
            ticket_stripped = strip_leading_zeros(row["col_ticket_ref"])
            departure = row["col_departure"]
            name = build_ticket_label(
                ticket_stripped, row["col_description"], departure
            )

        inv_line: Dict[str, Any] = {
            "account_id": account_id,
            "name": name,
            "quantity": 1.0,
            "price_unit": round(price_unit, 2),
        }

        # Analytic-verdeling
        search_key = row.get("_search_key", "")
        analytic_id = analytic_map.get(search_key) if search_key else None
        if analytic_id:
            inv_line["analytic_distribution"] = {str(analytic_id): 100.0}

        # Belasting
        if tax_id is not None:
            inv_line["tax_ids"] = [(6, 0, [tax_id])]

        invoice_lines.append((0, 0, inv_line))

    payload: Dict[str, Any] = {
        "move_type": move_type,
        "company_id": cfg.company_id,
        "invoice_date": invoice_date,
        "date": accounting_date,
        "partner_id": cfg.supplier_id,
        "journal_id": cfg.purchase_journal_id,
        "ref": ref,
        "payment_reference": payment_reference,
        "invoice_line_ids": invoice_lines,
    }

    if currency_id:
        payload["currency_id"] = currency_id

    return payload


# ---------------------------------------------------------------------------
# SQL Server-lookup (contains-LIKE-patroon, TUI-specifiek)
# ---------------------------------------------------------------------------


def _chunked(items: List[str], size: int):
    for i in range(0, len(items), size):
        yield items[i : i + size]


def fetch_tui_filenumbers(
    db_cfg: Dict[str, str],
    search_values: List[str],
    table: str,
    ticket_col: str,
    chunk_size: int = 500,
) -> Dict[str, str]:
    """Zoek file numbers via ``LIKE '%value%'``-contains-zoekopdracht.

    Retourneert ``{search_value: file_number}``.
    """
    from shared.sql_server import _open_cursor

    unique_values = sorted({v for v in search_values if v})
    if not unique_values or not table or not ticket_col:
        return {}

    if db_cfg.get("chunk_size"):
        try:
            chunk_size = int(db_cfg["chunk_size"])
        except ValueError:
            pass

    # Normaliseer sleutel: gedeelde module verwacht 'sql_connection_string'
    if "sql_connection_string" not in db_cfg or not db_cfg["sql_connection_string"]:
        db_cfg = dict(db_cfg, sql_connection_string=db_cfg.get("connection_string", ""))

    results: Dict[str, str] = {}
    logger.info(
        "[tui] SQL Server lookup: %d unique value(s) on %s.%s",
        len(unique_values),
        table,
        ticket_col,
    )

    with _open_cursor(db_cfg) as cursor:
        for chunk in _chunked(unique_values, chunk_size):
            conditions = " OR ".join([f"{ticket_col} LIKE ?"] * len(chunk))
            query = f"SELECT {ticket_col}, FileNumber FROM {table} WHERE {conditions}"
            params = ["%" + v + "%" for v in chunk]
            cursor.execute(query, params)

            for db_ticket, filenumber in cursor.fetchall():
                if db_ticket is None or filenumber is None:
                    continue
                ticket_str = str(db_ticket).strip()
                fn = str(filenumber).strip()
                for sv in chunk:
                    if sv in ticket_str and sv not in results:
                        results[sv] = fn

    logger.info(
        "[tui] SQL Server lookup: %d/%d matched",
        len(results),
        len(unique_values),
    )
    return results
