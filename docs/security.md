# Sikkerhet og persondata

Systemet behandles som et produksjonssystem så snart ekte kunde- eller leaddata brukes.

## Hemmeligheter

- Ingen API-nøkler, passord, OAuth secrets, access tokens eller databasepassord i kode.
- Alt ligger i miljøvariabler. Lokalt i `.env`, som står i `.gitignore`.
- `.env.example` viser variablene uten verdier.
- Loggen maskerer Bearer-tokens, `api_key`, `token`, `secret`, `password` og `sk-`-nøkler før noe skrives (`src/automation/pipeline_log.py`).

## Minste privilegium

- Research trenger ikke tilgang til å sende e-post.
- Dashboardet trenger ikke administrative Microsoft 365-funksjoner.
- Utsending er avslått som standard (`EMAIL_SENDING_ENABLED=false`).

## Kontrollpunkter

Se listen i `docs/sales-process.md`. Automatisering kan støtte disse stegene, men fjerner dem ikke uten eksplisitt instruks.

## Persondata

- Lagre bare data med forretningsmessig formål: bedrift, profesjonell rolle og offentlig profesjonell kontaktinformasjon.
- Ikke bygg profiler over privatpersoner.
- Data skal kunne oppdateres, eksporteres, slettes og spores til kilde (`research_source`).
- E-postadresser merkes som bekreftet eller ikke (`contact_email_verified`). Ikke gjett adresser.
- Ikke samle unødvendig personlig informasjon i research.

Data i `data/` og `logs/` ligger utenfor git.

## Feilhåndtering

Feil klassifiseres som `temporary_error`, `validation_error`, `authentication_error`, `rate_limit`, `external_service_error` eller `internal_error` (`src/automation/errors.py`). Midlertidige feil, rate limit og eksterne tjenestefeil kan prøves på nytt. Validering, autentisering og interne feil stopper kjøringen. Automatisering skal ikke fortsette ukritisk etter feil.

## Sikkerhetsmodell i korte trekk

1. Hemmeligheter kun i miljøvariabler.
2. Ekte data kun lokalt, utenfor git.
3. Ingen utsending uten godkjenning fra et navngitt menneske.
4. Loggene inneholder ingen secrets.
5. Hver integrasjon får bare de rettighetene den trenger.
