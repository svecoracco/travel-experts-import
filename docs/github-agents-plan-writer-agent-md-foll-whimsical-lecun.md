# Plan-mode deliverable — plan-writer output

**What this is.** Acting as the `plan-writer` agent, I recast the already-thought-through
narrative (`docs/can-you-review-the-stateful-phoenix.md`) into the mandated format of
`docs/implementation-plan-template.md`, grounded in a fresh reconnaissance of the real code.

**On approval (ExitPlanMode) I will:** write the document below **verbatim** to
`c:\github\travel-experts-import\docs\can-you-review-the-stateful-phoenix.md` (overwrite in place,
per the user's choice). No code is written — the plan-writer's only deliverable is this document.

**Locked context feeding the plan:**
- **Monorepo root** = `c:\github\travel-experts-import` (git-init here; add `web/`, `functions/`,
  `prisma/`; `docs/` already exists and holds this plan + the template).
- **Sources (read-only backup)** = `C:\github\travel-experts\travel-experts-backend` (Azure
  Functions host with Flask-via-WSGI in `apps/main`) + `travel-experts-frontend` (Next.js 16 +
  NextAuth v4 + Azure AD). Shared Odoo package = `C:\github\odoo python` (local tag `2.0.7`).
- **Strategy** = greenfield monorepo, **port & reuse** (not rewrite-from-zero).
- **Recon corrections baked in:** the `odoo` package already speaks JSON-2 (only the `@1.1.0`
  pin is stale); the frontend already has NextAuth+Azure AD (only the Flask token-exchange must
  go); all `apps/main` Odoo access is still hand-rolled XML-RPC (full rewrite surface); no separate
  `config`/`users` pages (both in `settings/page.tsx`); `sbmov` UI is at `/selfbilling-move`; OTP is
  an orphaned unused component; committed live secrets sit in `local.settings.json` + a PAT in
  `requirements.txt`.

The `simplify` / `code-review` skills are for the executing agents, not this plan.

---
---

# Implementatieplan: Re-platform naar de standaardarchitectuur + client 2 (greenfield monorepo, port & reuse)

> **Doel van dit document**: gedetailleerd, opvolgbaar implementatieplan om de BTS/Travel-Experts
> app te herbouwen op de portfolio-standaardarchitectuur in één monorepo en vervolgens een tweede
> client puur via configuratie te lanceren, met vijf kernuitgangspunten: (1) **port & reuse** van de
> bestaande domeinlogica (geen rewrite-from-zero), (2) **alle data + auth API in Next.js/Prisma**,
> (3) **sync/Odoo/import-werk in een Python Azure Function** (`auth_level=function`, HTTP + queue +
> timer), (4) **één gedeeld Odoo-pakket op JSON-2** i.p.v. handgeschreven XML-RPC, (5) **één schema
> per client** in één gedeelde Azure SQL DB, met alle client-verschil in App Settings + `app_config`.
> Elke fase heeft een statusveld en een gedetailleerde takenlijst. **Werk de statusvelden en
> checkboxes bij tijdens de implementatie.**

|                                 |                                                                                                                                                                                                                                                                                                    |
| ------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **Probleem**                    | Twee repos: een Azure Functions-host met **Flask embedded via WSGI** (`travel-experts-backend/apps/main` — alle auth + API + de 8 import-plugins + VAT/sbmov/translation) plus native syncs (`apps/syncs`), en een dunne Next.js-frontend. Odoo-toegang in `apps/main` is **handgeschreven XML-RPC** (`app/shared/odoo_client.py`). De import-runner is **threaded + in-memory SSE** (`app/jobs/runner.py`) en past niet in een stateless Function. Gecommitte secrets (`local.settings.json`) + PAT (`requirements.txt`). Serveert alleen BTS en wijkt af van de portfolio-standaard — het echte risico is verder wegdriften. |
| **Doelmodel**                   | Eén monorepo: `web/` (Next.js 16 + Prisma + NextAuth = **alle** data- en auth-API), `functions/` (Python Azure Functions: HTTP + queue + timer triggers, `auth_level=function`), `prisma/` (schema + getemplatiseerd DDL), `docs/`. Per client een eigen Next.js-app + Function-app + **eigen schema** in één gedeelde Azure SQL DB; alle client-verschil in App Settings + `app_config`. Odoo uitsluitend via het gedeelde `odoo`-pakket (JSON-2). |
| **Reeds uitgevoerd (voorwerk)** | Zie sectie **Voorwerk**: het `odoo`-pakket spreekt al JSON-2 (lokaal tag `2.0.7`); de frontend draait al Next.js 16 + NextAuth v4 + Azure AD; de backend is al een Azure Functions-host en de syncs gebruiken het pakket al; de plugin-parsers/transforms zijn runtime-agnostisch; `resolve_config`-precedentie bestaat. |
| **Bron / referentie**           | `C:\github\travel-experts\travel-experts-backend` + `travel-experts-frontend` (read-only backup). Gedeeld pakket: `C:\github\odoo python` (`from odoo import OdooClient`). Deze plan-tekst is gedistilleerd uit de narratieve go-forward-nota (voorheen in dit bestand). |
| **Basis-plannen & conventies**  | Portfolio-standaard: Next.js + Prisma + auth / Python Function + function-key / Odoo via het gedeelde pakket op JSON-2. Design-tokens ("Solvidas") leven in `web/src/app/globals.css` (Tailwind v4 `@theme`); er is géén `frontend-style-guide.md` in de frontend-repo. |
| **Scope**                       | **WEL**: monorepo-scaffold; port van de 8 plugins + VAT/sbmov/translation; Odoo-consolidatie op JSON-2; NextAuth als enige auth; queue-gebaseerd import-herontwerp; multi-client via schema + App Settings; BTS-cutover; client-2-launch; docs-curatie. **NIET**: functionele wijziging van plugin-businesslogica (payload-**pariteit** verplicht); datamigratie (BTS-data blijft in schema `bts`); rewrite-from-zero (port & reuse); OTP herimplementeren (vervalt); wijziging aan de Odoo-datamodellen zelf. |

---

## Statusoverzicht

| Fase | Omschrijving                                   | Track                        | Status          |
| ---- | ---------------------------------------------- | ---------------------------- | --------------- |
| 0    | Hardening (secrets/PAT, pin-bump, CI-smoke)    | — (eerst, exclusief)         | ✅ Klaar        |
| 1    | Contracten & fundament (scaffold, Prisma, schema-spike) | — (contract-first, één agent) | ✅ Klaar        |
| 2    | Odoo-consolidatie + plugin/feature-port         | A (functions)                | ⬜ Niet gestart |
| 3    | Queue-based import                             | A (functions)                | ⬜ Niet gestart |
| 4    | Auth in Next.js (NextAuth = enige auth)         | B (web)                      | ⬜ Niet gestart |
| 5    | Data/CRUD + read-routes + import-UI             | B (web)                      | ⬜ Niet gestart |
| 6    | Branding & config-gedreven UI                   | B (web)                      | ⬜ Niet gestart |
| 7    | Cutover client 1 (BTS)                          | — (na sync-punt, exclusief)  | ⬜ Niet gestart |
| 8    | Launch client 2 (config only)                   | — (exclusief)                | ⬜ Niet gestart |
| 9    | Validatie, opruimen, docs & guardrails          | — (laatste, één agent)       | ⬜ Niet gestart |

Statuswaarden: ⬜ Niet gestart · 🟨 Bezig · ✅ Klaar · ⛔ Geblokkeerd (met reden)
Checkbox-waarden: `[ ]` open · `[x]` klaar · `[~]` deels klaar / afwijking (zie noot)

> **Harde projectregels — onverkort van kracht:**
> 1. **Nooit zelf secrets roteren, DDL uitvoeren of deployen.** De agent levert scripts/instructies;
>    de mens voert ze uit op de **gate** en bevestigt met het gevraagde woord.
> 2. **Nooit tegen echte Odoo posten tijdens pariteitscontrole** — vergelijk uitsluitend de
>    gegenereerde `build_moves`-payloads (oud vs nieuw).
> 3. **De function-key mag nooit in de browser-bundle** — Next.js roept de functions uitsluitend
>    server-side aan.
> 4. **Geen dependencies buiten de aangewezen fase** (scaffold in fase 1; `odoo`-pin in fase 0/2).
> 5. **Nooit bestanden buiten de eigen track aanraken**; `prisma/schema.prisma` en `docs/contracts.md`
>    alleen in fase 1.
> 6. **Geen client-naam en geen `xmlrpc`-import mag in `functions/` of gedeelde code terugkeren**
>    (grep-lint, fase 9).
> 7. **Geen defaults op tenant-identificatie.** Verboden: `schema ?? 'dbo'`, hardgecodeerde schemanamen
>    (ook in tests, seeds, scripts), tenant afleiden uit hostname/`NODE_ENV`/mapnaam/branch, of stil doorgaan
>    bij een ontbrekende of schema-loze `DATABASE_URL`. Verplicht: startup-validatie die het proces laat
>    **crashen vóór de eerste query** (geen try/catch, geen warn-and-continue). Geldt evengoed voor de
>    Odoo-env-set. Rationale: deze tool schrijft naar boekhoudkundige data — stil terugvallen op het verkeerde
>    schema schrijft data van site A naar de administratie van site B: onomkeerbaar en pas bij een aansluiting zichtbaar.
> 8. **Eén env-module per track.** `web/src/lib/env.ts` (Track B) en het Python-equivalent (Track A) parseren en
>    valideren env **bij import** en exporteren een **bevroren** object. Nergens anders een directe `process.env`-
>    of `os.environ`-referentie, ook niet in scripts, seeds of tests. Handhaven met een ESLint-regel
>    (`no-restricted-properties`) respectievelijk `ruff`.

---

## Uitvoering: sequentieel, twee tracks

> **Beslissing (2026-07-24): sequentieel, niet parallel.** Eerst **Track A** (fase 2→3, `functions/`),
> dáárna **Track B** (fase 4→5→6, `web/`). Reden: één reviewer, en het **pariteitsrisico in fase 2**
> (Odoo-transport-rewrite) hoort eerst opgelost te zijn. Track B integreert vervolgens **direct** tegen
> de opgeleverde fase-2/3-functions (geen mock/skeleton — zie regel 3). In het manifest: `fase 4
> afhangt_van [3]`.

De mappenstructuur van de nieuwe monorepo is **disjunct**: `functions/` (Python) versus `web/`
(Next.js/TypeScript). De gedeelde koppelvlakken (DB-tabelvormen, queue-payload, function-HTTP-DTO's,
function-key-conventie) zijn in fase 1 bevroren; beide tracks bouwen tegen dat contract. De tracks
draaien om de bovenstaande reden **na elkaar**, niet gelijktijdig.

### Afhankelijkheidsgraaf

```
Fase 0 (hardening)                                   ← eerst, exclusief, met menselijke gate
  │
Fase 1 (contracten & fundament: scaffold + Prisma + schema-spike)   ← contract-first, één agent
  │
TRACK A (functions-agent):  Fase 2 (Odoo-port + parity-harness) → Fase 3 (queue-import)
  │                          ← eerst VOLLEDIG af (pariteitsrisico vóór Track B)
TRACK B (web-agent):        Fase 4 (auth) → Fase 5 (routes+import-UI, integreert direct tegen
  │                          de live fase-2/3-functions) → Fase 6 (branding)
  │
sync-punt na [3,6]  ← eindverificatie, LICHT (drift al zichtbaar in fase 5)
  ──► Fase 7 (cutover BTS, exclusief, parity-gate)
  ──► Fase 8 (client 2, config only, runbook-gate)
  ──► Fase 9 (validatie, opruimen, docs, guardrails)
```

### Regels

1. **Contract-first (fase 1)**: vóór de tracks starten definieert één agent alle gedeelde
   contracten: `prisma/schema.prisma` (DB-tabelvormen, bron van waarheid), plus `docs/contracts.md`
   met de **queue-message-payload**, de **function-HTTP request/response-DTO's** en de
   **function-key-authconventie**. Track A spiegelt dit in Python, Track B in TypeScript; **alleen
   fase 1** wijzigt `prisma/schema.prisma` en `docs/contracts.md` (latere wijziging = expliciet
   afstemmen vóór de andere track ze nodig heeft).
2. **Bestandseigendom**:
   - Track A: `functions/**`
   - Track B: `web/**`
   - Niemand raakt tijdens de tracks: `prisma/**` en `docs/contracts.md` (alleen fase 1),
     `web/package.json` (alleen fase 1-scaffold), `functions/requirements.txt` (alleen fase 0-pin +
     fase 1-scaffold), en `local.settings.json` / `*.env` / App Settings (**alleen de mens**, via gates).
3. **Track B integreert direct tegen de opgeleverde functions** (vervangt de oude "web bouwt tegen
   mock/skeleton"-regel; beslissing 2026-07-24, sequentieel). Omdat Track A (fase 2→3) al volledig
   opgeleverd is voordat Track B start, roept `web` in fase 5 meteen de **echte** functions aan (met
   de function-key); **geen** mock/skeleton-responses bouwen. Contract-drift wordt zo al tijdens
   fase 5 zichtbaar i.p.v. pas op het sync-punt.
4. **Odoo-transport alleen via het pakket**: in `functions/` bestaat na fase 2 geen `xmlrpc`-import
   meer; alle model-toegang loopt via `from odoo import OdooClient` (JSON-2).
5. **Sync-punt na de tracks (lichter)**: na fase 3+6 een eindverificatie end-to-end (import
   enqueue→queue→progress→poll; function-key-pad) → daarna pas fase 7. Lichter dan oorspronkelijk,
   want door regel 3 is de meeste drift al in fase 5 opgevangen; dit is de finale controle, geen
   eerste integratie.
6. **Fase 7 (cutover) is exclusief**: draait de nieuwe stack parallel tegen schema `bts`, valideert
   payload-pariteit, flipt domains en decommissioneert Flask — nooit parallel met trackwerk.
7. **Fase 8 (client 2) is exclusief en config-only**: geen code-edits, puur runbook.
8. **Laatste fase (9) altijd als laatste**, door één agent, over het geheel.
9. **Git-hygiëne**: werk per track in aparte branches/worktrees en merge op het sync-punt. Werk
   statusvelden in dit document alleen bij voor de eigen track.

### Wat NIET parallel kan

> **N.B. (2026-07-24)**: de uitvoering is nu **volledig sequentieel** (Track A vóór Track B, zie boven),
> dus ook A ∥ B valt weg. Onderstaande tabel blijft als rationale waarom fasen sowieso niet
> samengevoegd kunnen worden.

| Combinatie                     | Reden                                                                                             |
| ------------------------------ | ------------------------------------------------------------------------------------------------ |
| Fase 1 ∥ wat dan ook           | Definieert `prisma/schema.prisma` + `docs/contracts.md` die beide tracks importeren.             |
| Fase 2 ∥ Fase 3                | Zelfde Track A, `functions/**`; de queue-import (3) hergebruikt de geporte plugin-code uit (2).   |
| Fase 4 ∥ Fase 5 ∥ Fase 6       | Zelfde Track B, `web/**`; auth-session (4) is de basis voor de routes (5) en branding (6).        |
| Fase 0 ∥ wat dan ook           | Roteert PAT/secrets en verzet de `odoo`-pin waarvan alle Odoo-toegang + deployments afhangen; menselijke gate. |
| Fase 2 intern splitsen (Odoo)  | De payload-parity-harness vergelijkt oud (backend) én nieuw (functions) `build_moves` — moet in één hand blijven. |
| Fase 7/8/9 ∥ wat dan ook       | Exclusieve integratie-/cutover-/opleverfases met menselijke gates.                               |

### Uitvoeringsmanifest (machine-leesbaar)

```yaml
plan:
  titel: 'Re-platform naar de standaardarchitectuur + client 2 (greenfield monorepo, port & reuse)'
  verificatie:
    - 'cd web && npm run typecheck'
    - 'cd web && npm run lint'
    - 'cd web && npm run build'
    - 'cd functions && ruff check . && python -m pytest -q'
    - '! grep -rEl "xmlrpc|execute_kw" functions/'   # geen XML-RPC-restant in de nieuwe functions
  verboden_voor_iedereen:
    - 'prisma/schema.prisma'          # behalve fase 1
    - 'docs/contracts.md'             # behalve fase 1
    - 'web/package.json'              # behalve fase 1 (scaffold)
    - 'functions/requirements.txt'    # behalve fase 0 (odoo-pin) en fase 1 (scaffold)
    - 'local.settings.json / *.env / App Settings'   # uitsluitend de mens, via gates
fasen:
  - id: 0
    naam: 'Hardening'
    afhangt_van: []
    track: exclusief
    gate: "Roteer de gecommitte GitHub-PAT van het odoo-pakket én de SQL-, Azure-, SendGrid- en Odoo-secrets uit local.settings.json; zet ze als Key Vault-references in App Settings. Bevestig met 'geroteerd'."
  - id: 1
    naam: 'Contracten & fundament (scaffold, Prisma, schema-spike)'
    afhangt_van: [0]
    track: sequentieel
    eigendom: ['prisma/**', 'docs/contracts.md', 'web/** (scaffold)', 'functions/** (scaffold)']
    gate: "Schema-selectie-spike. VOLDAAN 2026-07-24: DEFAULT_SCHEMA werkt niet op de Prisma/SQL Server-connector; gekozen route = tenant in de connection string (;schema=<tenant>; in DATABASE_URL voor web, DB_SCHEMA + expliciete raw-SQL-kwalificatie voor functions). preferred/fallback vervalt; zie docs/contracts.md §5-§6."
  - id: 2
    naam: 'Odoo-consolidatie + plugin/feature-port (functions)'
    afhangt_van: [1]
    track: A
    eigendom: ['functions/**']
  - id: 3
    naam: 'Queue-based import (functions)'
    afhangt_van: [2]
    track: A
    eigendom: ['functions/**']
  - id: 4
    naam: 'Auth in Next.js (NextAuth = enige auth)'
    afhangt_van: [3]   # sequentieel (beslissing 2026-07-24): Track B start pas na Track A (fase 3)
    track: B
    eigendom: ['web/**']
  - id: 5
    naam: 'Data/CRUD + read-routes + import-UI (Next.js/Prisma)'
    afhangt_van: [4]
    track: B
    eigendom: ['web/**']
  - id: 6
    naam: 'Branding & config-gedreven UI'
    afhangt_van: [5]
    track: B
    eigendom: ['web/**']
  - id: 7
    naam: 'Cutover client 1 (BTS)'
    afhangt_van: [3, 6]
    track: exclusief
    gate: "Bevestig payload-pariteit (oud vs nieuw, per plugin), flip de custom domains naar de nieuwe stack en keur decommission van de Flask-app goed. Bevestig met 'cutover ok'."
  - id: 8
    naam: 'Launch client 2 (config only)'
    afhangt_van: [7]
    track: exclusief
    gate: "Voer de onboarding-runbook uit (Entra-registraties, schema clientb + DDL, Odoo API-key, App Settings, seed app_config, eerste admin). Bevestig met 'client2 ready'."
  - id: 9
    naam: 'Validatie, opruimen, docs & guardrails'
    afhangt_van: [8]
    track: exclusief
sync_punten:
  - na: [3, 6]
    doel: 'Eindverificatie (lichter — Track B integreerde in fase 5 al direct tegen de live fase-2/3-functions): web ↔ echte functions met function-key; import enqueue→queue→progress→poll end-to-end; eventuele resterende queue-payload/DTO-drift fixen.'
```

---

## Voorwerk (afgerond vóór dit plan)

**Status: ✅ Grotendeels aanwezig** — de volgende zaken bestaan al en de uitvoerende agents mogen
erop bouwen (dit de-riskt en verkleint de scope t.o.v. een letterlijke "greenfield rebuild"):

- **`odoo`-pakket spreekt al JSON-2.** `C:\github\odoo python` (lokaal tag `2.0.7`); JSON-2 landde in
  `2.0.0` (`OdooClient(api="json2"|"auto")`, transport `POST {url}/json/2/{model}/{method}` met
  `Authorization: bearer` + `X-Odoo-Database`). **Er is geen pakket-upgrade nodig** — alleen de pin
  in `functions/requirements.txt` van `@1.1.0` → `>=2.0.7` (fase 0/2).
- **Frontend is al Next.js 16 + NextAuth v4 + Azure AD.** `travel-experts-frontend` gebruikt
  `AzureADProvider` (`src/lib/authoptions.ts`); alleen de **token-exchange naar de Flask-JWT** moet
  verdwijnen. OTP (`src/components/otp-form.tsx`) is een **ongebruikt, wees-component**.
- **Backend is al een Azure Functions-host.** `travel-experts-backend/function_app.py` registreert
  `apps/syncs` (native timer/http) + `apps/main` (Flask-via-WSGI). De syncs gebruiken het pakket al
  (`integrations/odoo.py`, API-key auth).
- **Plugin-parsers/transforms zijn runtime-agnostisch.** Excel/CSV-readers en payload-builders
  (`build_moves`, `payment_reference`-idempotentie) zijn zuivere Python — herbruikbaar; alleen het
  **Odoo-transport** (XML-RPC → pakket) en de **entrypoint** (Flask-route → trigger) wijzigen.
- **Config-precedentie bestaat.** `resolve_config(company_id, script_name, key)` (script → company →
  global → default) in `apps/main/app/models/app_config.py`, plus de plugin-variant
  `build_import_config()` in `apps/main/app/api/imports.py`.

- Ontwerpbesluiten (vastgelegd, **niet heropenen zonder gebruikersoverleg**):
  - **Greenfield monorepo, port & reuse**: nieuwe `web/`+`functions/` in
    `c:\github\travel-experts-import`; oude repos blijven read-only backup.
  - **Eén gedeelde Azure SQL DB, één schema per client** (via de connection-string/login).
  - **NextAuth-JWT-sessie is de enige auth**; `refresh_tokens`/`otp_codes` vervallen.
  - **Odoo uitsluitend via het pakket (JSON-2)**; het handgeschreven `odoo_client.py` vervalt.
  - **Expliciete niet-scope**: geen functionele plugin-wijziging (pariteit verplicht); geen
    BTS-datamigratie; OTP niet herbouwen.

---

## A. Doelmodel & architectuur

### Begrippen

| Term                    | Definitie                                                                                                                                            |
| ----------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------- |
| **Deployment / client** | Eén klant-instantie: eigen Entra-tenant, eigen Odoo, eigen SQL-**schema**. Verschil leeft **uitsluitend** in App Settings + `app_config`, niet in code. |
| **`company_id`**        | Bedrijf **binnen** één deployment (data-gedreven via `app_config`). Orthogonaal aan "client". Blijft ongewijzigd.                                    |
| **Schema per client**   | Eigen SQL-schema (`bts`, `clientb`) in één gedeelde DB. Route (spike 2026-07-24): de tenant staat in de connection string — `;schema=<tenant>;` in `DATABASE_URL` (web/Prisma) resp. `DB_SCHEMA` + expliciete raw-SQL-kwalificatie (functions). Eén codebase bedient N deployments; `DEFAULT_SCHEMA` werkt NIET op de Prisma/SQL Server-connector. |
| **Function-key**        | `auth_level=function` sleutel; **alleen** in de Next.js-server-env. De browser roept functions nooit direct aan.                                    |
| **Payload-pariteit**    | Identieke BTS-input → identieke Odoo-move-payloads (`build_moves`) uit oud vs nieuw, incl. `payment_reference`. De harde cutover-gate.               |
| **Contract (fase 1)**   | `prisma/schema.prisma` (DB-vormen) + `docs/contracts.md` (queue-payload, function-DTO's, function-key-conventie). Bron van waarheid tussen de tracks. |

### Auth (kernontwerp — token-exchange vervalt)

```
OUD:  browser → NextAuth (Azure AD) → jwt-callback → POST Flask /api/auth/token-exchange
      → interne Flask-JWT (backendAccessToken) → elke API-call met Bearer Flask-JWT; refresh-cookie.
NIEUW: browser → NextAuth (Azure AD) → jwt-callback → Prisma-upsert user (users/user_companies),
      role + company_ids op de sessie → NextAuth-sessie IS de auth; geen Flask-JWT, geen refresh/OTP.
      Server-side function-calls: function-key (auth_level=function).
```

### Imports (kernontwerp — herontwerp, de enige echte redesign)

```
OUD:  POST /imports/{id}/run → threading.Thread(run_import_async) → in-memory _progress store;
      SSE-endpoint /imports/{id}/progress streamt uit de store; browser leest via EventSource(?token=).
NIEUW: web POST /api/imports/{id}/run → schrijft import_jobs (status=queued) + queue-message → job-id;
      queue-getriggerde Python-functie verwerkt (geporte run_import_async) en schrijft
      progress/status naar import_jobs; web polt GET /api/imports/{id} (Prisma) i.p.v. SSE.
      (De frontend heeft al een 5s-polling-fallback — EventSource wordt verwijderd, polling blijft.)
```

### Odoo-transport (kernontwerp — XML-RPC → pakket JSON-2)

```
OUD:  from app.shared.odoo_client import OdooClient  # xmlrpc.client, password-auth, execute_kw/search_read/read/write
NIEUW: from odoo import OdooClient                    # pakket, API-key, model-API (client.<model>.search_read/create/write), JSON-2
```

### Belangrijke bron → doel-verschillen (voor elke uitvoerende agent)

| #   | Verschil                                                     | Consequentie voor de port/bouw                                                                                       |
| --- | ----------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------- |
| 1   | Odoo-toegang: `execute_kw`/`search_read` → pakket-model-API | Herschrijf elke call-site; `OdooClient(database=, api_key=, user=, url=)` i.p.v. `(url, db, username, password)`.     |
| 2   | Entrypoint: Flask-route → HTTP/queue/timer-trigger          | Businesslogica ongewijzigd; alleen de wrapper verandert. Geen Flask-`current_app.config` meer → lees env/DB direct.  |
| 3   | Auth: Flask-JWT via token-exchange → NextAuth-sessie         | Provisioning + role/company_ids naar de NextAuth `jwt`-callback (Prisma); `apps/main/app/auth/**` vervalt grotendeels. |
| 4   | Import-progress: in-memory SSE → `import_jobs` + polling      | Geen gedeelde procesgeheugen; status is de DB. Idempotente queue-verwerking.                                          |
| 5   | Env-namen: `ODOO_DB/USERNAME/PASSWORD` → `ODOO_DATABASE/API_KEY/USER/URL` | Unificeer op de pakket-set overal; verwijder het dual-naming; elke client een eigen Odoo-API-key.        |
| 6   | Data-CRUD: Flask SQLAlchemy → Prisma in Next.js              | `config`/`users`/`database`/read-halves worden Next.js-routes; **geen** aparte `config`/`users`-pagina (beide in `settings/page.tsx`). |
| 7   | Staging-tabellen hardcoded (`BABTS/AABTS/SWED/ITA`)          | Verplaats de lijst uit `api/database.py` naar `app_config` (per deployment configureerbaar).                        |

---

## B. Doelstructuur (nieuwe/gewijzigde bestanden)

```
travel-experts-import/                 # monorepo-root (git-init hier)
├── prisma/
│   ├── schema.prisma                  # surviving models (fase 1)
│   └── ddl/schema.template.sql        # getemplatiseerd DDL met {{schema}} (fase 1)
├── docs/
│   ├── can-you-review-the-stateful-phoenix.md   # dit plan
│   ├── contracts.md                   # queue-payload + function-DTO's + function-key-conventie (fase 1)
│   ├── architecture.md onboard-client.md accounting.md logbook.md style-guide.md   # curatie (fase 9)
│   ├── plugins/{airplus,bsp,tui,ibanfirst,vivawallet,rail,commission,divers}.md    # (fase 9)
│   └── features/translation-check.md  # (fase 9)
├── functions/                         # Track A — Python Azure Functions
│   ├── function_app.py                # host: registreert alle triggers (fase 1)
│   ├── host.json  requirements.txt  local.settings.template.json                   # (fase 1)
│   ├── odoo_conn.py                   # from odoo import OdooClient (JSON-2) (fase 2)
│   ├── shared/{move_utils,account_utils,invoice_lookup}.py   # herschreven op JSON-2 (fase 2)
│   ├── plugins/{airplus,bsp,tui,ibanfirst,vivawallet,rail,commission,divers}/       # port (fase 2)
│   ├── features/{vat_return,sbmov,translation_check}/         # HTTP triggers (fase 2)
│   ├── import_processor.py            # queue-trigger (geporte run_import_async) (fase 3)
│   ├── config_resolve.py             # geporte resolve_config-precedentie (fase 3)
│   ├── syncs/                         # timer-triggers (al pakket-gebaseerd) (fase 2)
│   └── tools/parity_harness.py       # diff oud vs nieuw build_moves (fase 2)
└── web/                               # Track B — Next.js 16 (port van travel-experts-frontend)
    └── src/
        ├── lib/{prisma.ts,authoptions.ts,authz.ts,functions-client.ts}   # (fase 4/5)
        ├── app/api/
        │   ├── auth/[...nextauth]/route.ts                    # (fase 4)
        │   ├── config/**  admin/users/**  database/**         # CRUD/reads (fase 5)
        │   ├── imports/** (enqueue + status-poll)  vat-return/**  sbmov/**  translation-check/**   # (fase 5)
        ├── app/(authenticated)/**    # geporte pagina's; import-UI polling; branding via env (fase 5/6)
        └── lib/branding.ts           # env-gedreven app-naam/logo/Odoo-URL (fase 6)
```

---

## C. Referentiebestanden (bron → doel-mapping)

Alle bronpaden relatief aan `C:\github\travel-experts\`.

| Bronbestand                                                             | Doelbestand                                            | Aard                                                                    |
| ----------------------------------------------------------------------- | ------------------------------------------------------ | ----------------------------------------------------------------------- |
| `travel-experts-backend/apps/main/app/plugins/<n>/{reader,transform,plugin}.py` | `functions/plugins/<n>/…`                       | reader/transform **1-op-1 port**; Odoo-calls herschrijven; entrypoint → trigger |
| `…/apps/main/app/shared/odoo_client.py`                                 | — (**vervalt**)                                        | verwijderen; vervangen door `from odoo import OdooClient`                |
| `…/apps/main/app/shared/{move_utils,account_utils,invoice_lookup}.py`   | `functions/shared/…`                                   | herschrijven op pakket-model-API (JSON-2)                               |
| `…/apps/main/app/{vat_return,sbmov,translation_check}/**`               | `functions/features/…` (HTTP triggers)                 | Odoo-calls herschrijven; Flask-route → HTTP-trigger                     |
| `…/apps/main/app/jobs/runner.py`                                        | `functions/import_processor.py` (queue-trigger)        | **herontwerp**: threading+SSE → queue + `import_jobs`-status            |
| `…/apps/main/app/api/imports.py` (`stream_progress`, run, upload)       | `web/src/app/api/imports/**` + `functions/import_processor.py` | SSE→polling; enqueue-route (web); verwerking (function)          |
| `…/apps/main/app/api/{config_api,users,database,health}.py`             | `web/src/app/api/**` (Prisma)                          | dunne CRUD/reads → Next.js-routes                                       |
| `…/apps/main/app/models/app_config.py` (`resolve_config`) + `api/imports.py` (`build_import_config`) | `functions/config_resolve.py` + Prisma-reads (web) | precedentie porten (functions) / spiegelen (web)          |
| `…/apps/main/app/auth/**`                                               | `web/src/lib/{authoptions.ts,authz.ts}`                | token-exchange verwijderen; provisioning + authz in NextAuth            |
| `…/apps/main/database/create_tables.sql` + `create_vat_return_entries.sql` | `prisma/schema.prisma` + `prisma/ddl/schema.template.sql` | modellen + getemplatiseerd DDL ({{schema}})                    |
| `…/integrations/odoo.py`, `data/db.py`, `jobs/blob_csv_sync.py`, `apps/syncs/functions.py` | `functions/syncs/…`                     | grotendeels **1-op-1** (al pakket-gebaseerd)                            |
| `travel-experts-frontend/src/**`                                        | `web/src/**`                                           | port **~as-is**; branding via env; auth/import-aanpassingen             |

---

## Fase 0 — Hardening

**Status: ✅ Klaar** · Eigenaar: één agent, vóór alles · Exclusief · Geschat: middel

Beschermt de huidige productie én de migratie. Raakt de **oude** backend-repo + secrets; levert
scripts/instructies, de mens voert de rotatie uit op de gate.

- [~] **Odoo-pakket van de gecommitte PAT af.** In déze repo: `functions/requirements.txt` opgeleverd
      zónder token, pin `@1.1.0` → `@2.0.7` (JSON-2). De bron-repo `travel-experts-backend/requirements.txt`
      (regel 50) is niet gemuteerd (verboden pad); de before/after-diff + niet-gecommitte installatiemethode
      staan als instructie in `docs/hardening/rotation-runbook.md §1` — **mens past toe op de gate**.
- [x] **Rotatie-instructie (gate).** `docs/hardening/rotation-runbook.md §0/§3` documenteert de 5
      gecompromitteerde secrets met exacte sleutelnamen (GitHub-PAT + `SQL_CONNECTION_STRING`/`SQLConnectionString`,
      `AZURE_CLIENT_SECRET`, `SENDGRID_API_KEY`, `ODOO_PASSWORD`). Geen waarden overgenomen.
- [x] **Key Vault-references** voorbereid: `rotation-runbook.md §2` (5 KV-secretnamen + de
      `@Microsoft.KeyVault(SecretUri=…)`-vorm per App Setting). Geen echte Key Vault aangemaakt (mens/gate).
- [~] **Dependencies pinnen + CI import-smoke-test.** `functions/requirements.txt` gepind (`odoo@2.0.7`,
      `azure-functions`); CI-workflow `.github/workflows/odoo-import-smoke.yml` + `functions/tests/odoo_smoke.py`
      opgeleverd (secret-lek-guard + JSON-2-read tegen test-Odoo). Backend-pin = instructie in runbook; live CI-run nog te draaien.
- [~] **Verificatie.** Lokale grep-guard schoon (geen `github_pat_`/plaintext-secret/token-body in de repo);
      pin-check = `2.0.7`, geen token. **CI groen + smoke-import op 2.0.7 nog te bevestigen door CI na push**
      (vereist `GH_TOKEN` + `ODOO_*`-secrets + Actions-runner — niet lokaal uitvoerbaar).

> **Gate (mens):** "Roteer de PAT en de vier secrets, zet ze als Key Vault-references in App Settings,
> en bevestig met **'geroteerd'**."

> **Fase 0-noot (uitvoering)**: Agentwerk klaar (2026-07-23). Opgeleverd in déze repo:
> `functions/requirements.txt` (odoo@**2.0.7**, tokenvrij), `functions/tests/odoo_smoke.py`,
> `.github/workflows/odoo-import-smoke.yml` (secret-lek-guard + JSON-2-smoke), `docs/hardening/rotation-runbook.md`.
> Bronrepo's alleen gelezen, niet gemuteerd. CI-installatiekeuze: git URL-rewrite met `GH_TOKEN`-secret.
> **Gebruikersbeslissing 2026-07-23**: pin op nieuwste tag **`2.0.7`** i.p.v. `2.0.3` (repo bevat tags t/m 2.0.7).
> **Gate voldaan 2026-07-23**: gebruiker bevestigde **'geroteerd'** (PAT + 4 secrets geroteerd, Key Vault-refs gezet).
> **Enige residu (niet-blokkerend, infra-afhankelijk)**: de live CI-smoke draait pas na de eerste push naar
> GitHub met de secrets (`GH_TOKEN`, `ODOO_*`); de versie-assert is verzacht naar `startswith('2.0')` zodat
> een genormaliseerde git-tag-versiestring de build niet onterecht laat falen.

---

## Fase 1 — Contracten & fundament (scaffold, Prisma, schema-spike)

**Status: ✅ Klaar** · Eigenaar: één agent, vóór de tracks · Geschat: groot

### 1.1 Monorepo-scaffold

- [x] Git-init `c:\github\travel-experts-import`; maak `web/`, `functions/`, `prisma/` (`docs/` bestaat).
- [x] `web/`: kopieer `travel-experts-frontend` **~as-is** (Next.js 16, NextAuth v4, Tailwind v4,
      `globals.css`-tokens). Voeg toe: `@prisma/client` + `prisma`. Nog géén functionele wijziging.
- [x] `functions/`: nieuwe Python Functions-host naar het model van `travel-experts-backend/apps/syncs`
      (`function_app.py`, `host.json`, `requirements.txt` met `odoo>=2.0.7`, `local.settings.template.json`).

### 1.2 `prisma/schema.prisma` — surviving models

- [x] Modellen voor: `users`, `user_companies`, `app_config`, `import_jobs`, `audit_log`,
      `vat_return_entries`, `csv_blob_sync_log`. **Retire**: `refresh_tokens`, `otp_codes`.
      Spiegel de DDL uit `apps/main/database/create_tables.sql` + `create_vat_return_entries.sql`.
- [x] Behoud sleutelconstraints: `app_config` **uniek** `(company_id, script_name, key)`;
      `import_jobs` status/progress-velden; `user_companies` FK naar `users` + `company_id`.
- [x] **Unqualified** modelnamen (schema-selectie via connection-string, zie beslissing #1); valideer dat
      Prisma voor SQL Server unqualified namen emit.

### 1.3 `prisma/ddl/schema.template.sql` — getemplatiseerd DDL

- [x] Eén bron met `{{schema}}`-placeholder waaruit elk client-schema (`bts`, `clientb`) gemaakt wordt.

### 1.4 `docs/contracts.md` — gedeeld koppelvlak

- [x] **Queue-message-payload** (enqueue → verwerking): minimaal `{ jobId, companyId, script, blobRef }`.
- [x] **Function-HTTP-DTO's**: request/response voor VAT/sbmov/translation-triggers + import-status.
- [x] **Function-key-conventie**: header/param, `auth_level=function`, server-side-only.

### 1.5 Schema-selectie-spike (de-riskt alles)

- [x] Schema-selectie-spike uitgevoerd (mens, 2026-07-24). **Uitkomst: geen van beide gedocumenteerde routes.**
      Prisma 6.19.3/SQL Server negeert `DEFAULT_SCHEMA` (schema-loze `DATABASE_URL` → `FROM [dbo].[users]`, error 208;
      `;schema=<tenant>;` → `FROM [<tenant>].[users]`, slaagt). **Gekozen: connection-string-route** `;schema=<tenant>;`;
      `multiSchema` niet gebruikt. Bewijs + route: `docs/contracts.md` §5-§6.

- [x] Verificatie: `cd web && npm run typecheck && npm run build`; `npx prisma validate` groen;
      `functions/` host start lokaal (`func start`) leeg-maar-gezond.

> **Gate (mens) — ✅ VOLDAAN 2026-07-24:** de schema-selectie-spike is uitgevoerd. Uitkomst: `DEFAULT_SCHEMA` werkt
> NIET op de Prisma 6.19.3/SQL Server-connector; **gekozen route = connection-string `;schema=<tenant>;` in `DATABASE_URL`**
> (web) resp. expliciete raw-SQL-kwalificatie via `DB_SCHEMA` (functions). De oude `spike ok: preferred`/`spike ok: fallback`-
> beslisregel vervalt. Bewijs + volledige route: `docs/contracts.md` §5-§6.

> **Fase 1-noot (uitvoering)**: Agentwerk klaar (2026-07-24). Scaffold + bevroren contracten opgeleverd; alle
> verificaties groen: `web` typecheck ✅, `web` build ✅ (Next 16.1.6/Turbopack, 12 routes), `npx prisma validate` ✅,
> `functions` `func start` → `GET /ping` 200 ✅ (host **odoo-vrij**, "leeg-maar-gezond"). Prisma gepind op **6.19.3**
> (Prisma 7 verwijdert `datasource.url` — niet upgraden zonder herontwerp; root-`package.json`+`node_modules`
> toegevoegd om de client naar `web/node_modules/.prisma/client` te dirigeren). Python-runtime = `py` (3.11.3);
> `python`/`python3` zijn Windows Store-aliassen.
> **Contract-kern** (voor de tracks): queue-payload `{jobId,companyId,script,blobRef}` **camelCase** (queue `import-jobs`);
> function-HTTP-DTO's **snake_case**, alle triggers `auth_level=function`; **import-status is DB-gemedieerd via Prisma
> `import_jobs`, géén HTTP-call**; function-key header **`x-functions-key`** (nooit `?code=`), env `FUNCTIONS_BASE_URL`/`FUNCTIONS_KEY`
> uitsluitend server-side (nooit `NEXT_PUBLIC_*`). `ImportJob`: statussen `pending|queued|running|completed|failed`,
> nieuwe `progress*`-velden, `file_path`→`blobRef`. Reconciliatie voor Track A (fase 2): `csv_blob_sync_log` mirrort de
> **DDL** (niet het licht-afwijkende SQLAlchemy-model); `vat_return_entries` filtered-unique-index leeft alleen in de DDL-template.
> **Beveiliging**: tijdens de run belandden twee misplaatste bron-bestanden in de repo-root (root `requirements.txt` mét
> de **oude PAT** + root `local.settings.json`, mtime maart, via een kopie) → verplaatst naar scratchpad-backup;
> root-`.gitignore` guard `/requirements.txt` toegevoegd; **0 commits → geen historie-lek**; volledige repo-scan schoon.
> **Schema-spike-uitkomst (mens, 2026-07-24)**: geen van de twee gedocumenteerde routes werkt — Prisma 6.19.3/SQL Server
> negeert `DEFAULT_SCHEMA` (query-logging: schema-loze `DATABASE_URL` → `FROM [dbo].[users]`/error 208; `;schema=sbt;` →
> `FROM [sbt].[users]`/slaagt). **Gekozen: connection-string-route** (`;schema=<tenant>;` uit een App Setting; `DB_SCHEMA` +
> expliciete raw-SQL-kwalificatie voor functions; startup-validatie verplicht). `multiSchema` geschrapt. Doorgewerkt in
> `schema.prisma`, `contracts.md` §5-§6, beslissing #1, de risico-rij en harde projectregels #7/#8.
> **Fase 1 afgerond ✅** — de tracks kunnen starten tegen het bevroren contract.

---

## Fase 2 — Odoo-consolidatie + plugin/feature-port (Track A)

**Status: ⬜ Niet gestart** · Vereist: fase 1 · Track A — vóór Track B (sequentieel) · Geschat: groot

> Stijlreferentie: `travel-experts-backend/apps/main/app/plugins/base.py` (`ImportPlugin` ABC:
> `validate_file/parse/build_moves/execute`; dataclasses `MovePayload/ParsedData/ExecutionResult`).

### 2.1 `functions/odoo_conn.py` + `shared/`

- [ ] `odoo_conn.py`: `from odoo import OdooClient`; `OdooClient(database, api_key, user, url, api="auto")`
      uit de `ODOO_DATABASE/API_KEY/USER/URL`-env (unificeer; verwijder `ODOO_DB/USERNAME/PASSWORD`).
- [ ] Herschrijf `shared/{move_utils,account_utils,invoice_lookup}.py`: elke `execute_kw`/`search_read`/
      `read`/`write` → pakket-model-API (`client.<model>.search_read/create/write`). Behoud
      `action_post`, `resolve_account_id`, `resolve_tax_id`, invoice-lookup-gedrag exact.

### 2.2 Plugins (`functions/plugins/<n>/`)

- [ ] Port alle 8 (`airplus, bsp, commission, divers, ibanfirst, rail, tui, vivawallet`):
      **reader/transform 1-op-1**; herschrijf uitsluitend de Odoo-call-sites (transport) en de
      entrypoint. Behoud de `payment_reference`-idempotentie exact (bv. airplus
      `{factuur_nr}-{i:04d}`; tui `TUI-{ref}-{group}`; rail = `OFFICIAL_DOC_NUMBER`).
- [ ] Behoud de idempotentie-check (batch `search_read` op `payment_reference` + `move_type`) via de
      pakket-API.

### 2.3 Features als HTTP-triggers (`functions/features/`)

- [ ] Port `vat_return` (`GET data/check`, `POST book/dismiss`; boekt correctie-`account.move` +
      `action_post`; schrijft `vat_return_entries`), `sbmov` (`GET suppliers`, `POST move`;
      `button_cancel` + `xmlrpc.client.Fault` → pakket-equivalent), `translation_check`
      (`GET check`, `POST fix`) als `auth_level=function` HTTP-triggers.
- [ ] Port de timer-syncs (`syncs/`, al pakket-gebaseerd) grotendeels 1-op-1.

### 2.4 Payload-parity-harness (`functions/tools/parity_harness.py`)

- [ ] Draai **oud** (`travel-experts-backend`) en **nieuw** (`functions`) `build_moves` op dezelfde
      BTS-input (bv. `C:\github\travel-experts\files\**`) en **diff de payloads** (incl.
      `payment_reference`) — **nooit** dubbel naar Odoo posten.

- [ ] Verificatie: `cd functions && ruff check . && pytest -q`; `! grep -rEl "xmlrpc|execute_kw" functions/`;
      live smoke read/write over JSON-2 tegen test-Odoo slaagt; parity-harness draait per plugin.

> **Fase 2-noot (uitvoering)**: <in te vullen door de uitvoerende agent>.

---

## Fase 3 — Queue-based import (Track A)

**Status: ⬜ Niet gestart** · Vereist: fase 2 (geporte plugins/shared) · Geschat: middel

### 3.1 `functions/import_processor.py` (queue-trigger)

- [ ] Port `run_import_async` (`apps/main/app/jobs/runner.py`): pipeline validate → parse → connect
      (`odoo_conn`) → `build_moves` → `execute` → skip-report → **schrijf progress/status naar
      `import_jobs`** (i.p.v. de in-memory store). Idempotent op herlevering (job-status-guard +
      `payment_reference` aan Odoo-zijde).

### 3.2 `functions/config_resolve.py`

- [ ] Port `resolve_config`-precedentie (script → company → global → default) + de plugin-merge
      `build_import_config`. **Bron van config = de DB** (beslissing #2), niet de queue-payload.

- [ ] Verificatie: een grote import enqueue't, de queue-functie schrijft voortgang naar `import_jobs`,
      status is uitleesbaar via de DB; `pytest -q` groen.

> **Fase 3-noot (uitvoering)**: <in te vullen door de uitvoerende agent>.

---

## Fase 4 — Auth in Next.js (Track B)

**Status: ⬜ Niet gestart** · Vereist: fase 3 (Track A opgeleverd) · Track B — na Track A (sequentieel) · Geschat: middel

> Stijlreferentie: `travel-experts-frontend/src/lib/authoptions.ts` (bestaande NextAuth+Azure AD).

- [ ] Verwijder `exchangeTokenWithBackend()` en `token.backendAccessToken` uit `web/src/lib/authoptions.ts`.
      In de `jwt`-callback: **Prisma-upsert** van de user (`users`/`user_companies`) op eerste sign-in
      (eerste user → `admin` als `BOOTSTRAP_FIRST_ADMIN`); attach `role` + `company_ids`.
- [ ] `session`-callback: expose `role` + `company_ids`; werk `src/types/next-auth.d.ts` bij.
- [ ] `web/src/lib/authz.ts`: helpers voor **role** + **company-access** op de sessie (vervangt
      `require_auth`/`require_company_access`); statuscodes 401/403.
- [ ] Verwijder `src/lib/api.ts`-token-exchange/refresh-pad en het wees-`otp-form.tsx`; vul de
      `middleware.ts`-matcher aan (`/vat-return`, `/selfbilling-move`, `/translation-check`).
- [ ] Verificatie: login via Azure AD; sessie draagt `role` + `company_ids`; `npm run typecheck && lint`.

> **Fase 4-noot (uitvoering)**: <in te vullen door de uitvoerende agent>.

---

## Fase 5 — Data/CRUD + read-routes + import-UI (Track B)

**Status: ⬜ Niet gestart** · Vereist: fase 4 · Geschat: groot

### 5.1 Next.js-API-routes (Prisma)

- [ ] `config` (`GET companies`, `GET/{companyId}`, `PUT/DELETE {companyId}/{script}`), `admin/users`
      (`GET`, `PATCH/{id}`), `database` (`GET status` row-counts + freshness, `DELETE dry-runs`) — port
      van `apps/main/app/api/{config_api,users,database}.py` naar Prisma-routes met `authz`-gates.
- [ ] Verplaats de **staging-tabellenlijst** (`BABTS/AABTS/SWED/ITA`, nu hardcoded in
      `api/database.py`) naar `app_config` (verschil #7).
- [ ] Read/list-helften van `imports` (`GET list/{id}/skip-report/plugins`), `vat-return` (`GET data/check`),
      `sbmov` (`GET suppliers`) als Prisma-/function-proxy-routes.
- [ ] **Server-side function-calls** met de function-key (uit web-server-env) voor de zware
      VAT/sbmov/translation/import-verwerking; nooit vanuit de browser.

### 5.2 Import enqueue + polling-UI

- [ ] `POST /api/imports/{id}/run`: schrijf `import_jobs` (status=queued) + drop queue-message → job-id.
- [ ] `imports/[id]/page.tsx`: **verwijder `EventSource`**, behoud/verscherp de bestaande 5s-polling op
      `GET /api/imports/{id}` (Prisma). `upload-dialog.tsx` blijft functioneel.

- [ ] Verificatie: routes leveren correcte data; enqueue→poll werkt tegen de functions uit fase 3
      (direct tegen de live fase-2/3-functions — geen mock, sequentieel); `npm run typecheck && lint && build`.

> **Fase 5-noot (uitvoering)**: <in te vullen door de uitvoerende agent>.

---

## Fase 6 — Branding & config-gedreven UI (Track B)

**Status: ⬜ Niet gestart** · Vereist: fase 5 · Geschat: klein

- [ ] Verwijder hardcoded merknamen en zet ze env-gedreven (`web/src/lib/branding.ts`):
  - `layout.tsx` (`title: "Travel Experts"`, `description: "… BTS Travel"`);
  - `sidebar.tsx` (`<h2>Travel Experts</h2>`); `login/page.tsx` (`<h1>Travel Experts</h1>`);
  - `database/page.tsx` (BTS/Travelmind-copy); `vat-return/page.tsx`
    (`ODOO_BASE_URL` default `https://travel-experts.odoo.com`).
- [ ] Introduceer `NEXT_PUBLIC_APP_NAME`, `NEXT_PUBLIC_ODOO_URL`, een **logo-slot**
      (`NEXT_PUBLIC_LOGO_PATH`), en `SENDGRID_FROM_NAME` (server) — zie beslissing #7.
- [ ] Verificatie: geen hardcoded "Travel Experts"/"BTS" meer in `web/src/**` (grep); `npm run build`.

> **Fase 6-noot (uitvoering)**: <in te vullen door de uitvoerende agent>.

---

## Sync-punt (na fase 3 + fase 6)

> Integratieronde door de orchestrator (geen subagent): web roept de **echte** functions aan met de
> function-key; import enqueue→queue→progress→poll end-to-end; queue-payload/DTO-drift fixen. Daarna
> pas fase 7.

---

## Fase 7 — Cutover client 1 (BTS)

**Status: ⬜ Niet gestart** · Vereist: sync-punt (Track A + B klaar) · Exclusief · Geschat: middel

- [ ] BTS-data **blijft staan** (zelfde DB, schema `bts`) — alleen de serveercode verandert.
- [ ] Draai de nieuwe stack **parallel** tegen `bts`; **payload-pariteit** (fase 2-harness) per plugin
      moet groen zijn — de harde gate.
- [ ] Flip de custom domains naar de nieuwe stack; **decommissioneer** de Flask-app.
- [ ] Verificatie: pariteit groen; nieuwe stack serveert `bts`; oude Flask uit.

> **Gate (mens):** "Bevestig payload-pariteit, flip de domains en keur decommission goed. Bevestig met
> **'cutover ok'**."

> **Fase 7-noot (uitvoering)**: <in te vullen door de uitvoerende agent>.

---

## Fase 8 — Launch client 2 (config only)

**Status: ⬜ Niet gestart** · Vereist: fase 7 · Exclusief · Geschat: klein (config)

- [ ] **Geen code-edits.** Volg de runbook (`docs/onboard-client.md`, opgesteld in fase 9-voorbereiding):
      Entra-registraties; schema `clientb` uit het getemplatiseerde DDL; `;schema=clientb;` in `DATABASE_URL` + `DB_SCHEMA=clientb`;
      Odoo-API-key; App Settings (Next.js + Function); seed `app_config` + eerste admin.
- [ ] Verificatie: client 2 logt in met eigen tenant; role + `company_ids` gaten kloppen; een
      tenant-A-token is nutteloos tegen deployment B; **nul** code-wijzigingen.

> **Gate (mens):** "Voer de onboarding-runbook uit en bevestig met **'client2 ready'**."

> **Fase 8-noot (uitvoering)**: <in te vullen door de uitvoerende agent>.

---

## Fase 9 — Validatie, opruimen, docs & guardrails

**Status: ⬜ Niet gestart** · Laatste fase, één agent · Geschat: middel

- [ ] **Volledige verificatiesuite**: `web` typecheck/lint/build; `functions` ruff/pytest;
      `npm audit` (geen high/critical); grep-lints: **geen `xmlrpc`** in `functions/`, **geen
      client-naam** in gedeelde code, **geen function-key** in de browser-bundle, **geen hardcoded merk**.
- [ ] **Docs-curatie** in `docs/`:
  - Behoud & migreer (XML-RPC→JSON-2 bijwerken): `airplus/bsp/tui/ibanfirst/vivawallet` → `docs/plugins/`;
    `accounting.md` → `docs/accounting.md`; `agents/prompt-translation-check.md` → `docs/features/translation-check.md`.
  - Herschrijf: backend-README (NL) → `docs/architecture.md`; `frontend-style-guide.md` →
    `docs/style-guide.md` (Solvidas-tokens houden, stale OTP/login schrappen); `agents/logbook.md` →
    `docs/logbook.md`; root-`MEMORY.md` → ververs voor de nieuwe repo.
  - Backfill stubs: `docs/plugins/{rail,commission,divers}.md`.
  - Schrijf `docs/onboard-client.md` (runbook, zie onder).
- [ ] **Handmatige smoke-checklist** (mens of agent met draaiende app):
  1. Login via Azure AD; role + `company_ids` correct; niet-admin ziet geen settings.
  2. Import: upload → enqueue → queue verwerkt → polling toont live status tot done; skip-report klopt.
  3. VAT-return / sbmov / translation-check draaien over JSON-2 tegen test-Odoo.
  4. Tenant-A-token faalt tegen deployment B; function-HTTP zonder key wordt geweigerd.
- [ ] Opruimen: dode comments/TODO's, tijdelijke conventies; oude repos read-only laten. (Geen mock-states op te ruimen — Track B integreerde sequentieel direct tegen de live functions.)
- [ ] Dit document: alle statusvelden definitief; afwijkingen in de slotsectie.

> **Fase 9-noot (uitvoering)**: <in te vullen door de uitvoerende agent>.

---

## Per-client onboarding-runbook (samenvatting, uitgewerkt in `docs/onboard-client.md`)

1. **Entra**: registreer de Next.js-app in de client-tenant; redirect `{site}/api/auth/callback/azure-ad`.
2. **Azure**: Next.js-Web-App + Python-Function-app + custom domains; GitHub-Environment met beide publish-profielen.
3. **Database**: maak het client-**schema**; run het getemplatiseerde DDL; zet de tenant in de connection string — `;schema=<tenant>;` in `DATABASE_URL` (web) en `DB_SCHEMA=<tenant>` (functions). Géén login-`DEFAULT_SCHEMA` (werkt niet op de Prisma/SQL Server-connector — zie `contracts.md` §5).
4. **Odoo**: maak de client-**API-key**.
5. **Next.js App Settings**: `DATABASE_URL`, `NEXTAUTH_SECRET/URL`, `AZURE_AD_*`, Function-URL + function-key, `NEXT_PUBLIC_ODOO_URL` + branding-vars.
6. **Function App Settings**: DB-connectie, de **enkele** Odoo-set (`ODOO_DATABASE/API_KEY/USER/URL`), SQL-lookup-connectie, SendGrid, storage/queue-vars.
7. **Seed**: `app_config`-companies + per-company plugin-config + staging-tabellenlijst; eerste admin.

---

## Openstaande beslissingen (met aanbevolen default)

| #   | Vraag                                                        | Aanbeveling (default bij geen tegenbericht)                                                                 |
| --- | ----------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------- |
| 1   | Prisma schema-selectie                                      | **BESLIST (fase 1-spike, 2026-07-24): connection-string-route.** Tenant via `;schema=<tenant>;` in `DATABASE_URL` (web/Prisma) + `DB_SCHEMA` met expliciete raw-SQL-kwalificatie (functions). `DEFAULT_SCHEMA` werkt NIET op deze connector; `multiSchema` niet gebruikt. Zie `contracts.md` §5-§6. |
| 2   | Import-config-bron voor de queue-functie                    | **DB + `resolve_config`-precedentie** (single source of truth; geen stale queue-payload). Queue draagt alleen `jobId/companyId/script/blobRef`. |
| 3   | `odoo`-dep zonder gecommitte PAT                            | **Build-time token uit CI-secret** (eenvoudigst; geen submodule/registry-onderhoud). Alternatief: deploy key of private index. |
| 4   | Queue-technologie                                           | **Azure Storage Queue** (al in de stack, goedkoop, volstaat voor het volume). Service Bus alleen bij ordering/dead-letter-eisen. |
| 5   | `csv_blob_sync_log`                                         | **Prisma-model (read-only vanuit web)**; de function schrijft via de eigen DB-connectie (raw SQL, zoals nu). |
| 6   | Client 2 in dit plan of aparte oplevering                   | **Opnemen als config-only fase 8** — bewijst de kernbelofte "client = config, geen code".                   |
| 7   | Branding-vars                                               | `NEXT_PUBLIC_APP_NAME`, `NEXT_PUBLIC_ODOO_URL`, `NEXT_PUBLIC_LOGO_PATH`, `SENDGRID_FROM_NAME`.               |

---

## Risico's & mitigaties

| Risico                                                | Mitigatie                                                                                          |
| ----------------------------------------------------- | ------------------------------------------------------------------------------------------------- |
| 3-uur-imports raken de HTTP-timeout                   | Queue-trigger + DB-polling (fase 3); geen synchrone request-verwerking.                            |
| Odoo-transport-rewrite introduceert payload-drift     | Payload-parity-harness (fase 2) + harde pariteits-gate vóór cutover (fase 7).                      |
| ⚠️ **OPGETREDEN (fase 1-spike)**: voorkeurs-schema-selectie werkt niet op SQL Server | **Mitigatie uitgevoerd**: `DEFAULT_SCHEMA` bleek genegeerd door Prisma/SQL Server → overgestapt op de connection-string-route (`;schema=<tenant>;` / `DB_SCHEMA`); `multiSchema` verworpen. Zie `contracts.md` §5-§6 + projectregels #7/#8. |
| Gecommitte secrets/PAT al gelekt                      | Fase 0: als gecompromitteerd behandelen en roteren; Key Vault-references; grep-lint.               |
| Tenant-A-token bruikbaar tegen deployment B           | Authz op de sessie + per-deployment Entra; expliciete verify (fase 8/9).                           |
| Function-key lekt in de browser-bundle                | Uitsluitend server-side function-calls; grep-check op de bundle (fase 9).                          |
| Dubbele queue-delivery → dubbele boekingen            | Idempotente verwerking (`payment_reference` aan Odoo-zijde) + job-status-guard (fase 3).           |
| Client-naam of `xmlrpc` sluipt terug in shared code   | Grep CI-lint naast de branding-lint (fase 9-guardrail).                                            |
| Contract-drift tussen web (TS) en functions (Python)  | `docs/contracts.md` bevroren in fase 1; drift-fix op het sync-punt.                                |

---

## Anti-drift-guardrails (doorlopend)

- Eén `main` in één monorepo is de enige bron van waarheid; **alle** client-verschil in App Settings +
  `app_config`, nooit in code of branches.
- Deze app volgt weer de portfolio-standaard (Next.js+Prisma+auth / Python-Function+function-key,
  Odoo via het pakket op JSON-2) — nieuwe features volgen dezelfde splitsing.
- Geen client-naam en geen `xmlrpc`-import mag terugkeren (grep CI-lint). Elke nieuwe per-client-setting
  wordt in dezelfde wijziging aan `docs/onboard-client.md` toegevoegd. De oude
  `travel-experts-backend`/`-frontend` blijven read-only backup tot BTS volledig en stabiel gecutovered is.

---

## Slotsectie: afwijkingenlog (in te vullen bij oplevering)

| Fase | Afwijking | Motivering | Impact |
| ---- | --------- | ---------- | ------ |
|      |           |            |        |
