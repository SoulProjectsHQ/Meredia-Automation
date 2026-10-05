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

- Gmail API med Google Workspace (besluttet). Først bare opprette utkast i Gmail.
- Gmail-scopes: verifiser mot Google-dokumentasjonen når vi bygger. Så vidt jeg vet kan et scope som oppretter utkast også sende, så "bare utkast" må sikres i koden og med `EMAIL_SENDING_ENABLED`, ikke bare med rettigheter.
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
2. Mailutkast: forslag er å la utkastene ligge i Gmail-utkast, så du leser og godkjenner dem i vanlig innboks. Databasen logger bare mottaker, tidspunkt, status og message ID. Samme løsning som i dag. Gjelder hvis du ikke sier noe annet.
3. Hva skal dashboardet bygges med, og hvor skal det kjøre (deployment)?
4. Hvilke dagsgrenser vil du ha for utsending i starten? `.env.example` har 10 per dag som plassholder.
5. `DO_NOT_CONTACT` og sletting. En bedrift ber oss slutte å kontakte dem. Senere ber de om at alt om dem slettes. Nå nekter systemet å slette dem, fordi raden er sperren som hindrer ny kontakt via en annen kilde. Forslag: behold bare en minimal sperre (org.nr og domene), slett resten.
6. Minimumsscore for `QUALIFIED`. Nå kreves bare at en score finnes, også om den er 25. Forslag: krev 60 eller mer. Leads på 40 til 59 kan løftes manuelt med en kort begrunnelse som logges.

Besluttet: Gmail med Google Workspace som e-postintegrasjon (punkt 2 i forrige liste). Python er beholdt som språk til noe annet er sagt.
