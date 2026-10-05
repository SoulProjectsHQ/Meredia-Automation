# Meredia Automation

Interne verktøy for Meredia Digital: prospektering, lead-kvalifisering, CRM, e-postutkast og oppfølging. Målet er bedre leads og mindre manuelt arbeid, med et menneske som godkjenner alt som sendes.

Status per i dag står i `PROJECT_STATE.md`. Prioritert arbeid står i `TODO.md`.

## Arkitektur

Fasene i salgsflyten er egne moduler:

```
DISCOVER -> RESEARCH -> VALIDATE -> SCORE -> QUALIFY -> FIND CONTACT
-> PERSONALIZE -> DRAFT EMAIL -> HUMAN REVIEW -> SEND -> LOG -> FOLLOW UP
-> CLASSIFY RESPONSE -> NEXT ACTION
```

| Mappe | Innhold | Status |
| --- | --- | --- |
| `src/crm` | modeller, validering, duplikater, statusoverganger, SQLite-skjema, lead-repository, eksport | ferdig |
| `src/scoring` | lead score 0 til 100 | ferdig |
| `src/email` | e-poststatus, oppfølgingsdatoer, mailregler | ferdig kjerne |
| `src/automation` | feilklasser, pipeline-logg | ferdig kjerne |
| `src/research` | research og nettsideanalyse | ikke startet |
| `src/prospecting` | discovery og kvalifisering | ikke startet |
| `src/integrations` | Graph, Gmail, Brønnøysund | ikke startet |
| `src/dashboard` | internt dashboard | ikke startet |
| `prompts/` | versjonerte prompts | første versjon |
| `docs/` | forretningsregler og sikkerhet | første versjon |

## Oppsett

Krever Python 3.11 eller nyere. Ingen eksterne pakker er nødvendig foreløpig.

```sh
cp .env.example .env   # fyll inn lokalt, .env ligger i .gitignore
```

Miljøvariabler står i `.env.example`. Ingen hemmeligheter skal i kode eller i git.

## Utvikling

- Importer med `src.`-prefiks: `from src.crm.models import Lead`.
- Alle prompts ligger i `prompts/`, ikke i kildekoden.
- Les `CLAUDE.md` for kodestandard og kontrollpunkter.

## Testing

```sh
sh scripts/run_tests.sh
```

Testene bruker ingen ekte kunder og ingen eksterne tjenester.

## Deployment

Ikke avgjort. Se `TODO.md`.

## Integrasjoner

Ingen er koblet til ennå. Planlagt: Microsoft Graph eller Gmail (kun utkast i første omgang), Brønnøysundregistrene. Alle integrasjoner skal kunne mockes.

## Sikkerhet

Se `docs/security.md`. Kort: hemmeligheter i miljøvariabler, ekte data utenfor git, ingen utsending uten godkjenning fra et navngitt menneske, utsending avslått som standard.
