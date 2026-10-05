# TODO

Prioritert. Øverste uferdige oppgave tas først.

## 1. Research-modul

- Brønnøysund-klient bak et grensesnitt som kan mockes. Offentlig API, ingen nøkkel.
- Nettsideanalyse: HTTPS, viewport/mobil, tittel og metabeskrivelse, CTA, kontaktpunkter. Mockbar HTTP.
- Hver observasjon lagres med kilde via `LeadRepository.add_research_source`.

## 2. LLM-klient og prompt-lasting

- Tynn klient bak et grensesnitt som kan mockes.
- Last prompts fra `prompts/` og logg promptversjon.
- Validér JSON-svar mot output-skjemaene i promptene.

## 3. Pipeline

- Egne fasefunksjoner i `src/automation`, ingen stor skriptfil.
- Kontrollpunkt før utsending.
- Logging per fase, feilklasser og retry med timeout og rate limiting.

## 4. E-postintegrasjon, kun DRAFT

- Graph eller Gmail. Først bare opprette utkast.
- Utsending krever `EMAIL_SENDING_ENABLED=true` og en `APPROVED`-mail.
- Logg mottaker, tidspunkt, lead-ID, promptversjon, status og message ID.

## 5. Dashboard (`src/dashboard`)

Nye, kvalifiserte og klare leads. Utsendte mailer, forfalte oppfølginger, svar, interesserte, møter, tilbud, vunnet, tapt. Vis score, hvorfor leadet passer, observert problem, anbefalt tjeneste, siste og neste aktivitet. Enkelt og profesjonelt, arbeidsflyt før pynt.

## 6. Svarklassifisering

Koble `prompts/classify-response.md` til CRM. Menneske bekrefter statusendring.

## Senere, ved behov

- `add_lead` henter alle leads for duplikatsjekk. Greit ved moderat volum. Bytt til indeksert oppslag på org.nr og domene hvis tabellen blir stor.
- Utsendingsplan som velger riktig dag og viser forfalte oppfølginger (bruker `src/email/schedule.py` og `list_open_tasks`).

## Åpne spørsmål til Aleksander

1. Er Python greit som språk, eller vil du ha TypeScript?
2. Graph (Outlook) eller Gmail som første e-postintegrasjon?
3. Skal mailutkast ligge i Outlook/Gmail-utkast eller i egen database?
4. Hva skal dashboardet bygges med, og hvor skal det kjøre (deployment)?
5. Hvilke dagsgrenser vil du ha for utsending i starten? `.env.example` har 10 per dag som plassholder.
6. Hva skjer hvis noen på `DO_NOT_CONTACT` krever sletting? Nå nektes sletting for at vi ikke skal kontakte dem igjen. Alternativet er en minimal sperreliste med org.nr og domene.
7. Skal `QUALIFIED` kreve en minimumsscore? Nå kreves bare at en score finnes. Under 40 er "ikke prioriter" i reglene, men kan fortsatt settes til `QUALIFIED` manuelt.
