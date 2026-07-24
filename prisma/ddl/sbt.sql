-- ============================================================================
-- prisma/ddl/schema.template.sql â€” getemplatiseerde DDL (Fase 1, bevroren
-- contract). EÃ©n bron waaruit ELK client-schema wordt aangemaakt
-- (bv. `bts`, `clientb`) door de `sbt`-placeholder te vervangen.
--
-- Dit is het DDL dat de MENS op de fase-1-gate uitvoert tegen een test-schema
-- (en later, per client, bij de fase 8-onboarding). De agent voert dit NOOIT
-- zelf uit tegen een echte database (harde projectregel #1).
--
-- Vorm gespiegeld naar prisma/schema.prisma â€” zie dat bestand voor de volledige
-- toelichting op de kernontwerp-afwijkingen t.o.v. de letterlijke bron-DDL
-- (queued-status, progress_*-kolommen op import_jobs, blob_ref i.p.v.
-- file_path, updated_at op import_jobs). Retired t.o.v. de bron:
-- refresh_tokens, otp_codes (niet in dit bestand).
--
-- Substitutie (voorbeeld, PowerShell â€” pas sbt toe vÃ³Ã³r uitvoeren):
--   (Get-Content prisma/ddl/schema.template.sql -Raw) `
--     -replace '\{\{schema\}\}', 'bts' `
--     | Set-Content prisma/ddl/schema.bts.generated.sql
--   sqlcmd -S <server> -d <database> -i prisma/ddl/schema.bts.generated.sql
--
-- Idempotent: elk CREATE TABLE/INDEX/CONSTRAINT is guarded met een
-- IF NOT EXISTS-check, dus dit script kan veilig herhaald worden.
-- ============================================================================

-- ============================================================
-- 0. Schema zelf (idempotent aanmaken â€” zie ook de per-client
--    onboarding-runbook in het plan, Â§8, die dit als los stap benoemt)
-- ============================================================
IF NOT EXISTS (SELECT * FROM sys.schemas WHERE name = 'sbt')
    EXEC('CREATE SCHEMA sbt');
GO

-- ============================================================
-- 1. Users
-- ============================================================
IF NOT EXISTS (SELECT * FROM sys.tables WHERE schema_id = SCHEMA_ID('sbt') AND name = 'users')
CREATE TABLE sbt.users (
    id              INT IDENTITY(1,1) PRIMARY KEY,
    email           NVARCHAR(255)   NOT NULL,
    display_name    NVARCHAR(100)   NULL,
    role            NVARCHAR(20)    NOT NULL DEFAULT 'operator',
    is_active       BIT             NOT NULL DEFAULT 1,
    created_at      DATETIME2       NOT NULL DEFAULT SYSUTCDATETIME(),
    updated_at      DATETIME2       NOT NULL DEFAULT SYSUTCDATETIME(),
    last_login_at   DATETIME2       NULL,

    CONSTRAINT uq_users_email UNIQUE (email),
    CONSTRAINT ck_users_role CHECK (role IN ('admin', 'operator'))
);
GO

-- ============================================================
-- 2. App Config (replaces odoo.yml)
-- ============================================================
IF NOT EXISTS (SELECT * FROM sys.tables WHERE schema_id = SCHEMA_ID('sbt') AND name = 'app_config')
CREATE TABLE sbt.app_config (
    id              INT IDENTITY(1,1) PRIMARY KEY,
    company_id      INT             NOT NULL DEFAULT 0,
    script_name     NVARCHAR(50)    NOT NULL DEFAULT '',
    [key]           NVARCHAR(100)   NOT NULL,
    [value]         NVARCHAR(MAX)   NOT NULL,
    updated_at      DATETIME2       NOT NULL DEFAULT SYSUTCDATETIME(),
    updated_by      INT             NULL,

    CONSTRAINT uq_config_scope_key UNIQUE (company_id, script_name, [key]),
    CONSTRAINT fk_app_config_user FOREIGN KEY (updated_by)
        REFERENCES sbt.users (id)
);
GO

-- ============================================================
-- 3. Import Jobs (queue-based herontwerp â€” zie kop-comment)
-- ============================================================
IF NOT EXISTS (SELECT * FROM sys.tables WHERE schema_id = SCHEMA_ID('sbt') AND name = 'import_jobs')
CREATE TABLE sbt.import_jobs (
    id                  INT IDENTITY(1,1) PRIMARY KEY,
    plugin_name         NVARCHAR(50)    NOT NULL,
    company_id          INT             NOT NULL,
    status              NVARCHAR(20)    NOT NULL DEFAULT 'pending',
    file_name           NVARCHAR(255)   NOT NULL,
    blob_ref            NVARCHAR(500)   NOT NULL,
    dry_run             BIT             NOT NULL DEFAULT 0,
    accounting_date     DATE            NULL,
    original_entry_ref  NVARCHAR(255)   NULL,
    result_summary      NVARCHAR(MAX)   NULL,
    skip_report_path    NVARCHAR(500)   NULL,
    created_by          INT             NOT NULL,
    created_at          DATETIME2       NOT NULL DEFAULT SYSUTCDATETIME(),
    started_at          DATETIME2       NULL,
    completed_at        DATETIME2       NULL,
    updated_at          DATETIME2       NOT NULL DEFAULT SYSUTCDATETIME(),
    progress_phase      NVARCHAR(20)    NULL,
    progress_current    INT             NULL,
    progress_total      INT             NULL,
    progress_message    NVARCHAR(1000)  NULL,

    CONSTRAINT ck_import_jobs_status CHECK (status IN ('pending', 'queued', 'running', 'completed', 'failed')),
    CONSTRAINT fk_import_jobs_user FOREIGN KEY (created_by)
        REFERENCES sbt.users (id)
);
GO

-- ============================================================
-- 4. Audit Log (admin actions)
-- ============================================================
IF NOT EXISTS (SELECT * FROM sys.tables WHERE schema_id = SCHEMA_ID('sbt') AND name = 'audit_log')
CREATE TABLE sbt.audit_log (
    id              INT IDENTITY(1,1) PRIMARY KEY,
    actor_user_id   INT             NOT NULL,
    action          NVARCHAR(50)    NOT NULL,
    target_user_id  INT             NULL,
    before_value    NVARCHAR(255)   NULL,
    after_value     NVARCHAR(255)   NULL,
    created_at      DATETIME2       NOT NULL DEFAULT SYSUTCDATETIME(),

    CONSTRAINT fk_audit_log_actor FOREIGN KEY (actor_user_id)
        REFERENCES sbt.users (id),
    CONSTRAINT fk_audit_log_target FOREIGN KEY (target_user_id)
        REFERENCES sbt.users (id)
);
GO

IF NOT EXISTS (SELECT * FROM sys.indexes WHERE name = 'ix_audit_log_created_at')
CREATE NONCLUSTERED INDEX ix_audit_log_created_at
    ON sbt.audit_log (created_at DESC);
GO

-- ============================================================
-- 5. VAT Return Entries â€” Audit trail for booked correction entries
-- ============================================================
IF NOT EXISTS (SELECT * FROM sys.tables WHERE schema_id = SCHEMA_ID('sbt') AND name = 'vat_return_entries')
CREATE TABLE sbt.vat_return_entries (
    id              INT IDENTITY(1,1) PRIMARY KEY,
    company_id      INT             NOT NULL,
    period          NVARCHAR(7)     NOT NULL,           -- "2026-02"
    odoo_move_id    INT             NOT NULL,           -- Odoo account.move ID
    odoo_move_name  NVARCHAR(100)   NULL,               -- e.g. "MISC/2026/0042"
    ref             NVARCHAR(50)    NOT NULL,           -- "VAT-CORR-2026-02"
    created_by      NVARCHAR(100)   NULL,               -- user email from session
    created_at      DATETIME2       NOT NULL DEFAULT SYSUTCDATETIME(),
    total_amount    FLOAT           NULL,               -- net total of correction lines
    line_count      INT             NULL,               -- number of correction lines
    dismissed       BIT             NOT NULL DEFAULT 0,
    dismissed_by    NVARCHAR(100)   NULL,
    dismissed_at    DATETIME2       NULL
);
GO

IF NOT EXISTS (SELECT * FROM sys.indexes WHERE name = 'ix_vat_return_entries_company_id')
CREATE NONCLUSTERED INDEX ix_vat_return_entries_company_id
    ON sbt.vat_return_entries (company_id);
GO

-- Filtered unique index (allows multiple dismissed rows per company/period;
-- NOT representable in prisma/schema.prisma â€” see the note there).
IF NOT EXISTS (SELECT * FROM sys.indexes WHERE name = 'uq_vat_return_active_company_period')
CREATE UNIQUE NONCLUSTERED INDEX uq_vat_return_active_company_period
    ON sbt.vat_return_entries (company_id, period)
    WHERE dismissed = 0;
GO

-- ============================================================
-- 6. CSV Blob Sync Log
-- ============================================================
IF NOT EXISTS (SELECT * FROM sys.tables WHERE schema_id = SCHEMA_ID('sbt') AND name = 'csv_blob_sync_log')
CREATE TABLE sbt.csv_blob_sync_log (
    id              INT IDENTITY(1,1) PRIMARY KEY,
    blob_name       NVARCHAR(500)   NOT NULL,
    status          NVARCHAR(20)    NOT NULL DEFAULT 'pending',
    started_at      DATETIME2       NOT NULL DEFAULT SYSUTCDATETIME(),
    finished_at     DATETIME2       NULL,
    error_message   NVARCHAR(MAX)   NULL,
    row_count       INT             NULL,
    triggered_by    NVARCHAR(50)    NOT NULL DEFAULT 'cron',

    CONSTRAINT ck_csv_blob_sync_log_status CHECK (status IN ('pending', 'success', 'error')),
    CONSTRAINT ck_csv_blob_sync_log_triggered_by CHECK (triggered_by IN ('cron', 'manual'))
);
GO

IF NOT EXISTS (SELECT * FROM sys.indexes WHERE name = 'ix_csv_blob_sync_log_blob_name')
CREATE NONCLUSTERED INDEX ix_csv_blob_sync_log_blob_name
    ON sbt.csv_blob_sync_log (blob_name, started_at DESC);
GO

IF NOT EXISTS (SELECT * FROM sys.indexes WHERE name = 'ix_csv_blob_sync_log_status')
CREATE NONCLUSTERED INDEX ix_csv_blob_sync_log_status
    ON sbt.csv_blob_sync_log (status, started_at DESC);
GO

-- ============================================================
-- 7. User <-> Company assignments (multi-tenant access control)
-- ============================================================
IF NOT EXISTS (SELECT * FROM sys.tables WHERE schema_id = SCHEMA_ID('sbt') AND name = 'user_companies')
CREATE TABLE sbt.user_companies (
    user_id     INT             NOT NULL,
    company_id  INT             NOT NULL,
    created_at  DATETIME2       NOT NULL DEFAULT SYSUTCDATETIME(),

    CONSTRAINT pk_user_companies PRIMARY KEY (user_id, company_id),
    CONSTRAINT fk_user_companies_user FOREIGN KEY (user_id)
        REFERENCES sbt.users (id) ON DELETE CASCADE
);
GO

IF NOT EXISTS (SELECT * FROM sys.indexes WHERE name = 'ix_user_companies_company')
CREATE NONCLUSTERED INDEX ix_user_companies_company
    ON sbt.user_companies (company_id);
GO

PRINT 'All sbt tables created successfully.';
GO

