"""Move posting/reconciliation helpers — poort van
`travel-experts-backend/apps/main/app/shared/move_utils.py`.

Odoo-toegang herschreven naar de generieke `odoo_conn`-helpers (pakket-
transport). Gedrag 1-op-1 behouden, inclusief de "cannot marshal None"-
RPC-marshalling-eigenaardigheid (Odoo's `reconcile()` retourneert soms `None`,
wat de oude XML-RPC-laag niet kon marshallen — het pakket kan dit via JSON-2
wél, maar de except-tak blijft staan zodat een eventuele XML-RPC-fallback-pad
van het pakket zelf hetzelfde gedrag houdt).
"""

from __future__ import annotations

import logging
from typing import Any, List

import odoo_conn


def post_moves(
    client: Any,
    move_ids: List[int],
    company_id: int,
) -> None:
    """Boek moves via action_post."""
    ctx = {"allowed_company_ids": [company_id], "company_id": company_id}
    odoo_conn.call(client, "account.move", "action_post", [move_ids], {"context": ctx})


def reconcile_clearing_lines(
    client: Any,
    move_ids: List[int],
    clearing_account_id: int,
    company_id: int,
) -> None:
    """Zoek niet-gereconcilieerde lijnen op de clearing-account en reconcilieer ze.

    Gebruikt door de BSP-pipeline om invoice- + misc-entry-clearingregels te
    reconciliëren. Handelt de "cannot marshal None"-RPC-waarschuwing netjes af.
    """
    ctx = {"allowed_company_ids": [company_id], "company_id": company_id}
    lines = odoo_conn.search_read(
        client,
        "account.move.line",
        [
            ("move_id", "in", move_ids),
            ("account_id", "=", clearing_account_id),
            ("reconciled", "=", False),
        ],
        ["id", "balance", "debit", "credit", "account_id", "partner_id", "reconciled"],
    )
    line_ids = [ln["id"] for ln in lines]
    if len(line_ids) >= 2:
        try:
            odoo_conn.call(
                client, "account.move.line", "reconcile", [line_ids], {"context": ctx}
            )
            logging.info("Reconciled clearing lines: %s", line_ids)
        except Exception as exc:
            if "cannot marshal None" in str(exc):
                logging.warning("Reconcile returned None (RPC marshal warning)")
            else:
                raise
    else:
        logging.warning("Reconcile skipped (need >= 2 lines, got %s)", len(line_ids))


def reconcile_invoice_lines(
    client: Any,
    move_id: int,
    invoice_id: int,
    account_id: int,
    partner_id: int,
    company_id: int,
) -> None:
    """Reconcilieer een misc-entry-lijn tegen een invoice-lijn op een gedeelde account.

    Gebruikt door de Vivawallet-pipeline om betalingen aan facturen te matchen.
    """
    ctx = {"allowed_company_ids": [company_id], "company_id": company_id}
    lines = odoo_conn.search_read(
        client,
        "account.move.line",
        [
            ("move_id", "in", [move_id, invoice_id]),
            ("account_id", "=", account_id),
            ("partner_id", "=", partner_id),
            ("reconciled", "=", False),
        ],
        [
            "id",
            "balance",
            "debit",
            "credit",
            "account_id",
            "partner_id",
            "reconciled",
            "move_id",
        ],
    )
    if len(lines) >= 2:
        line_ids = [ln["id"] for ln in lines]
        try:
            odoo_conn.call(
                client,
                "account.move.line",
                "reconcile",
                [line_ids],
                {"context": ctx},
            )
            logging.info(
                "Reconciled invoice lines: move_id=%s invoice_id=%s (%d lines)",
                move_id,
                invoice_id,
                len(line_ids),
            )
        except Exception as exc:
            if "cannot marshal None" in str(exc):
                logging.warning(
                    "Reconcile returned None (RPC marshal warning) move_id=%s invoice_id=%s",
                    move_id,
                    invoice_id,
                )
            else:
                raise
    else:
        logging.warning(
            "Reconcile skipped: need 2 lines, got %s (move_id=%s invoice_id=%s)",
            len(lines),
            move_id,
            invoice_id,
        )


def reconcile_cash_clearing_lines(
    client: Any,
    invoice_ids: List[int],
    consolidated_misc_id: int,
    clearing_account_id: int,
    company_id: int,
) -> None:
    """Reconcilieer cash-invoice-clearingregels tegen de geconsolideerde misc-entry.

    Elke invoice heeft één clearingregel op de clearing-account. De
    geconsolideerde misc-entry heeft één clearingregel per invoice (gematcht
    op naam). We koppelen ze paarsgewijs en reconciliëren elk paar.
    """
    ctx = {"allowed_company_ids": [company_id], "company_id": company_id}

    invoice_lines = odoo_conn.search_read(
        client,
        "account.move.line",
        [
            ("move_id", "in", invoice_ids),
            ("account_id", "=", clearing_account_id),
            ("reconciled", "=", False),
        ],
        ["id", "move_id", "name", "balance", "partner_id"],
    )

    misc_lines = odoo_conn.search_read(
        client,
        "account.move.line",
        [
            ("move_id", "=", consolidated_misc_id),
            ("account_id", "=", clearing_account_id),
            ("reconciled", "=", False),
        ],
        ["id", "name", "balance"],
    )

    misc_by_name: dict[str, list] = {}
    for ml in misc_lines:
        name = ml.get("name", "")
        misc_by_name.setdefault(name, []).append(ml)

    reconciled_count = 0
    for inv_line in invoice_lines:
        inv_name = inv_line.get("name", "")
        candidates = misc_by_name.get(inv_name, [])
        if not candidates:
            logging.warning(
                "Cash reconcile: no matching misc line for invoice line id=%s name=%s",
                inv_line["id"],
                inv_name,
            )
            continue

        # Voorkeur voor de kandidaat wiens absolute balans matcht met de
        # absolute balans van de invoice-lijn — vangt gelijke-ref-rijen met
        # verschillende bedragen op (bv. een TKTT en RFND met hetzelfde
        # doc_number, dus dezelfde ref-naam).
        inv_bal = round(float(inv_line.get("balance") or 0.0), 2)
        best_idx = next(
            (
                i
                for i, c in enumerate(candidates)
                if round(abs(float(c.get("balance") or 0.0)), 2)
                == round(abs(inv_bal), 2)
            ),
            0,  # terugval naar de eerste kandidaat bij geen exacte match
        )
        misc_line = candidates.pop(best_idx)

        misc_bal = round(float(misc_line.get("balance") or 0.0), 2)

        if round(abs(inv_bal) - abs(misc_bal), 2) != 0.0:
            logging.warning(
                "Cash reconcile: amount mismatch inv_line=%s misc_line=%s inv_bal=%s misc_bal=%s",
                inv_line["id"],
                misc_line["id"],
                inv_bal,
                misc_bal,
            )
            continue

        try:
            odoo_conn.call(
                client,
                "account.move.line",
                "reconcile",
                [[inv_line["id"], misc_line["id"]]],
                {"context": ctx},
            )
            reconciled_count += 1
        except Exception as exc:
            if "cannot marshal None" in str(exc):
                reconciled_count += 1  # waarschijnlijk toch geslaagd
            else:
                logging.warning(
                    "Cash reconcile failed: inv_line=%s misc_line=%s: %s",
                    inv_line["id"],
                    misc_line["id"],
                    exc,
                )

    logging.info(
        "Cash clearing reconciliation: %d/%d pairs reconciled",
        reconciled_count,
        len(invoice_lines),
    )
