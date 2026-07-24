"""Odoo-settings-sync (analytic accounts / companies / partners → SQL Server).

Poort van `travel-experts-backend/integrations/odoo.py`. Al pakket-gebaseerd
in de bron (`from odoo import OdooClient`); herschreven om de client via
`odoo_conn.get_client()` te bouwen (unificeert de constructie op één plek) en
het hardgecodeerde schema `"bts"` te vervangen door `env.ENV.db_schema`
(harde projectregel #7).
"""

from __future__ import annotations

import json
import logging
from enum import Enum

import pandas as pd

import odoo_conn
from syncs.db import clean_db_table, write_df_to_db


class OdooSettingType(Enum):
    ANALYTICAL_ACCOUNTS = "analytical_accounts"
    COMPANIES = "companies"
    PARTNERS = "partners"


def extract_values_id_column(
    row: dict, column_name: str, id_col: str, name_col: str | None = None
) -> dict:
    if name_col is not None and name_col == "display_name":
        name_col = id_col.replace("_id", "") + "_name"

    if isinstance(row[column_name], list):
        if name_col is None:
            print("name_col is None, skipping")
        elif len(row[column_name]) > 1:
            row[name_col] = row[column_name][1]
        else:
            row[name_col] = None
        row[id_col] = row[column_name][0]
    else:
        row[id_col] = row[column_name]
        row[name_col] = None
    return row


def extract_list_values_id_column(
    row: dict, column_name: str, id_col: str, name_col: str | None = None
):
    if isinstance(row[column_name], list):
        if isinstance(row[column_name][0], dict):
            row[id_col] = [item["id"] for item in row[column_name]]
            if name_col is not None:
                row[name_col] = [item["display_name"] for item in row[column_name]]
            else:
                row[name_col] = None
        else:
            return row
    else:
        row[id_col] = row[column_name]
        row[name_col] = None
    return row


def transform_settings(df: pd.DataFrame, setting: str) -> pd.DataFrame:
    if setting == "analytical_accounts":
        columns_to_transform = {
            "company_id": ("display_name",),
            "currency_id": ("currency_code",),
            "partner_id": ("display_name",),
            "plan_id": ("display_name",),
        }
    elif setting == "accounts":
        columns_to_transform = {
            "company_ids": ("company_ids",),
        }
    elif setting == "companies":
        columns_to_transform = {
            "company_id": ("company_name",),
        }
    else:
        columns_to_transform = {
            "company_id": ("company_name",),
        }

    def apply_transformations(row):
        for col, (name_col,) in columns_to_transform.items():
            if col in row:
                if col == "company_ids":
                    row = extract_list_values_id_column(
                        row, "company_ids", "company_ids", "company_names"
                    )
                else:
                    row = extract_values_id_column(row, col, col, name_col)
        return row

    df = df.apply(apply_transformations, axis=1)

    for col in df.columns:
        if df[col].apply(lambda x: isinstance(x, dict) or isinstance(x, list)).any():
            df[col] = df[col].apply(
                lambda x: json.dumps(x) if isinstance(x, (dict, list)) else x
            )

    return df


def odoo_get_all_records(odoo_model):
    domain = []
    offset = 0
    limit = 1000
    fields = odoo_model._default_fields
    data_list = []
    while True:
        data = odoo_model.search_read(
            domain, fields, offset=offset, limit=limit, order="id asc"
        )
        if not data:
            break
        data_list.extend(data)
        offset += limit
    return data_list


def odoo_fetch_settings(odoo_client, setting_type: OdooSettingType | None = None):
    if setting_type is None:
        raise ValueError("setting_type must be specified")
    elif setting_type == OdooSettingType.ANALYTICAL_ACCOUNTS:
        odoo_model = odoo_client.analytic_accounts
    elif setting_type == OdooSettingType.COMPANIES:
        odoo_model = odoo_client.companies
    elif setting_type == OdooSettingType.PARTNERS:
        odoo_model = odoo_client.partners
    else:
        raise ValueError(f"Invalid setting_type: {setting_type}")

    return odoo_get_all_records(odoo_model)


def odoo_sync_settings() -> bool:
    from env import ENV

    settings_list = [setting_type.value for setting_type in OdooSettingType]
    for setting in settings_list:
        try:
            odoo_client = odoo_conn.get_client()
            data = odoo_fetch_settings(odoo_client, OdooSettingType(setting))
            if len(data) > 0:
                df = pd.DataFrame(data)
                df = transform_settings(df, setting)
                table_name = f"odoo_{setting}"
                clean_db_table(ENV.db_schema, table_name)
                # LET OP (bugfix t.o.v. de bron): de bron riep dit aan als
                # `write_df_to_db(df, f"odoo_{setting}", if_exists="append", schema="bts")`
                # — dat botst (positioneel `f"odoo_{setting}"` bindt al aan
                # `schema`, plus het keyword `schema=...` erbovenop) en zou een
                # TypeError geven bij elke aanroep (stil verzwolgen door de
                # brede `except Exception` hieronder). Hier expliciet met
                # keyword-argumenten aangeroepen zodat de sync daadwerkelijk werkt.
                write_df_to_db(
                    df, schema=ENV.db_schema, db_table=table_name, if_exists="append"
                )
                logging.info(f"{setting} synced successfully")
        except Exception as e:
            logging.error(f"Error syncing {setting}: {e}")
            continue
    logging.info("Settings sync completed")
    return True
