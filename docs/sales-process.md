# Salgsprosess

## Arbeidsflyt

```
DISCOVER -> RESEARCH -> VALIDATE -> SCORE -> QUALIFY -> FIND CONTACT
-> GENERATE PERSONALIZATION -> DRAFT EMAIL -> HUMAN REVIEW OR APPROVED AUTOMATION
-> SEND -> LOG -> FOLLOW UP -> CLASSIFY RESPONSE -> NEXT ACTION
```

Fasene holdes separert i koden. Hver fase er en egen modul under `src/`.

## Lead-statuser (faste verdier)

`NEW`, `RESEARCHED`, `QUALIFIED`, `READY_TO_CONTACT`, `CONTACTED`, `FOLLOWUP_1`, `FOLLOWUP_2`, `REPLIED`, `INTERESTED`, `MEETING`, `PROPOSAL`, `WON`, `LOST`, `DO_NOT_CONTACT`.

Ikke opprett egne statuser uten god grunn. Tillatte overganger ligger i `src/crm/status.py`. Alle åpne statuser kan gå til `LOST` eller `DO_NOT_CONTACT`. `DO_NOT_CONTACT` er endelig.

## Oppfølging

- Første oppfølging: omtrent 4 til 5 dager etter første mail.
- Andre oppfølging: omtrent 7 til 10 dager etter første oppfølging.
- Maks to automatiske oppfølginger. En tredje krever manuell vurdering.
- Oppfølgingsmail er kortere enn første mail.

Koden rykker datoer frem til nærmeste tirsdag, onsdag eller torsdag. Se `src/email/schedule.py`.

## Utsending

- Ikke masseutsend identiske e-poster. Hver mail knyttes til research på selskapet.
- Foretrukne dager: tirsdag, onsdag, torsdag. Mandag kveld ved behov.
- Unngå ordinær kald utsendelse på fredag, lørdag og søndag.
- Start med moderat volum.

## CRM-felt

`company_name`, `organization_number`, `website`, `industry`, `employee_count`, `location`, `contact_name`, `contact_role`, `contact_email`, `source`, `lead_score`, `lead_status`, `reason_for_fit`, `identified_problem`, `recommended_service`, `first_contact_date`, `followup_1_date`, `followup_2_date`, `last_contact_date`, `next_action`, `notes`.

Datamodellen (`src/crm/schema.sql`) har tabellene `company`, `contact`, `lead`, `interaction`, `email`, `task` og `research_source`. Ett selskap kan ha flere kontakter, interaksjoner, kilder og salgsaktiviteter.

## Duplikater

Før et nytt lead legges inn, sjekk organisasjonsnummer, domene, selskapsnavn og tidligere kontakt. Organisasjonsnummer er primær identifikator. Ikke kontakt samme bedrift flere ganger fordi den finnes fra flere kilder. Se `src/crm/dedupe.py`.

## Kontrollpunkter (human-in-the-loop)

Disse handlingene krever et menneske:

- utsending til nye leads
- sletting av kundedata
- større endringer i CRM
- tilbud og avtaler
- endringer i produksjon
- endringer i Microsoft 365 eller Google Workspace hos kunder

Kontrollpunkter fjernes ikke uten eksplisitt instruks.
