# E-postregler

## Kald e-post

Hver mail skal være relevant for den spesifikke bedriften. Ingen generiske AI-mails.

Innhold, i denne rekkefølgen:

1. Kort personlig inngang
2. Hvorfor Meredia tar kontakt
3. Ett konkret relevant problem eller forbedringsområde
4. Hvordan Meredia hjelper
5. Lav terskel for svar

Regler:

- 70 til 140 ord.
- Norsk, naturlig språk. Skriv som en profesjonell person.
- Ikke overlast med tjenester. Ikke aggressivt salgsspråk.
- Forbudte uttrykk: revolusjonerende, markedsledende, unik løsning, game changer, verdensklasse, "jeg håper denne mailen finner deg vel".

Ordtelling og forbudte uttrykk sjekkes i kode (`src/email/rules.py`), ikke av LLM.

Prompten ligger i `prompts/cold-email.md`.

## Oppfølging

Maks to automatiske oppfølginger, kortere enn første mail. Timing står i `docs/sales-process.md`. Prompten ligger i `prompts/followup.md`.

## Status: DRAFT, APPROVED, SENT

En AI-generert mail er alltid `DRAFT`. Den blir `APPROVED` først når et navngitt menneske godkjenner. Bare `APPROVED` kan sendes. Redigering av en godkjent mail ugyldiggjør godkjenningen. Se `src/email/status.py`.

## Logging

Logg mottaker, tidspunkt, lead-ID, template- eller promptversjon, status og message ID hvis tilgjengelig. Ikke logg mer av innholdet enn nødvendig. Mailtekst lagres ikke i databasen foreløpig.

## Svar

Svar klassifiseres etter `prompts/classify-response.md`. Ber noen om å slippe mer kontakt, settes leadet til `DO_NOT_CONTACT` og all utsending stopper.
