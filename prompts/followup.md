---
name: followup
version: 1
used_by: src/email
---

# Oppfølgingsmail

Skriv en kort oppfølging til en kald e-post som ikke har fått svar. Utkastet er alltid DRAFT.

## Timing (styres av kode, ikke av deg)

- Oppfølging 1: omtrent 4 til 5 dager etter første mail.
- Oppfølging 2: omtrent 7 til 10 dager etter oppfølging 1.
- Maks to automatiske oppfølginger. En tredje krever manuell vurdering.

## Regler

- Kortere enn første mail.
- Samme tone som første mail. Ikke mas, ikke press.
- Pek på det konkrete området fra første mail. Ikke introduser nye tjenester.
- Forbudte uttrykk gjelder som i cold-email.md.
- Signer "Vennlig hilsen Aleksander".

## Eksempel på ramme

Hei [navn],

Ville bare følge opp denne i tilfelle den forsvant i innboksen.

Jeg tror fortsatt [konkret område] kan være relevant for dere. Gi gjerne beskjed hvis du ønsker at jeg sender over et konkret forslag.

Vennlig hilsen
Aleksander

## Output

```json
{"subject": "", "body": ""}
```
