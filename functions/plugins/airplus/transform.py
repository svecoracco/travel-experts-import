"""Airplus transform — poort van
`travel-experts-backend/apps/main/app/plugins/airplus/transform.py`.

Odoo-toegang (`lookup_partners`, `lookup_currencies`) herschreven van de
oude generieke `OdooClient`-aanroepmethode naar `odoo_conn`. Payload-builders
(`build_invoice_payload`, `build_payout_misc_payload`) zijn zuivere
Python en ongewijzigd t.o.v. de bron — dit zijn de functies die de
payload-parity-harness (fase 2.4) vergelijkt.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

import odoo_conn
from shared.account_utils import resolve_account_id

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class AirplusConfig:
    """Runtime-config voor de Airplus-plugin, opgelost uit app_config + env."""

    company_id: int
    purchase_journal_id: int  # journal voor purchase invoices
    payment_journal_id: int  # journal voor de payout misc entry
    payout_gl_account: int  # tegenrekening (totale Airplus-payout)
    suppliers_gl_account: int  # AP/creditor-account gebruikt voor per-invoice misc lines


def _req_int(cfg: Dict[str, Any], key: str, company_id: int) -> int:
    val = cfg.get(key)
    if val is None or str(val).strip() == "":
        raise ValueError(
            f"Missing required Airplus config key '{key}' for company_id={company_id}. "
            "Set it via Settings → Config."
        )
    return int(val)


def build_airplus_config(cfg: Dict[str, Any], company_id: int) -> AirplusConfig:
    return AirplusConfig(
        company_id=company_id,
        purchase_journal_id=_req_int(cfg, "airplus_purchase_journal_id", company_id),
        payment_journal_id=_req_int(cfg, "airplus_payment_journal_id", company_id),
        payout_gl_account=_req_int(cfg, "airplus_payout_glaccount", company_id),
        suppliers_gl_account=_req_int(cfg, "airplus_suppliers_glaccount", company_id),
    )


# ---------------------------------------------------------------------------
# Bulk-lookups
# ---------------------------------------------------------------------------


def lookup_partners(
    client: Any,
    codes: List[str],
    company_id: int,
) -> Dict[str, int]:
    """Bulk-lookup res.partner via het 'ref'-veld.

    Retourneert {leverancier_code: partner_id}.
    """
    codes = [c for c in codes if c]
    if not codes:
        return {}

    result: Dict[str, int] = {}
    chunk_size = 200
    for i in range(0, len(codes), chunk_size):
        chunk = codes[i : i + chunk_size]
        rows = odoo_conn.search_read(
            client,
            "res.partner",
            [("ref", "in", chunk)],
            ["id", "ref"],
            context={"allowed_company_ids": [company_id]},
        )
        for row in rows:
            ref = str(row.get("ref") or "").strip()
            if ref:
                result[ref] = int(row["id"])

    logger.info(
        "[airplus] Partner lookup: %d unique codes, %d matched",
        len(codes),
        len(result),
    )
    return result


def lookup_currencies(
    client: Any,
    names: List[str],
) -> Dict[str, int]:
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
    result = {str(r["name"]).strip(): int(r["id"]) for r in rows}
    logger.info(
        "[airplus] Currency lookup: %d names, %d matched", len(names), len(result)
    )
    return result


# ---------------------------------------------------------------------------
# Payout-lijn (opgebouwd in build_moves voor de geconsolideerde payout misc entry)
# ---------------------------------------------------------------------------


@dataclass
class AirplusPayoutLine:
    """Eén lijn in de geconsolideerde Airplus-payout-misc-entry."""

    ref: str  # {Factuur Nummer}-{suffix} — uniek per rij, idempotentie-sleutel
    amount: float  # positief = standaard invoice, negatief = credit note
    partner_id: int
    currency_id: Optional[int]  # None als valuta niet opgelost
    naam: str = ""  # Reference — gebruikt om de 440000-lijnnaam op te bouwen
    factuur_nr: str = (
        ""  # Basis Factuur Nummer (zonder suffix) — gebruikt voor de 58x-tegenlijn
    )


# ---------------------------------------------------------------------------
# Invoice-payload-builder
# ---------------------------------------------------------------------------


def build_invoice_payload(
    cfg: AirplusConfig,
    partner_id: int,
    currency_id: Optional[int],
    gl_account_id: int,
    invoice_date: str,
    amount: float,
    label: str,
    payment_reference: str,
    analytic_distribution: Optional[Dict[str, float]],
    tax_id: Optional[int],
    accounting_date: Optional[str] = None,
) -> Dict[str, Any]:
    """Bouw een purchase-invoice- (in_invoice of in_refund) payload voor één rij.

    ``invoice_date`` komt altijd uit de Excel-``Bookings date``.
    ``accounting_date`` (optioneel) overschrijft het move-``date``-veld —
    nuttig bij het importeren van vorige-maand-aankopen die in de huidige
    periode moeten landen. Indien afwezig bepaalt Odoo zelf de boekingsdatum
    via zijn lock-date-regels.
    """
    abs_amount = abs(amount)
    move_type = "in_refund" if amount < 0 else "in_invoice"

    inv_line: Dict[str, Any] = {
        "account_id": gl_account_id,
        "name": label,
        "quantity": 1,
        "price_unit": round(abs_amount, 2),
    }
    if analytic_distribution:
        inv_line["analytic_distribution"] = analytic_distribution
    if tax_id is not None:
        inv_line["tax_ids"] = [(6, 0, [tax_id])]

    payload: Dict[str, Any] = {
        "move_type": move_type,
        "company_id": cfg.company_id,
        "invoice_date": invoice_date,
        "date": accounting_date if accounting_date else invoice_date,
        "partner_id": partner_id,
        "journal_id": cfg.purchase_journal_id,
        "ref": label,
        "payment_reference": payment_reference,
        "invoice_line_ids": [(0, 0, inv_line)],
    }
    if currency_id:
        payload["currency_id"] = currency_id

    return payload


# ---------------------------------------------------------------------------
# Payout-misc-entry-builder
# ---------------------------------------------------------------------------


def build_payout_misc_payload(
    payout_lines: List[AirplusPayoutLine],
    cfg: AirplusConfig,
    entry_date: str,
    period_label: str,
    account_id_cache: Dict[int, int],
    client: Any,
) -> Dict[str, Any]:
    """Bouw één geconsolideerde misc-entry voor alle Airplus-invoices.

    Structuur (standaard-invoice-bedragen):
      Debit:  airplus_suppliers_glaccount   bedrag1  partner=A  naam=factuur1
      Debit:  airplus_suppliers_glaccount   bedrag2  partner=B  naam=factuur2
      ...
      Credit: airplus_payout_glaccount      totaal              naam=AIRPLUS-PAYOUT-{periode}

    Credit notes (bedrag < 0) draaien debit/credit-zijden om.
    De payout-tegenlijn is altijd het nettototaal op airplus_payout_glaccount.
    """
    suppliers_account_id = resolve_account_id(
        client,
        cfg.suppliers_gl_account,
        cfg.company_id,
        account_id_cache,
    )
    payout_account_id = resolve_account_id(
        client,
        cfg.payout_gl_account,
        cfg.company_id,
        account_id_cache,
    )

    line_ids = []
    net_total = 0.0

    for pl in payout_lines:
        amount = pl.amount
        net_total += amount

        if amount >= 0:
            debit, credit = round(amount, 2), 0.0
        else:
            debit, credit = 0.0, round(abs(amount), 2)

        # 440000-lijnnaam: "{Reference} - {Factuur Nummer}-{suffix}"
        supplier_line_name = f"{pl.naam} - {pl.ref}" if pl.naam else pl.ref

        supplier_line: Dict[str, Any] = {
            "account_id": suppliers_account_id,
            "name": supplier_line_name,
            "debit": debit,
            "credit": credit,
            "partner_id": pl.partner_id,
        }
        if pl.currency_id:
            supplier_line["currency_id"] = pl.currency_id

        line_ids.append((0, 0, supplier_line))

    # Tegenlijn: netto payout aan Airplus (tegenovergestelde richting van supplier-lijnen)
    # Naam = basis Factuur Nummer (gedeeld over alle rijen — de Airplus-betaalreferentie).
    net_total = round(net_total, 2)
    base_factuur_nr = payout_lines[0].factuur_nr if payout_lines else ""
    payout_ref = base_factuur_nr or f"AIRPLUS-{period_label}"

    if net_total >= 0:
        payout_debit, payout_credit = 0.0, net_total
    else:
        payout_debit, payout_credit = abs(net_total), 0.0

    line_ids.append(
        (
            0,
            0,
            {
                "account_id": payout_account_id,
                "name": payout_ref,
                "debit": round(payout_debit, 2),
                "credit": round(payout_credit, 2),
            },
        )
    )

    return {
        "move_type": "entry",
        "company_id": cfg.company_id,
        "date": entry_date,
        "journal_id": cfg.payment_journal_id,
        "ref": payout_ref,
        "line_ids": line_ids,
    }
