# Contracts — bevroren koppelvlak tussen `functions/` (Track A) en `web/` (Track B)

> **Status: bevroren in Fase 1.** Alleen Fase 1 wijzigt dit bestand (samen met
> `prisma/schema.prisma`). Latere wijziging = expliciet afstemmen vóór de
> andere track het nodig heeft (zie de harde projectregels in het plan,
> `docs/github-agents-plan-writer-agent-md-foll-whimsical-lecun.md`).
>
> Db-tabelvormen (de derde "gedeelde koppelvlak") staan in
> `prisma/schema.prisma` + `prisma/ddl/schema.template.sql`, niet hier —
> dit document verwijst er alleen naar.

## Inhoud

1. [Queue-message-payload](#1-queue-message-payload)
2. [Function-HTTP-DTO's](#2-function-http-dtos)
3. [Import-status (DB-gemedieerd, geen Function-HTTP)](#3-import-status-db-gemedieerd-geen-function-http)
4. [Function-key-conventie](#4-function-key-conventie)
5. [Schema-resolutie (spike-uitkomst)](#5-schema-resolutie-spike-uitkomst)
6. [Schema-resolutie per track](#6-schema-resolutie-per-track)
7. [Amendementen na fase 1 (orchestrator)](#7-amendementen-na-fase-1-orchestrator)

---

## 0. Notatieconventie (belangrijk — lees eerst)

Er zijn **twee verschillende wire-casing-conventies** in dit contract, bewust
en niet per ongeluk:

| Koppelvlak                                              | Casing       | Waarom                                                                                                    |
| --------------------------------------------------------- | ------------ | ----------------------------------------------------------------------------------------------------------- |
| Queue-message-payload (§1)                                 | `camelCase`  | Letterlijk zo gespecificeerd in het plan (`{ jobId, companyId, script, blobRef }`) — niet wijzigen.        |
| Function-HTTP-DTO's: VAT/sbmov/translation-check (§2)      | `snake_case` | 1-op-1 portvan de bestaande Flask-JSON-vormen (`company_id`, `correction_lines`, …) — minimaliseert portrisico/transformatiebugs t.o.v. `apps/main/app/{vat_return,sbmov,translation_check}`. |
| Import-status-DTO (§3, `import_jobs`-rij als JSON)          | `camelCase`  | Puur Track B-intern (Prisma-model → Next.js-route → frontend); geen Python-kant leest dit via HTTP.        |

Track A en Track B mogen deze casing **niet** "normaliseren" zonder de andere
track én dit document tegelijk aan te passen (buiten fase 1 verboden, zie
boven).

---

## 1. Queue-message-payload

**Richting:** `web` (enqueue, Next.js server-side) → Azure Storage Queue →
`functions` (queue-trigger, verwerking).

**Queue-technologie:** Azure Storage Queue (beslissing #2 in het plan;
Service Bus alleen bij toekomstige ordering/dead-letter-eisen).

**Queue-naam (voorstel, vast te leggen in App Settings van beide apps):**
`import-jobs` — env var `AZURE_QUEUE_IMPORT_JOBS_NAME` (default `import-jobs`
als niet gezet) in zowel `web` als `functions`. Beide apps verbinden met
**dezelfde** storage account (`AZURE_QUEUE_ACCOUNT_URL` +
`AZURE_QUEUE_ACCOUNT_CREDENTIAL`/managed identity — zie
`local.settings.template.json`); dit is **niet** de function-key-route (§4) —
web schrijft de queue-message rechtstreeks met de Azure Storage Queue SDK, de
functions-host wordt automatisch getriggerd door de queue-binding (geen
HTTP-call, geen function-key nodig voor dit pad).

> **Amendement C (na fase 3, zie §7):** de functions-**queue-trigger** bindt via
> `connection="AzureWebJobsStorage"` (app-setting-naam, platform-resolved), terwijl
> **web** enqueue't via de Storage-Queue-SDK met `AZURE_QUEUE_ACCOUNT_URL/CREDENTIAL`.
> Deployment-eis: beide moeten naar hetzelfde storage-account + dezelfde queue-naam wijzen.

### Payload-vorm (minimaal, uitbreidbaar met optionele velden)

**TypeScript (Track B — bron van de payload):**

```ts
// web/src/lib/functions-client.ts (of gelijkwaardig, fase 5)
export interface ImportQueueMessage {
  /** import_jobs.id (Prisma ImportJob.id) — primaire correlatiesleutel */
  jobId: number;
  /** Company binnen de deployment (app_config-scope, NIET het client-schema) */
  companyId: number;
  /** = ImportJob.pluginName = AppConfig.scriptName voor deze job
   *  (bv. "airplus", "bsp", "tui", "ibanfirst", "vivawallet", "rail",
   *  "commission", "divers"). Zelfde waarde, drie namen naargelang context —
   *  zie de kop-comment in prisma/schema.prisma. */
  script: string;
  /** Azure Blob-pad/naam van het geüploade bestand (zelfde waarde als
   *  ImportJob.blobRef in de DB — geen aparte lookup nodig). */
  blobRef: string;
  /** Optioneel: idempotentie-/redelivery-guard aan de queue-consumer-kant;
   *  de functie MOET zelf ook een job-status-guard toepassen
   *  (skip als import_jobs.status niet meer 'queued' is — zie risico's-tabel
   *  in het plan: "Dubbele queue-delivery → dubbele boekingen"). */
  enqueuedAt?: string; // ISO-8601, informatief
}
```

**Python (Track A — consument, `functions/import_processor.py`, fase 3):**

```python
from typing import TypedDict, NotRequired

class ImportQueueMessage(TypedDict):
    jobId: int
    companyId: int
    script: str
    blobRef: str
    enqueuedAt: NotRequired[str]
```

### Regels

- De queue-payload is **niet** de bron van waarheid voor import-config
  (beslissing #2 in het plan): de queue-functie leest plugin-config via
  `resolve_config`/`build_import_config` **uit de DB** (`app_config`), met
  `companyId` + `script` uit de payload als lookup-sleutel. Geen
  config-velden in de payload zelf.
- Per-job opties die vandaag als losse `ImportJob`-kolommen bestaan
  (`dryRun`, `accountingDate`, `originalEntryRef`) staan **niet** in de
  queue-payload — de functie leest ze via `jobId` uit `import_jobs`
  (Prisma/raw-SQL) op het moment van verwerking. Dit houdt de payload klein
  én voorkomt drift tussen payload en DB-rij bij herlevering.
- Bij ontvangst zet de functie eerst `import_jobs.status = 'running'`
  (job-status-guard: als de status al `completed`/`failed`/`running` is,
  **niet** opnieuw verwerken — voorkomt dubbele boekingen bij queue-redelivery).

---

## 2. Function-HTTP-DTO's

Alle onderstaande triggers: `auth_level=function` (zie §4), Azure Functions
native (geen Flask/WSGI — dat verdwijnt met de poort in fase 2), JSON
request/response, `Content-Type: application/json`.

**Foutvorm (uniform over alle triggers hieronder):**

```json
{ "error": "<mens-leesbare boodschap>" }
```

met statuscode 400 (validatiefout van de aanroeper), 404 (niet gevonden),
409 (conflict/al-verwerkt), of 500 (onverwachte serverfout). Dit spiegelt
1-op-1 de bestaande Flask-foutafhandeling in
`apps/main/app/{vat_return,sbmov,translation_check}/routes.py`.

**Autorisatie:** de functions doen **geen** eigen user-auth/role-check — de
function-key (§4) is de enige toegangscontrole op HTTP-niveau. Role- en
`company_id`-toegangscontrole voor de eindgebruiker gebeurt **in Next.js**
(Track B, `web/src/lib/authz.ts`, fase 4) **vóórdat** de server-side
function-call wordt gedaan. `company_id` staat daarom wél in elke
request-DTO (nodig voor Odoo-scoping/config-resolution), maar de functie
vertrouwt de aanroeper voor de toegangscontrole op dat `company_id`.

### 2.1 `GET /vat-return/data`

Poort van `apps/main/app/vat_return/routes.py::get_vat_return_data` +
`service.py::fetch_vat_return_data`.

- Query: `company_id` (int, verplicht), `period` (string `YYYY-MM`, verplicht).
- 200:
  ```json
  {
    "period": "2026-02",
    "config": {
      "correction_mappings": [{ "source_vat_grid": "string", "target_base_grid": "string" }],
      "remainder_grid": "string",
      "standard_vat_rate": 0.21,
      "correction_account": 450000,
      "vat_return_journal_id": 12
    },
    "data": { "<vat_code>": { "<grid>": 0.0 } },
    "tag_info": { "<grid>": { "...": "..." } }
  }
  ```
- 400: `company_id`/`period` ontbreekt of `period` niet `YYYY-MM`.
- 500: Odoo-/config-fout (`RuntimeError`/onverwacht).

### 2.2 `GET /vat-return/check`

Poort van `check_vat_return` / `service.py::check_existing_entry`.

- Query: `company_id` (int, verplicht), `period` (string, verplicht).
- 200 (bestaat): `{ "exists": true, "move_id": 123, "move_name": "MISC/2026/0042", "created_at": "2026-...", "created_by": "user@x.com" }`
- 200 (bestaat niet): `{ "exists": false }`

### 2.3 `POST /vat-return/dismiss`

Poort van `dismiss_vat_return` / `service.py::dismiss_entry`.

- Body: `{ "company_id": 1, "period": "2026-02", "dismissed_by": "user@x.com" }`
  (`dismissed_by` = e-mail van de ingelogde gebruiker, door Track B uit de NextAuth-sessie;
  optioneel maar vereist voor audittrail-pariteit — vult `vat_return_entries.dismissed_by`. Zie **§7 amendement A**.)
- 200: `{ "success": true }`
- 404: `{ "error": "No active correction entry found for this period" }`
- 400: `company_id`/`period` ontbreekt.

### 2.4 `POST /vat-return/book`

Poort van `book_vat_return` / `service.py::book_correction_entry`.

- Body:
  ```json
  {
    "company_id": 1,
    "period": "2026-02",
    "correction_lines": [
      { "description": "string", "grid": "string", "amount": 0.0, "tag_id": 123 }
    ],
    "start_data": { "<vat_code>": { "<grid>": 0.0 } }
  }
  ```
  (`start_data` optioneel — alleen nodig voor de Excel-bijlage. Body mag daarnaast
  `created_by` dragen — e-mail van de ingelogde gebruiker, door Track B uit de
  NextAuth-sessie; vult `vat_return_entries.created_by` voor audittrail-pariteit. Zie **§7 amendement A**.)
- 200: `{ "success": true, "move_id": 456, "odoo_move_name": "MISC/2026/0043", "warning"?: "string" }`
- 409 (idempotentie — entry bestaat al):
  `{ "error": "correction_exists", "move_id": 123, "move_name": "...", "created_at": "...", "created_by": "..." }`
  of `{ "error": "Correction entry already exists for 2026-02", "move_id": 123 }`
- 400: ontbrekende velden.

### 2.5 `GET /sbmov/suppliers`

Poort van `get_suppliers` / `service.py::list_suppliers`.

- Query: `company_id` (int, verplicht).
- 200:
  ```json
  {
    "moveto_journal": { "id": 5, "name": "string" },
    "search_journals": [{ "id": 3, "name": "string" }],
    "suppliers": [
      { "partner_id": 42, "partner_name": "string", "invoice_count": 3, "journal_ids": [3, 4] }
    ]
  }
  ```
  (`partner_id: null` = "(No partner)"-emmer.)

### 2.6 `POST /sbmov/move`

Poort van `post_move` / `service.py::move_partner_drafts`.

- Body: `{ "company_id": 1, "partner_id": 42 }` (`partner_id: null` toegestaan
  — de no-partner-emmer; sleutel moet aanwezig zijn, ook als `null`).
- 200: `{ "requested": 3, "moved": 3, "cancelled": 3, "errors": [{ "invoice_id": 99, "error": "string" }] }`
- 400: `company_id` ontbreekt, `partner_id`-sleutel ontbreekt, of `partner_id`
  niet int/null.

### 2.7 `GET /translation-check/check`

Poort van `check` / `service.py::check_translations`. **Admin-only**
(role-check gebeurt in Next.js vóór de call — zie Autorisatie hierboven).

- Query: `company_id` (int, verplicht), `plan_id` (int, optioneel).
- 200:
  ```json
  {
    "languages": [{ "code": "en_US", "name": "English" }],
    "plans": [{ "id": 1, "name": "string" }],
    "mismatches": [
      {
        "account_id": 10,
        "plan_id": 1,
        "plan_name": "string",
        "reference_name": "string",
        "translations": { "en_US": "string", "nl_BE": "string" },
        "deviating_langs": ["nl_BE"]
      }
    ],
    "total_checked": 120,
    "total_mismatched": 3
  }
  ```

### 2.8 `POST /translation-check/fix`

Poort van `fix` / `service.py::apply_fixes`. **Admin-only.**

- Body: `{ "company_id": 1, "fixes": [{ "account_id": 10, "correct_name": "string" }] }`
- 200:
  ```json
  {
    "results": [
      {
        "account_id": 10,
        "correct_name": "string",
        "fixed_langs": ["nl_BE"],
        "already_ok_langs": ["en_US"],
        "errors": []
      }
    ],
    "total_fixed": 1,
    "total_errors": 0
  }
  ```

### 2.9 `GET /ping` (Fase 1-scaffold — al opgeleverd)

Triviale health-trigger, `auth_level=function`, geen business-logica; bewijst
dat de host "leeg-maar-gezond" draait zonder het `odoo`-pakket te laden.

- 200: `{ "ok": true, "service": "travel-experts-import-functions" }`

> **Nog niet gecontracteerd in fase 1** (volgt in fase 2/3, dan éérst hier
> aanvullen): de 8 plugin-import-triggers zijn geen losse HTTP-DTO's — import
> loopt via de queue (§1) + `import_jobs` (§3), niet via een directe
> HTTP-call per plugin. `sync/csv-blob` (timer + HTTP, al pakket-gebaseerd in
> de bronrepo) wordt in fase 2 1-op-1 geport; vorm ongewijzigd t.o.v.
> `apps/syncs/functions.py::sync_single_csv_blob_http`.

---

## 3. Import-status (DB-gemedieerd, geen Function-HTTP)

**Belangrijk verschil met §2**: dit is **geen** function-HTTP-DTO. Volgens
het kernontwerp (plan §Belangrijke bron → doel-verschillen #4) is de
import-voortgang niet langer in-memory/SSE, maar **de DB is de bron van
waarheid**. Web leest `import_jobs` rechtstreeks via Prisma; de queue-functie
schrijft er rechtstreeks naartoe (geen HTTP-laag ertussen). Dit is puur een
Track-B-intern contract tussen de Next.js-route en de frontend — opgenomen
hier omdat Track A (de queue-functie) exact dezelfde velden/waarden moet
schrijven die Track B verwacht te lezen.

### 3.1 `POST /api/imports/{id}/run` (Next.js-route, fase 5)

- Web-eigen route (geen function-key nodig — is zelf een Next.js-endpoint).
- Effect: valideert dat `import_jobs.status === 'pending'`; zet
  `status = 'queued'`; pusht `ImportQueueMessage` (§1) op de queue; retourneert
  202 met de bijgewerkte job-rij (DTO hieronder).
- 409 als de job al `queued`/`running`/`completed`/`failed` is.

### 3.2 `GET /api/imports/{id}` (Next.js-route, polling, fase 5)

Vervangt de oude SSE-`stream_progress`; frontend polt elke 5s (bestaande
polling-fallback in de frontend blijft, `EventSource` wordt verwijderd).

**Response-DTO** (JSON-serialisatie van het Prisma `ImportJob`-model,
`prisma/schema.prisma` — camelCase, zie §0):

```ts
export interface ImportJobStatus {
  id: number;
  pluginName: string;
  companyId: number;
  /** 'pending' | 'queued' | 'running' | 'completed' | 'failed' */
  status: 'pending' | 'queued' | 'running' | 'completed' | 'failed';
  fileName: string;
  blobRef: string;
  dryRun: boolean;
  accountingDate: string | null; // ISO date
  originalEntryRef: string | null;
  /** JSON-string (zoals vandaag) OF al geparsed object — Track B kiest dit
   *  bij het porten van ImportJob.to_dict(); vastleggen in fase 5, niet hier
   *  heropenen zonder dit document bij te werken. */
  resultSummary: string | null;
  skipReportPath: string | null;
  createdBy: number;
  creatorName: string | null; // afgeleid (join op users), zoals to_dict() vandaag
  createdAt: string; // ISO datetime
  startedAt: string | null;
  completedAt: string | null;
  updatedAt: string;
  /** Live voortgang — geschreven door de queue-functie (fase 3), NIEUW t.o.v.
   *  de oude in-memory ProgressInfo. Fase-waarden (vrije string, niet
   *  DB-afgedwongen): starting | validating | parsing | connecting |
   *  building | executing | done | failed. */
  progressPhase: string | null;
  progressCurrent: number | null;
  progressTotal: number | null;
  progressMessage: string | null;
}
```

- 404 als de job niet bestaat; 403 als de sessie geen toegang heeft tot
  `companyId` (authz in Next.js, niet in de functie).
- `skipReportPath` is een **blob-naam** in dezelfde container als `blobRef`
  (`skip-reports/{jobId}_skip_report.xlsx`), **geen** lokaal pad — Track B's download-route
  (fase 5) haalt het uit Blob Storage. Zie **§7 amendement B**.

---

## 4. Function-key-conventie

- **Auth-level**: elke HTTP-trigger in `functions/` gebruikt
  `auth_level=func.AuthLevel.FUNCTION` (Azure Functions ingebouwde
  function-key-verificatie) — **nooit** `ANONYMOUS`. Dit vervangt de oude
  Flask-`@require_auth`-JWT-check op dit niveau volledig; er zit geen
  aparte custom auth-laag in de functions zelf (zie Autorisatie-alinea
  onder §2).
- **Header (voorkeur)**: `x-functions-key: <FUNCTIONS_KEY>` — Next.js
  server-side code stuurt de key **altijd als header**, nooit als
  `?code=`-querystring (voorkomt lekken via server-/proxy-logs en browser-
  historie, ook al roept de browser deze URL's toch nooit direct aan).
- **Env-namen**:
  - `web` (server-only, **nooit** `NEXT_PUBLIC_*`): `FUNCTIONS_BASE_URL`
    (bv. `https://<function-app>.azurewebsites.net`), `FUNCTIONS_KEY`.
  - `functions`: geen eigen env nodig voor het aanvaarden van de key — Azure
    Functions verifieert dit op platformniveau vóór de trigger-code draait.
- **Scope van de key (v1 — scaffold-beslissing, fase 9 kan verscherpen)**:
  één gedeelde host-function-key per deployment/Function-app (dekt alle
  `auth_level=function`-triggers in die app). Per-function-keys (Azure
  ondersteunt dit) zijn een beschikbare hardening-optie, niet vereist voor de
  eerste oplevering — als dit later verandert: hier documenteren.
- **Harde regel (plan, projectregel #3)**: de function-key mag **nooit** in
  de browser-bundle terechtkomen. Concreet:
  - Nooit `NEXT_PUBLIC_FUNCTIONS_KEY` of vergelijkbaar.
  - Function-calls uitsluitend vanuit Server Components, Route Handlers
    (`app/api/**/route.ts`) of Server Actions — nooit vanuit `"use client"`-
    code, nooit doorgegeven aan de client als prop/JSON.
  - Fase 9-guardrail: grep-lint op de gebouwde bundle dat de key-waarde (of
    de env-naam `FUNCTIONS_KEY`) niet voorkomt in `web/.next/static/**`.
- **Timeout**: `functions/host.json` zet `functionTimeout: "03:00:00"` (3 uur,
  geport 1-op-1 van de bronrepo) — relevant voor eventuele toekomstige lange
  synchrone HTTP-calls, al lost de queue (§1) het 3-uur-import-timeout-risico
  al structureel op (zie plan, risico's-tabel).

---

## 5. Schema-resolutie (spike-uitkomst)

**Gate-uitkomst (2026-07-24):** de in het plan gedocumenteerde *preferred*- en
*fallback*-routes zijn **beide verworpen**. De spike bewees met query-logging
dat Prisma 6.19.3 met de SQL Server-connector **`DEFAULT_SCHEMA` volledig
negeert**:

| `DATABASE_URL` | Login | Gegenereerde SQL | Resultaat |
| -------------- | ----- | ---------------- | --------- |
| zónder `schema`-parameter | `sbt_app` met `DEFAULT_SCHEMA=sbt` | `FROM [dbo].[users]` | ❌ error 208 (invalid object name) |
| mét `;schema=sbt;` | idem | `FROM [sbt].[users]` | ✅ slaagt |

De *preferred*-route (login-`DEFAULT_SCHEMA` + unqualified namen) **bestaat
niet** voor deze connector; `multiSchema` wordt **niet** gebruikt en **niet**
als fallback bewaard. De oude beslisregel (`spike ok: preferred` /
`spike ok: fallback`) **vervalt**.

**Gekozen route:** één schema per deployment; de tenant leeft **uitsluitend** in
de connection string (`;schema=<tenant>;`) uit een Azure App Setting. Eén
codebase, N productiesites, **één `PrismaClient` per proces**, géén
tenant-switching at runtime. De Prisma-modellen blijven **unqualified** (geen
`@@schema`) — de schema-kwalificatie komt volledig uit de connection-string-
parameter en wordt door Prisma uniform op elke tabel toegepast.

Zie §6 voor hoe elke track de tenant-schema-selectie concreet implementeert.

---

## 6. Schema-resolutie per track

De connection-string-route (§5) werkt alléén voor Prisma (Track B). Track A
(Python/raw SQL) heeft geen Prisma-connectiestring en moet het schema zelf
kwalificeren. **Beide tracks halen de tenant uit env — nooit uit een default**
(zie harde projectregels #7 en #8 in het plan).

### Track B — `web` (Next.js / Prisma)

- Het tenant-schema staat in **`DATABASE_URL`** als `;schema=<tenant>;` (Azure
  App Setting per deployment). Eén `PrismaClient` per proces; Prisma prefixt
  elke tabel automatisch met `<tenant>`.
- **Geen** `@@schema` in `prisma/schema.prisma`; **geen** schemanaam in de
  query-code. Niets in de app kent de tenant behalve de connection string.

### Track A — `functions` (Python / raw SQL, pyodbc)

- Prisma's `schema`-parameter is hier **niet** van toepassing. Track A leest
  **`DB_SCHEMA`** uit env en **kwalificeert elke raw-SQL-query expliciet**:
  `[{schema}].[import_jobs]`, `[{schema}].[app_config]`, `[{schema}].[csv_blob_sync_log]`,
  enz. Geen `DEFAULT_SCHEMA`-afhankelijkheid.
- `DB_SCHEMA` wordt als **identifier geïnterpoleerd** (niet als query-parameter —
  een schemanaam kán niet geparametriseerd worden). Daarom **startup-validatie**
  tegen `^[a-z][a-z0-9_]*$` die het proces laat **crashen vóór de eerste query**
  (harde projectregel #7). Interpolatie is uitsluitend veilig ná die validatie.

### Gedeeld

- De tenant-identiteit is voor beide tracks **één env-waarde per deployment**
  (`;schema=` in `DATABASE_URL` voor web; `DB_SCHEMA` voor functions) die naar
  **hetzelfde** SQL-schema wijst. Nooit een default, nooit afgeleid uit
  hostname/branch/mapnaam — zie harde projectregels #7 en #8.

---

## 7. Amendementen na fase 1 (orchestrator)

> Deze sectie legt afstemmingen vast die ná fase 1 nodig bleken toen Track A
> (fase 2-3) opleverde en Track B ze gaat consumeren. Conform de harde
> projectregel "contracts.md wijzigt alleen in fase 1 — latere wijziging =
> expliciet afstemmen vóór de andere track ze nodig heeft" voert de orchestrator
> deze afstemming uit **vóór Track B (fase 4-5) start**. §1-§6 hierboven blijven de
> bevroren basis; onderstaande amendementen zijn er expliciet (met datum + reden)
> aan toegevoegd, niet stilzwijgend ingeslepen.

### Amendement A — audittrail-velden op de VAT-return-DTO's (§2.3/§2.4)

_2026-07-24, na fase 3._ De oude Flask-code vulde `vat_return_entries.created_by`
en `dismissed_by` uit `g.current_user` (de sessie). Nu doet **Track B** (NextAuth)
de user-auth, dus **Track B levert de waarde mee in de request-body**:

- `POST /vat-return/book` → optioneel `created_by` (string, e-mail; vult
  `vat_return_entries.created_by`, NVARCHAR(100)).
- `POST /vat-return/dismiss` → optioneel `dismissed_by` (string, e-mail; vult
  `vat_return_entries.dismissed_by`).

Niet-breaking (de functie behandelt ze als optioneel; ontbreekt de waarde → NULL).
Voor **audittrail-pariteit** met het oude systeem MOET Track B ze echter sturen.
Type/lengte matchen `prisma/schema.prisma::VatReturnEntry` — dat veld anticipeerde
dit al (`createdBy String? // user-email uit de sessie`).

### Amendement B — `skipReportPath` is een blob-naam, geen lokaal pad (§3.2)

_2026-07-24, na fase 3._ De bron schreef skip-reports naar lokale schijf
(`UPLOAD_DIR`); een stateless Function kan dat niet betrouwbaar aanbieden voor
latere download. De queue-functie schrijft skip-reports daarom naar **dezelfde
Blob-container als `blobRef`**, als `skip-reports/{jobId}_skip_report.xlsx`, en zet
die **blob-naam** in `import_jobs.skip_report_path`. De kolomvorm (§3.2
`skipReportPath: string | null`) verandert niet; alleen de interpretatie ligt nu
vast: **Track B's download-route (fase 5) haalt het bestand uit Blob Storage**,
niet van schijf.

### Amendement C — queue-connectie: trigger-kant vs. enqueue-kant (§1)

_2026-07-24, na fase 3._ De functions-**queue-trigger** bindt via
`connection="AzureWebJobsStorage"` (een Azure-Functions **app-setting-naam**, door
het platform geresolved vóór de trigger-code draait — bewust NIET via `env.py`, want
het is geen applicatie-env). **Web** enqueue't daarentegen via de Storage-Queue-SDK
met `AZURE_QUEUE_ACCOUNT_URL` + `AZURE_QUEUE_ACCOUNT_CREDENTIAL` (§1).
**Deployment-/gate-eis** (App Settings, mens): `AzureWebJobsStorage` (functions-app)
en `AZURE_QUEUE_ACCOUNT_URL` (web) MOETEN naar **hetzelfde** storage-account wijzen,
en beide apps gebruiken dezelfde queue-naam `AZURE_QUEUE_IMPORT_JOBS_NAME` (default
`import-jobs`). Op te nemen in `docs/onboard-client.md` (fase 9) + de fase-7/8-gates.
