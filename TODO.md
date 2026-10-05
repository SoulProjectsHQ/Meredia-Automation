# TODO

Prioritert. Øverste uferdige oppgave tas først.

## 1. Lead-repository i `src/crm`

CRUD mot SQLite for `company`, `contact`, `lead`, `interaction`, `email`, `task` og `research_source`.

- Duplikatsjekk før innlegging (bruk `find_duplicate`).
- Statusendring bare via `transition`.
- Eksport (CSV og JSON), oppdatering og sletting. Sletting er et kontrollpunkt.
- Hvert funn kan spores til kilde.
- Tester mot SQLite i minnet.

## 2. Research-modul

- Brønnøysund-klient bak et grensesnitt som kan mockes. Offentlig API, ingen nøkkel.
- Nettsideanalyse: HTTPS, viewport/mobil, tittel og metabeskrivelse, CTA, kontaktpunkter. Mockbar HTTP.
- Hver observasjon lagres med kilde.

## 3. LLM-klient og prompt-lasting

- Tynn klient bak et grensesnitt som kan mockes.
- Last prompts fra `prompts/` og logg promptversjon.
- Validér JSON-svar mot output-skjemaene i promptene.

## 4. Pipeline

- Egne fasefunksjoner i `src/automation`, ingen stor skriptfil.
- Kontrollpunkt før utsending.
- Logging per fase, feilklasser og retry med timeout og rate limiting.

## 5. E-postintegrasjon, kun DRAFT

- Graph eller Gmail. Først bare opprette utkast.
- Utsending krever `EMAIL_SENDING_ENABLED=true` og en `APPROVED`-mail.
- Logg mottaker, tidspunkt, lead-ID, promptversjon, status og message ID.

## 6. Dashboard (`src/dashboard`)

Nye, kvalifiserte og klare leads. Utsendte mailer, forfalte oppfølginger, svar, interesserte, møter, tilbud, vunnet, tapt. Vis score, hvorfor leadet passer, observert problem, anbefalt tjeneste, siste og neste aktivitet. Enkelt og profesjonelt, arbeidsflyt før pynt.

## 7. Svarklassifisering

Koble `prompts/classify-response.md` til CRM. Menneske bekrefter statusendring.

## Åpne spørsmål til Aleksander

1. Er Python greit som språk, eller vil du ha TypeScript?
2. Graph (Outlook) eller Gmail som første e-postintegrasjon?
3. Skal mailutkast ligge i Outlook/Gmail-utkast eller i egen database?
4. Hva skal dashboardet bygges med, og hvor skal det kjøre (deployment)?
5. Hvilke dagsgrenser vil du ha for utsending i starten? `.env.example` har 10 per dag som plassholder.
