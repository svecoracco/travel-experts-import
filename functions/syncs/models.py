"""SQLAlchemy-model voor `csv_blob_sync_log` — poort van
`travel-experts-backend/apps/syncs/models.py`.

Het schema was hardcoded op ``"bts"`` in de bron — dat mag niet terugkeren
(harde projectregel #7: nooit een hardgecodeerde schemanaam). Hier komt het
uit `env.ENV.db_schema` (al gevalideerd tegen een identifier-regex bij import
van `env`). Omdat SQLAlchemy `__table_args__` op class-definitietijd nodig
heeft, vereist het simpelweg *importeren* van deze module (in tegenstelling
tot de meeste andere modules in `functions/`) al een volledige Track-A-
omgeving — dat is aanvaardbaar hier, want deze module wordt uitsluitend door
de syncs-feature gebruikt, die toch al de volledige env nodig heeft.

**Reconciliatie tegen de echte DDL** (`prisma/ddl/schema.template.sql` §6,
zie ook `prisma/schema.prisma`-kop-comment "Bron-inconsistentie"): de
bron-SQLAlchemy-model declareerde `blob_name` als UNIQUE en een extra kolom
`synced_to_table` die NIET in de gedeployde DDL voorkomt. Zoals het
fase-1-contract voorschreef ("Track A: reconcilieer dit bij het porten van
`syncs/` in fase 2 tegen de echte deployed DB-vorm") is dit hier gecorrigeerd:
géén `unique=True`, géén `synced_to_table`-kolom — anders zou elke
SELECT/INSERT tegen de echte tabel falen met "invalid column name". Zie ook
`queries.py`/`csv_blob_sync.py` waar de aanroepen dienovereenkomstig zijn
aangepast.
"""

from __future__ import annotations

from sqlalchemy import (
    Column,
    DateTime,
    Integer,
    String,
    Text,
)
from sqlalchemy.ext.declarative import declarative_base

from env import ENV

Base = declarative_base()


class CsvBlobSyncLog(Base):
    __tablename__ = "csv_blob_sync_log"
    __table_args__ = {"schema": ENV.db_schema}

    id = Column(Integer, primary_key=True, autoincrement=True)
    blob_name = Column(String(500), nullable=False)
    status = Column(String(20), nullable=False, default="pending")
    started_at = Column(DateTime, nullable=False)
    finished_at = Column(DateTime, nullable=True)
    error_message = Column(Text, nullable=True)
    row_count = Column(Integer, nullable=True)
    triggered_by = Column(String(50), nullable=False, default="cron")
