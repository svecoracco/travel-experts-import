# Rotatie-runbook — Fase 0 (Hardening)

> **Dit is een gate-instructie voor de mens.** Deze track-worker roteert zelf **geen** secrets,
> voert **geen** DDL uit en deployt niet. Alle stappen hieronder worden **door de mens** uitgevoerd
> (of via een door de mens goedgekeurd CI/CD-pipeline-run). Bevestig pas na uitvoering met het
> woord **"geroteerd"** (zie afsluiting).

## 0. Samenvatting: wat is gecompromitteerd

De volgende secrets staan **gecommitteerd / on-disk in plaintext** in de bron-repo's
(`C:\github\travel-experts\travel-experts-backend`, read-only backup, **niet gewijzigd** door
deze track-worker) en moeten als **gecompromitteerd** behandeld worden:

| # | Secret | Waar (bron-repo, read-only) | Aard |
|---|--------|------------------------------|------|
| 1 | GitHub-PAT voor het `odoo`-pakket | `requirements.txt`, regel 50 (gecommitteerd in git-historie) | fine-grained GitHub PAT, ingebed in een `git+https://`-URL |
| 2 | SQL-wachtwoord | `local.settings.json`, sleutels `SQL_CONNECTION_STRING` **en** `SQLConnectionString` (zelfde connection string, twee keer aanwezig onder verschillende sleutelnamen; wachtwoord zit in het `Pwd=`-deel) | Azure SQL-login-wachtwoord (server `finplex.database.windows.net`, login `bts`) |
| 3 | Azure client-secret | `local.settings.json`, sleutel `AZURE_CLIENT_SECRET` (hoort bij `AZURE_CLIENT_ID` / `AZURE_TENANT_ID`, gebruikt voor storage-toegang) | Entra app-registratie client-secret |
| 4 | SendGrid-API-key | `local.settings.json`, sleutel `SENDGRID_API_KEY` | SendGrid API-key (`SG.…`-vorm) |
| 5 | Odoo-wachtwoord | `local.settings.json`, sleutel `ODOO_PASSWORD` (hoort bij `ODOO_URL` / `ODOO_DB` / `ODOO_USERNAME`) | Odoo-gebruikerswachtwoord (legacy XML-RPC password-auth) |

**Geverifieerd**: dit bestand is gelezen (read-only, alleen lezen is toegestaan) om de exacte
sleutelnamen vast te stellen. De waarden zelf worden **niet** in dit document of elders
overgenomen — alleen sleutelnamen en context.

**Niet als apart te roteren item behandeld, maar wel relevant**: `AZURE_CLIENT_ID`,
`AZURE_TENANT_ID`, `AZURE_AD_TENANT_ID`, `AZURE_AD_CLIENT_ID`, `AZURE_AD_ISSUER`, `ODOO_URL`,
`ODOO_DB`, `ODOO_USERNAME` zijn **identifiers**, geen secrets op zich — maar ze bepalen *welke*
Entra-app-registratie en *welke* Odoo-omgeving bij de bovenstaande secrets horen, dus neem ze mee
bij het opzoeken van de juiste plek om te roteren (Entra-portal resp. Odoo-instance-instellingen).

Voor de duidelijkheid: de PAT-waarde wordt **nergens** in dit document volledig overgenomen —
uitsluitend geredigeerd (`github_pat_11AN2XY3A0…<REDACTED>`), conform de harde projectregel.

---

## 1. `travel-experts-backend/requirements.txt` regel 50 — before/after

Dit bestand staat in de **bron-repo** (`C:\github\travel-experts\travel-experts-backend`), die
deze track-worker niet muteert. Onderstaande is de **instructie/diff** die de mens (of een
vervolgtaak met schrijfrechten op die repo) daar moet toepassen.

### Before (huidige, gecompromitteerde regel — PAT geredigeerd)

```diff
- odoo @ git+https://github_pat_11AN2XY3A0…<REDACTED>:x-oauth-basic@github.com/Acco-Group/odoo.git@1.1.0
```

Kenmerken van de huidige regel: (a) de volledige PAT staat **in plaintext in git-historie** —
zelfs na verwijderen uit de huidige HEAD blijft hij in de historie zichtbaar tot een
historie-herschrijving (`git filter-repo`/BFG) is uitgevoerd; (b) pin op `@1.1.0` (vóór JSON-2,
dat in `2.0.0` landde).

### After (toe te passen in de bron-repo, of — beter — bij het overzetten naar de nieuwe
### `functions/requirements.txt` in déze repo, wat al gebeurd is in fase 0)

```diff
+ odoo @ git+https://github.com/Acco-Group/odoo.git@2.0.7
```

Geen ingebedde credentials meer in de URL. Het installatietoken komt in CI uit een
GitHub Actions-secret (`GH_TOKEN`), via een git URL-rewrite die **vóór** `pip install -r
requirements.txt` wordt uitgevoerd:

```bash
git config --global url."https://${GH_TOKEN}@github.com/".insteadOf "https://github.com/"
pip install -r functions/requirements.txt
```

Dit is al zo doorgevoerd in **déze** repo (`functions/requirements.txt` +
`.github/workflows/odoo-import-smoke.yml`, opgeleverd in fase 0). Voor de bron-repo
(`travel-experts-backend`) — die tot de cutover (fase 7) nog in productie draait — geldt dezelfde
diff + dezelfde CI-aanpassing als **aanbevolen** actie, zodat de bron-repo niet langer een
levende PAT in git-historie/werkkopie heeft staan terwijl hij nog actief gebruikt wordt.

**Actie voor de mens op de gate:**
1. Roteer/intrek de bestaande PAT in de GitHub-organisatie-instellingen (Acco-Group) — de oude
   PAT moet **ingetrokken**, niet alleen vervangen, omdat hij al gecommitteerd (dus gelekt) is.
2. Genereer een nieuwe, **read-only, fine-grained** PAT of deploy-key met toegang tot uitsluitend
   `Acco-Group/odoo`, met een zo kort mogelijke levensduur/scope.
3. Zet die nieuwe waarde **uitsluitend** als GitHub Actions-secret `GH_TOKEN` op de repo(s) die
   hem nodig hebben (deze repo voor CI; optioneel de bron-repo als daar ook CI op ingericht wordt)
   — nooit in een bestand.
4. Pas de diff hierboven toe op `travel-experts-backend/requirements.txt` (aanbevolen, buiten de
   scope van deze track-worker) zodat ook de bron-repo geen PAT meer commit.
5. Overweeg een git-historie-herschrijving op de bron-repo (BFG/`git filter-repo`) om de oude PAT
   ook uit de historie te verwijderen — dit is een destructieve, door de mens te plannen actie
   (force-push, coördinatie met alle contributors), buiten de scope van deze track-worker.

---

## 2. Key Vault-referenties — namen + vorm

**Vault**: gebruik de bestaande/aan te wijzen Key Vault voor dit project (vul de naam in op de
plek van `<key-vault-naam>` hieronder — deze track-worker kent geen echte Key Vault-naam en maakt
er ook geen aan).

**Referentievorm in App Settings** (Azure Functions / Azure Web App), zoals gevraagd:

```
@Microsoft.KeyVault(SecretUri=https://<key-vault-naam>.vault.azure.net/secrets/<secret-naam>/)
```

(Alternatieve, functioneel gelijkwaardige vorm die Azure ook accepteert:
`@Microsoft.KeyVault(VaultName=<key-vault-naam>;SecretName=<secret-naam>)` — kies één vorm en
wees consistent; deze runbook gebruikt de `SecretUri`-vorm omdat die door de gate-tekst wordt
voorgeschreven.)

| # | Secret | Key Vault-secretnaam | App Setting-sleutel(s) die ernaar verwijzen | Referentie |
|---|--------|-----------------------|----------------------------------------------|------------|
| 1 | GitHub-PAT (odoo-pakket) | `odoo-pkg-github-pat` | *(primair: GitHub Actions-secret `GH_TOKEN`, niet een App Setting — zie §1. Optioneel: mirror in Key Vault als de organisatie centraal secret-beheer wil, bv. voor een toekomstige Azure-DevOps-pipeline met OIDC-federated identity die de PAT uit Key Vault haalt i.p.v. uit een Actions-secret.)* | `@Microsoft.KeyVault(SecretUri=https://<key-vault-naam>.vault.azure.net/secrets/odoo-pkg-github-pat/)` |
| 2 | SQL-wachtwoord (als volledige connection string) | `sql-connection-string` | `SQL_CONNECTION_STRING`, `SQLConnectionString` (beide sleutels, één KV-secret; de twee App-Setting-namen bestaan in de bron voor twee code-paden die dezelfde string lezen) | `@Microsoft.KeyVault(SecretUri=https://<key-vault-naam>.vault.azure.net/secrets/sql-connection-string/)` |
| 3 | Azure client-secret | `azure-client-secret` | `AZURE_CLIENT_SECRET` | `@Microsoft.KeyVault(SecretUri=https://<key-vault-naam>.vault.azure.net/secrets/azure-client-secret/)` |
| 4 | SendGrid-API-key | `sendgrid-api-key` | `SENDGRID_API_KEY` | `@Microsoft.KeyVault(SecretUri=https://<key-vault-naam>.vault.azure.net/secrets/sendgrid-api-key/)` |
| 5 | Odoo-wachtwoord/API-key | `odoo-api-key` (**let op naamswijziging**, zie hieronder) | `ODOO_API_KEY` (**nieuw**, vervangt `ODOO_PASSWORD` + `ODOO_USERNAME`-password-auth) | `@Microsoft.KeyVault(SecretUri=https://<key-vault-naam>.vault.azure.net/secrets/odoo-api-key/)` |

### Noot bij #5 — Odoo-secret verandert van *aard*, niet alleen van waarde

De nieuwe stack (fase 2+) gebruikt het `odoo`-pakket op JSON-2 met **API-key-auth**
(`OdooClient(database=, api_key=, user=, url=, api="auto")`, env-namen
`ODOO_DATABASE/API_KEY/USER/URL`), niet het oude wachtwoord-gebaseerde
`ODOO_DB/ODOO_USERNAME/ODOO_PASSWORD`. Rotatie hier is dus twee stappen:

1. **Intrek/wijzig** het bestaande Odoo-gebruikerswachtwoord (`ODOO_PASSWORD`) in Odoo zelf, zodat
   de gelekte waarde niet langer werkt — nodig zolang de **oude** Flask/XML-RPC-backend nog draait
   (tot cutover, fase 7).
2. **Genereer een nieuwe Odoo API-key** voor de nieuwe stack en zet die als Key Vault-secret
   `odoo-api-key`, gerefereerd via App Setting `ODOO_API_KEY` (plus de niet-secret env-waarden
   `ODOO_DATABASE`, `ODOO_USER`, `ODOO_URL` gewoon als reguliere App Settings, geen Key
   Vault-referentie nodig voor niet-geheime waarden).

### App Settings die **geen** Key Vault-referentie nodig hebben (niet-geheim, ter context)

`AZURE_CLIENT_ID`, `AZURE_TENANT_ID`, `AZURE_AD_TENANT_ID`, `AZURE_AD_CLIENT_ID`,
`AZURE_AD_ISSUER`, `AZURE_STORAGE_ACCOUNT_URL`, `BLOB_CONTAINER_NAME`, `ODOO_URL`, `ODOO_DATABASE`,
`ODOO_USER` — dit zijn identifiers/URL's, geen secrets; gewoon als platte App Settings-waarden
laten staan.

---

## 3. Stapsgewijze rotatie-instructies (uit te voeren door de mens op de gate)

1. **GitHub-PAT**: intrekken in GitHub (Acco-Group-org) → nieuwe read-only fine-grained PAT/deploy
   key genereren, scope beperkt tot `Acco-Group/odoo` → zetten als GitHub Actions-secret
   `GH_TOKEN` (niet in een bestand; zie §1).
2. **SQL-wachtwoord**: wachtwoord van de login `bts` op `finplex.database.windows.net` wijzigen in
   de Azure SQL/portal → nieuwe volledige connection string samenstellen → opslaan als Key
   Vault-secret `sql-connection-string` (§2, #2).
3. **Azure client-secret**: in Entra ID → App registrations → de app achter `AZURE_CLIENT_ID`
   (`7cbf1678-3c57-4928-b8fa-e4c579825ffc`) → Certificates & secrets → oude secret intrekken →
   nieuwe genereren → opslaan als Key Vault-secret `azure-client-secret` (§2, #3).
4. **SendGrid-key**: in het SendGrid-dashboard de bestaande API-key intrekken → nieuwe key
   genereren (minimale scope: mail send) → opslaan als Key Vault-secret `sendgrid-api-key`
   (§2, #4).
5. **Odoo-secret**: zie de tweestaps-procedure in §2-noot-bij-#5 hierboven (wachtwoord intrekken
   én nieuwe API-key genereren) → opslaan als Key Vault-secret `odoo-api-key` (§2, #5).
6. **App Settings omzetten naar Key Vault-referenties**: voor elke Function-app / Web-app die deze
   secrets vandaag als plaintext App Setting heeft staan, vervang de waarde door de bijpassende
   `@Microsoft.KeyVault(SecretUri=…)`-referentie uit de tabel in §2. Geef de Function-app/Web-app
   se managed identity **get**-rechten op de betreffende Key Vault-secrets (access policy of
   RBAC-rol `Key Vault Secrets User`).
7. **`local.settings.json` (bron-repo, lokaal-only)**: dit bestand hoort nooit gecommitteerd te
   zijn/blijven; laat de lokale ontwikkelaar 'm regenereren vanuit
   `local.settings.template.json` + de nieuwe secrets uit Key Vault/1Password/eigen kluis — niet
   opnieuw hardcoded plaintext.
8. **Verifieer**: draai de CI-workflow `.github/workflows/odoo-import-smoke.yml` (gebruikt
   `GH_TOKEN` + `ODOO_URL/DATABASE/API_KEY/USER` als GitHub-secrets tegen een **test**-Odoo) en
   bevestig dat hij groen is.
9. Bevestig de voltooiing van bovenstaande stappen in het plandocument-gesprek met het exacte
   woord: **"geroteerd"**.

---

## 4. Wat deze track-worker wél en niet gedaan heeft

- **Wel**: dit runbook geschreven; de nieuwe `functions/requirements.txt` (geen PAT, pin
  `@2.0.7`) en de CI-smoke-workflow (`.github/workflows/odoo-import-smoke.yml`) opgeleverd in
  déze repo; `local.settings.json` van de bron-repo **gelezen** (read-only) om de exacte
  sleutelnamen vast te stellen.
- **Niet** (per harde projectregels): geen enkele secret geroteerd; geen DDL uitgevoerd; niets
  gedeployed; geen bestand in de bron-repo's gewijzigd; geen Key Vault aangemaakt of gevuld; geen
  App Settings gewijzigd.

**Bevestig deze fase met: "geroteerd"** (nadat bovenstaande stappen 1–8 door de mens zijn
uitgevoerd).
