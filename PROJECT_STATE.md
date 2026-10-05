# Prosjektstatus

Sist oppdatert: 2026-10-05

## Ferdig

- Mappestruktur etter avtalt oppsett.
- `src/crm`: `Lead`-modell, faste statuser, tillatte statusoverganger, validering (organisasjonsnummer med modulus 11, e-postformat, domene), duplikatsjekk (org.nr, domene, navn), SQLite-skjema med `company`, `contact`, `lead`, `interaction`, `email`, `task`, `research_source`.
- `src/crm/repository.py`: `LeadRepository` med innlegging (validering og duplikatsjekk først), oppdatering, statusendring via `transition`, interaksjoner, e-postlogg, oppgaver, research-kilder og sletting (krever navngitt menneske, nekter `DO_NOT_CONTACT`).
- `src/crm/export.py`: JSON- og CSV-eksport, CSV nøytraliserer formler.
- `src/crm/approval.py`: delt sjekk for kontrollpunkter, brukes av e-poststatus og sletting.
- `src/scoring`: scoring etter de sju kategoriene, klassifisering i fire nivåer.
- `src/email`: status `DRAFT`, `APPROVED`, `SENT` med krav om navngitt menneske ved godkjenning, oppfølgingsdatoer (tirsdag til torsdag, maks to), mekanisk mailsjekk (ordtelling, forbudte uttrykk).
- `src/automation`: feilklasser med retry-flagg, pipeline-logg med maskering av secrets.
- `prompts/`: research, qualification, scoring, cold-email, followup, classify-response (alle versjon 1).
- `docs/`: forretningsmodell, tjenester, målgruppe, salgsprosess, e-postregler, sikkerhet.
- 128 enhetstester, alle grønne.

## Beslutninger tatt

- Python 3.11 med standardbiblioteket, SQLite, `unittest`. Ingen eksterne dependencies.
- Delt infrastruktur (feil, logging) ligger i `src/automation`. Ingen `src/utils`.
- `Lead` bruker `None` for ukjent verdi. UI viser `UNKNOWN`. Feltet `contact_email_verified` skiller bekreftet fra ubekreftet e-post.
- Mailtekst lagres ikke i databasen. Hvor utkast skal ligge er ikke avgjort.
- Første oppfølging settes til nærmeste tirsdag til torsdag på eller etter dag 4. Andre på eller etter dag 7 etter første oppfølging.
- Nye leads starter alltid som `NEW`. `QUALIFIED` og `READY_TO_CONTACT` krever score. `READY_TO_CONTACT` krever bekreftet e-post.
- `DO_NOT_CONTACT`-leads slettes aldri, fordi de hindrer ny kontakt.
- Databasen avviser `APPROVED`- og `SENT`-mail uten godkjenner.
- E-postintegrasjon blir Gmail med Google Workspace (besluttet av Aleksander). Ingen Graph.

## Ikke startet

Research, prospektering, integrasjoner, dashboard, LLM-klient, pipeline som kobler fasene. Se `TODO.md`.

## Åpne spørsmål til Aleksander

Se bunnen av `TODO.md`.

## Kjente begrensninger

- Ingen ekte lead-data i repoet. `data/` og `logs/` ligger utenfor git.
- Ingenting sender e-post. Det finnes ingen e-postintegrasjon.
- Duplikatsjekken leser alle leads ved innlegging. Greit ved moderat volum.
