# <!--

# TEMPLATE: Implementatieplan (gefaseerd, multi-agent, met statusbeheer)

INSTRUCTIES VOOR DE INVULLENDE LLM — verwijder alle HTML-comments in de output.

Vul dit sjabloon volledig in op basis van de opdracht/context die je krijgt.
Kernprincipes van dit documentformaat:

1. HET PLAN IS EEN LEVEND DOCUMENT. Elke fase heeft een statusveld en elke taak
   een checkbox. Uitvoerende agents werken statussen en checkboxes bij tijdens
   de implementatie en documenteren afwijkingen inline.
2. FASES ZIJN ATOMISCHE, OPLEVERBARE EENHEDEN. Elke fase eindigt met een
   verificatiestap (typecheck/lint/build/tests — wat van toepassing is).
3. PARALLELLISATIE IS EXPLICIET. Fases zijn toegewezen aan tracks (A, B, …)
   die door aparte agents parallel uitgevoerd kunnen worden, met harde
   bestandseigendomsregels, sync-punten en een lijst van wat NIET parallel kan.
4. CONTRACT-FIRST. Als tracks gedeelde types/contracten nodig hebben, is fase 0
   altijd het definiëren daarvan door één agent, vóór de tracks starten.
5. BESLISSINGEN EN RISICO'S ZIJN TABELLEN, GEEN PROZA. Openstaande beslissingen
   krijgen altijd een aanbevolen default zodat een agent nooit blokkeert.
6. WEES CONCREET: exacte bestandspaden, functiesignaturen, foutcodes,
   statuscodes en verwijzingen naar bestaande patronen in de codebase.
   Vage taken ("implementeer de service") zijn niet acceptabel; elke checkbox
   moet uitvoerbaar zijn zonder aanvullende vragen.
7. HET UITVOERINGSMANIFEST IS VERPLICHT EN MACHINE-LEESBAAR. De sectie
   "Uitvoeringsmanifest" bevat een YAML-blok dat de fases, afhankelijkheden,
   tracks, gates, bestandseigendom en sync-punten formeel vastlegt. Een
   orchestrator-agent parseert dít blok om subagents te starten — het is de
   bron van waarheid voor uitvoeringsvolgorde. Genereer het manifest ALS
   LAATSTE, afgeleid uit het voltooide plan, en verifieer dat het 1-op-1
   consistent is met het Statusoverzicht, de Afhankelijkheidsgraaf en de
   eigendomsregels. Bij twijfel of conflict: het manifest volgt de proza-
   secties, nooit omgekeerd.
   ================================================================================
   -->

# Implementatieplan: <titel van het werk, bv. "Module X (port/nieuwbouw/refactor)">

> **Doel van dit document**: gedetailleerd, opvolgbaar implementatieplan voor
> <één-zin-samenvatting van het werk>, met <aantal> kernontwerpwijzigingen/
> uitgangspunten: (1) <…>, (2) <…>, (3) <…>. Elke fase heeft een statusveld en
> een gedetailleerde takenlijst zodat de uitvoerende agent exact weet wat er nog
> te doen is. **Werk de statusvelden en checkboxes bij tijdens de implementatie.**

|                                 |                                                                                             |
| ------------------------------- | ------------------------------------------------------------------------------------------- |
| **Probleem**                    | <huidige situatie en waarom die niet volstaat — verwijs naar concrete bestanden/modules>    |
| **Doelmodel**                   | <eindtoestand in 2–4 zinnen: datamodel, gedrag, eigenaarschap>                              |
| **Reeds uitgevoerd (voorwerk)** | <wat al bestaat en waarop gebouwd mag worden — verwijs naar sectie "Voorwerk">              |
| **Bron / referentie**           | <POC-repo, ontwerpdocument, ticket — met exact pad of link>                                 |
| **Basis-plannen & conventies**  | <bestaande plandocumenten/conventiedocumenten die onverkort gelden (auth, fouten, i18n, …)> |
| **Scope**                       | <wat WEL en expliciet wat NIET in scope is — benoem de niet-scope met reden>                |

---

## Statusoverzicht

<!-- Eén rij per fase. Kolom "Track" bepaalt welke agent de fase uitvoert:
     een tracknaam (A, B, …) voor parallelliseerbare fases, of "—" met een
     korte kwalificatie voor exclusieve fases (eerst / na sync-punt / laatste). -->

| Fase | Omschrijving                                | Track                       | Status          |
| ---- | ------------------------------------------- | --------------------------- | --------------- |
| 0    | Contracten: gedeelde types, schema's, DTO's | — (eerst, één agent)        | ⬜ Niet gestart |
| 1    | <backend-bouwsteen 1>                       | A (backend)                 | ⬜ Niet gestart |
| 2    | <backend-bouwsteen 2>                       | A (backend)                 | ⬜ Niet gestart |
| 3    | <frontend-bouwsteen 1>                      | B (frontend)                | ⬜ Niet gestart |
| 4    | <frontend-bouwsteen 2>                      | B (frontend)                | ⬜ Niet gestart |
| 5    | <integratie-/cross-cutting-fase>            | — (na sync-punt, één agent) | ⬜ Niet gestart |
| 6    | <exclusieve fase, bv. i18n>                 | — (exclusief, één agent)    | ⬜ Niet gestart |
| 7    | Validatie, opruimen & documentatie          | — (laatste, één agent)      | ⬜ Niet gestart |

Statuswaarden: ⬜ Niet gestart · 🟨 Bezig · ✅ Klaar · ⛔ Geblokkeerd (met reden)
Checkbox-waarden: `[ ]` open · `[x]` klaar · `[~]` deels klaar / afwijking (zie noot)

> **<Harde projectregel(s) — onverkort van kracht>**: <regels die de agent NOOIT
> mag overtreden, bv.: nooit zelf migraties/DDL uitvoeren (agent levert scripts
> aan, de gebruiker voert ze uit en meldt "done"); nooit dependencies toevoegen
> buiten de daarvoor aangewezen fase; nooit bestanden buiten de eigen track
> aanraken. Formuleer imperatief en ondubbelzinnig.>

---

## Parallellisatie: uitvoering met meerdere agents

<!-- Leg eerst uit WAAROM parallel kan (disjuncte mappen/modules), dan de graaf,
     dan de regels, dan expliciet wat niet parallel kan. Als het werk niet
     zinvol parallelliseerbaar is: zeg dat expliciet en laat de tracks weg,
     maar behoud de afhankelijkheidsgraaf én het Uitvoeringsmanifest (alle
     fases dan `track: sequentieel` of `exclusief` — de orchestrator werkt
     ook zuiver sequentiële plannen af). -->

De mappenstructuur is disjunct (<mappen van track A> versus <mappen van track B>),
waardoor twee tracks parallel kunnen lopen **mits onderstaande eigendomsregels en
sync-punten gerespecteerd worden**.

### Afhankelijkheidsgraaf

```
Fase 0 (contracten)                                  ← eerst, één agent
  │
  ├─► TRACK A (backend-agent):  Fase 1 → Fase 2
  └─► TRACK B (frontend-agent): Fase 3 → Fase 4     (tegen de contracten uit fase 0)
              beide klaar ──► sync-punt (integratieronde, UI tegen echte backend)
                              ──► Fase 5 (<cross-cutting>, één agent)
                              ──► Fase 6 (<exclusief, bv. i18n>, één agent)
                              ──► Fase 7 (validatie & opruimen)
```

### Regels

1. **Contract-first (fase 0)**: vóór de tracks starten definieert één agent alle
   gedeelde types/contracten in `<pad naar contractbestand>`. Beide tracks
   importeren dit bestand maar **alleen Track <X> mag het wijzigen**; wijzigingen
   worden expliciet gecommuniceerd vóór de andere track ze nodig heeft.
2. **Bestandseigendom**:
   - Track A: `<glob(s) van track A>`
   - Track B: `<glob(s) van track B>`
   - Niemand raakt tijdens de tracks: `<gevoelige bestanden: package.json,
lockfiles, schema's, locales, gedeelde config — met de fase waarin ze wél
aangeraakt worden>`.
3. **Track B werkt tegen het contract, niet tegen de live backend**: de UI kan
   gebouwd en getypecheckt worden zonder werkende backend (mock/skeleton-states).
   Integratie gebeurt op het sync-punt.
4. **<Tijdelijke conventie tijdens de tracks>**: <bv. strings hardcoded in <taal>
   tijdens de tracks — fase <n> verplaatst alles naar de locales>.
5. **Sync-punt na de tracks**: beide klaar → korte integratieronde (<wat er
   geverifieerd wordt: UI tegen echte routes, contract-drift fixen>) → daarna
   pas fase <n>.
6. **Fase <n> is exclusief**: <reden, bv. raakt package.json / npm install +
   audit verplicht> — nooit parallel met andere werkzaamheden.
7. **Fase <n+1> (<naam>) is exclusief**: <reden, bv. raakt vrijwel alle
   componentbestanden>.
8. **Laatste fase altijd als laatste**, door één agent, over het geheel.
9. **Git-hygiëne**: werk bij voorkeur in aparte branches/worktrees per track en
   merge op de sync-punten. Statusvelden in dit document alleen bijwerken voor
   de eigen track.

### Wat NIET parallel kan

| Combinatie               | Reden                                                                 |
| ------------------------ | --------------------------------------------------------------------- |
| Fase <x> ∥ Fase <y>      | <gedeelde code/bestanden/transactiepatronen — wees concreet>          |
| Fase <n> ∥ wat dan ook   | <bv. package.json + install + audit>                                  |
| Fase <m> intern splitsen | <waarom een fase niet verder opgedeeld mag worden — foutgevoeligheid> |

### Uitvoeringsmanifest (machine-leesbaar)

<!-- VERPLICHT (kernprincipe 7). Dit YAML-blok wordt geparsed door een
     orchestrator-agent die de fases als subagents uitvoert. Regels:

     - Genereer dit blok ALS LAATSTE, afgeleid uit het voltooide plan.
       Het moet 1-op-1 consistent zijn met het Statusoverzicht (zelfde
       fase-id's en -namen), de Afhankelijkheidsgraaf (zelfde volgorde en
       sync-punten) en de eigendomsregels (zelfde globs). Status hoort hier
       NIET thuis — die leeft uitsluitend in het Statusoverzicht.
     - `afhangt_van` is de formele afhankelijkheidsgraaf: een fase mag pas
       starten als ALLE genoemde fase-id's voltooid zijn. De tabel
       "Wat NIET parallel kan" moet hieruit afleidbaar zijn.
     - `track`: `sequentieel` (één agent, vóór de tracks), een tracknaam
       (`A`, `B`, … — parallelliseerbaar met andere tracks), of `exclusief`
       (één agent, nooit parallel met wat dan ook). Fases met dezelfde
       tracknaam draaien onderling sequentieel binnen die track.
     - `eigendom` is VERPLICHT voor elke fase met een tracknaam en moet
       exact de globs uit regel "Bestandseigendom" spiegelen. Voor
       sequentiële/exclusieve fases mag het weggelaten worden (die draaien
       alleen), tenzij een beperking zinvol is (bv. fase 0 alleen het
       contractbestand).
     - `gate` alleen opnemen als er een menselijke handeling nodig is
       (bv. DDL uitvoeren, secret aanmaken, deploy goedkeuren). De
       orchestrator STOPT na de fase, toont de gate-tekst en wacht op
       expliciete bevestiging. Formuleer de tekst als instructie aan de
       gebruiker, inclusief het verwachte bevestigingswoord.
     - `sync_punten`: na welke fases een integratieronde volgt en wat daar
       geverifieerd wordt. De orchestrator voert dit zelf uit (geen subagent)
       vóór de eerstvolgende fase start.
     - `verificatie` (plan-breed): de commando's waarmee elke fase afsluit.
       Een fase is pas voltooid als deze groen gerapporteerd zijn.
     - Verwijder niet-gebruikte optionele velden; laat geen placeholders
       achter in de output. -->

```yaml
plan:
  titel: '<zelfde titel als de H1>'
  verificatie: ['<bv. npm run typecheck>', '<bv. npm run lint>']
  verboden_voor_iedereen:
    - '<bv. package.json>' # <evt. uitzondering: behalve fase <n>>
    - '<bv. prisma/schema/**>' # behalve fase <n>
    - '<bv. src/i18n/locales/**>' # behalve fase <n>
fasen:
  - id: 0
    naam: '<Contracten>'
    afhangt_van: []
    track: sequentieel
    eigendom: ['<pad naar contractbestand(en)>']
  - id: 1
    naam: '<backend-bouwsteen 1>'
    afhangt_van: [0]
    track: A
    eigendom: ['<glob(s) van track A>']
    # gate: "<alleen indien menselijke handeling nodig; bv.: Voer het
    #        DDL-script uit tegen de database en bevestig met 'done'.>"
  - id: 2
    naam: '<backend-bouwsteen 2>'
    afhangt_van: [1]
    track: A
    eigendom: ['<glob(s) van track A>']
  - id: 3
    naam: '<frontend-bouwsteen 1>'
    afhangt_van: [0]
    track: B
    eigendom: ['<glob(s) van track B>']
  - id: 4
    naam: '<frontend-bouwsteen 2>'
    afhangt_van: [3]
    track: B
    eigendom: ['<glob(s) van track B>']
  - id: 5
    naam: '<cross-cutting fase>'
    afhangt_van: [2, 4]
    track: exclusief
  - id: 6
    naam: '<exclusieve fase, bv. i18n>'
    afhangt_van: [5]
    track: exclusief
  - id: 7
    naam: 'Validatie, opruimen & documentatie'
    afhangt_van: [6]
    track: exclusief
sync_punten:
  - na: [2, 4]
    doel: '<wat geverifieerd wordt: UI tegen echte routes, contract-drift fixen>'
```

---

## Voorwerk (afgerond vóór dit plan)

**Status: <✅ Klaar / n.v.t.>** — <wat al uitgevoerd en geverifieerd is>. De
uitvoerende agents mogen op onderstaande staat bouwen:

- <concreet artefact 1, met pad en relevante details (unieke keys, defaults, FK-gedrag)>
- <concreet artefact 2, incl. verificatie ("validate/generate groen")>
- Ontwerpbesluiten (vastgelegd, **niet heropenen zonder gebruikersoverleg**):
  - **<besluit 1>**: <één zin>.
  - **<besluit 2>**: <één zin>.
  - **<expliciete niet-scope>**.

---

## A. Doelmodel & architectuur

### Begrippen

<!-- Definieer elk domeinbegrip dat in de fases terugkomt. Eén rij per term,
     definitie scherp genoeg dat een agent er ontwerpbeslissingen op kan baseren. -->

| Term         | Definitie                                                      |
| ------------ | -------------------------------------------------------------- |
| **<Term 1>** | <definitie incl. eigenaarschap, levensduur, bron van waarheid> |
| **<Term 2>** | <definitie>                                                    |

### <Kernontwerp-onderwerp> (kernontwerp<, evt. "— afwijking t.o.v. de bron">)

<!-- Voor elk ontwerpaspect dat afwijkt van de bron/POC of foutgevoelig is:
     een kort was/wordt- of flow-diagram in een codeblok, plus de consequenties. -->

```
BRON/OUD:  <hoe het was>
NIEUW:     <hoe het wordt — API-vorm, dataflow, in enkele regels>
```

### Belangrijke bron → doel-verschillen (voor elke uitvoerende agent)

| #   | Verschil   | Consequentie voor de port/bouw                    |
| --- | ---------- | ------------------------------------------------- |
| 1   | <verschil> | <wat de agent anders moet doen dan de bron toont> |
| 2   | <verschil> | <consequentie>                                    |

---

## B. Doelstructuur (nieuwe/gewijzigde bestanden)

<!-- Volledige boom van alle nieuwe en gewijzigde bestanden, met per bestand
     een one-liner en de fase waarin het ontstaat. Dit is de bron van waarheid
     voor bestandseigendom per track. -->

```
<root>/
├── <map A (Track A)>/
│   ├── <bestand>.ts          # <doel> (fase 1)
│   └── <bestand>.ts          # <doel> (fase 2)
├── <map B (Track B)>/
│   ├── <component>.tsx       # <doel> (fase 3)
│   └── <component>.tsx       # <doel> (fase 4)
└── <gewijzigd bestand>       # <aard van de wijziging> (fase <n>)
```

---

## C. Referentiebestanden (bron → doel-mapping)

<!-- Alleen bij port/migratie. Exacte mapping zodat de agent nooit hoeft te
     zoeken. Markeer wat 1-op-1 kan en wat herschreven moet worden. -->

| Bronbestand | Doelbestand | Aard                                 |
| ----------- | ----------- | ------------------------------------ |
| `<bronpad>` | `<doelpad>` | 1-op-1 port / herschrijven omdat <…> |

---

<!-- ============================ FASE-SECTIES ============================
Per fase, altijd in dit format:

- Statusregel: **Status: <status>** · <Eigenaar/Track> · Vereist: <deps> · Geschat: <klein/middel/groot>
- Taken als checkboxes, gegroepeerd in genummerde subsecties (### N.M `bestand`)
  wanneer de fase meerdere bouwstenen heeft.
- Elke checkbox: concreet, met bestandspaden, signaturen, foutcodes, verwijzing
  naar bestaande patronen ("zelfde patroon als <bestand>").
- Afwijkingen tijdens uitvoering: sub-item met [~] en prefix **Afwijking**/**Noot**,
  cursief of ingesprongen, mét motivering. Nooit stilzwijgend afwijken.
- Grotere uitvoeringsnotities: blockquote "> **Fase N-noot (uitvoering)**: …"
  direct onder de fase, incl. baseline-vergelijking (fouten vóór/ná).
- Laatste checkbox van elke fase = verificatie (typecheck + lint + evt. build/tests).
======================================================================== -->

## Fase 0 — Contracten: <gedeelde types, validatieschema's & DTO's>

**Status: ⬜ Niet gestart** · Eigenaar: één agent, vóór de tracks · Geschat: klein

- [ ] `<pad>/types.ts` — alle gedeelde contracten:
  - <type 1 met exacte union-/veldopsomming>;
  - <type 2>;
  - Route-DTO's: <opsomming request/response-vormen per endpoint>;
  - Foutcodes (uitbreiding van `<bestaand foutcodepatroon>`): `'<code_1>' | '<code_2>' | …`.
- [ ] `<pad>/<validatieschema>.ts` — <schema-mirror van de types, bv. Zod>.
- [ ] `<pad>/<helper>.ts` — `<functiesignatuur>` → `<returnvorm>`: <gedragsspecificatie
      per variant, randgevallen expliciet>.
- [ ] `<verificatiecommando's>` groen; baseline noteren (pre-existing fouten tellen
      zodat regressies zichtbaar zijn).

> **Fase 0-noot (uitvoering)**: <in te vullen door de uitvoerende agent:
> afwijkingen, baseline vóór/ná, motivering>.

---

## Fase 1 — <naam> (Track A)

**Status: ⬜ Niet gestart** · Vereist: fase 0

### 1.1 `<bestand>.ts`

- [ ] <taak: wat de functie doet, signatuur, foutpaden als discriminated results,
      welke bestaande helpers/patronen hergebruikt worden, performance-aandachtspunten
      (bv. `select` op alleen benodigde velden)>.

### 1.2 `<bestand>.ts`

- [ ] <taak, genummerde stappen als de flow meerstaps is:>
  1. <stap>;
  2. <stap>;
  3. <stap>.
- [ ] <foutpaden en hun HTTP-mapping in de route>.

### 1.3 Route `<METHOD> <pad>`

- [ ] Body `<vorm>` via `<schema>`. <auth-resolutie + statuscode-mapping, verwijs
      naar een bestaande route als patroon>. <bijzonderheden zoals caching-flags>.
- [ ] `<verificatiecommando's>` groen.

---

## Fase 2 — <naam> (Track A)

**Status: ⬜ Niet gestart** · Vereist: fase 1 (<welke code gedeeld wordt>)

### 2.1 `<service>.ts`

- [ ] `<functie>(…)`: <gedragsspecificatie, incl. transactiegrenzen, race-guards
      (welke unieke constraint vangt de race, welke fout wordt afgevangen), en wat er
      gebeurt bij corrupte data (per-item overslaan + loggen, nooit de hele call laten
      falen)>.
- [ ] <CRUD-functies met eigendomschecks: welke keten wordt gevalideerd, welke
      statuscode bij mismatch>.
- [ ] <limieten/soft-caps met statuscode en verwijzing naar de beslissingentabel>.

### 2.2 Routes

- [ ] `<METHOD> <pad>` — <gedrag> → `<ResponseDTO>`.
- [ ] `<METHOD> <pad>` — <gedrag>.
- [ ] Alles: <gedeelde route-conventies: contextresolutie, envelope, foutformaat>.
- [ ] `<verificatiecommando's>` groen.

---

## Fase 3 — <naam> (Track B)

**Status: ⬜ Niet gestart** · Vereist: fase 0 (contracten) · Parallel met Track A

> Stijlreferentie: <bestaande pagina's/componenten die als visueel en technisch
> voorbeeld dienen; datalaag-conventies>.

- [ ] `<component>.tsx` — <gedrag, states (loading/empty/error), interacties>.
- [ ] <mock-/skeleton-states waarmee de UI zonder backend werkt (sync-punt-regel)>.
- [ ] `<verificatiecommando's>` groen.

---

## Fase 4 — <naam> (Track B)

**Status: ⬜ Niet gestart** · Vereist: fase 3

- [ ] <taken zoals fase 3>.
- [ ] `<verificatiecommando's>` groen.

---

## Fase 5 — <cross-cutting fase, bv. externe integratie> (na sync-punt)

**Status: ⬜ Niet gestart** · Vereist: sync-punt (Track A + B klaar) · Exclusief

- [ ] <taken; als deze fase dependencies toevoegt: install + audit als expliciete
      checkbox met blokkerende criteria (high/critical = niet opleveren)>.
- [ ] `<verificatiecommando's>` groen.

---

## Fase 6 — <exclusieve fase, bv. i18n>

**Status: ⬜ Niet gestart** · Exclusief, één agent, nooit parallel

- [ ] <taak met omvangsindicatie (bv. "geschat ~N strings") en de exacte
      bestemmingsbestanden/patronen>.
- [ ] <kwaliteitseis, bv. beide talen volwaardig>.
- [ ] `<verificatiecommando's>` groen.

---

## Fase 7 — Validatie, opruimen & documentatie

**Status: ⬜ Niet gestart** · Laatste fase, één agent

- [ ] `<volledige verificatiesuite: typecheck, lint, build, audit, tests>` — alles
      groen / geen high/critical.
- [ ] **Handmatige smoke-test-checklist** (gebruiker of agent met draaiende app):
  1. <end-to-end-scenario 1, geformuleerd als waarneembaar gedrag>;
  2. <scenario 2 — dek expliciet de randgevallen uit de risicotabel>;
  3. <scenario n>.
- [ ] Opruimen: <mock-states, dode comments, TODO's, tijdelijke conventies>.
- [ ] Dit document: alle statusvelden definitief zetten; afwijkingen t.o.v. het
      plan documenteren in een slotsectie.

---

## Openstaande beslissingen (met aanbevolen default)

<!-- Elke open vraag MOET een default hebben zodat een agent zonder antwoord
     verder kan. Verwijs vanuit de fases naar deze tabel op nummer. -->

| #   | Vraag   | Aanbeveling (default bij geen tegenbericht)       |
| --- | ------- | ------------------------------------------------- |
| 1   | <vraag> | <default + korte motivering + evt. v2-verwijzing> |
| 2   | <vraag> | <default>                                         |

---

## Risico's & mitigaties

| Risico                              | Mitigatie                                             |
| ----------------------------------- | ----------------------------------------------------- |
| <technisch/functioneel risico>      | <concrete mitigatie, met fase- of bestandsverwijzing> |
| <race condition / data-integriteit> | <constraint + afgevangen fout + gedrag>               |
| <track-blokkade>                    | <contract-first + mocks + sync-punt>                  |
| <dependency-/security-risico>       | <audit-regel + blokkerend criterium>                  |

---

## Slotsectie: afwijkingenlog (in te vullen bij oplevering)

<!-- Fase 7 vult dit in: elke [~] en elke plan-afwijking samengevat, met
     motivering en impact, zodat het plan achteraf de werkelijkheid beschrijft. -->

| Fase | Afwijking | Motivering | Impact |
| ---- | --------- | ---------- | ------ |
|      |           |            |        |
