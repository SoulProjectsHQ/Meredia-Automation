---
name: classify-response
version: 1
used_by: src/email
---

# Klassifisering av svar

Klassifiser et svar fra en prospect. Du foreslår. Et menneske bekrefter statusendringen.

## Klasser

| class | Betyr | Foreslått status | Foreslått neste handling |
| --- | --- | --- | --- |
| interested | Vil vite mer | INTERESTED | Svar og foreslå møte |
| meeting_request | Ber om møte eller samtale | MEETING | Finn tidspunkt |
| question | Stiller et spørsmål | REPLIED | Svar på spørsmålet |
| not_now | Ikke aktuelt nå, kanskje senere | REPLIED | Notér og sett påminnelse |
| not_interested | Takker nei | LOST | Takk høflig, ingen mer kontakt |
| do_not_contact | Ber om å ikke bli kontaktet igjen | DO_NOT_CONTACT | Stopp all utsending |
| wrong_person | Feil mottaker | REPLIED | Be om riktig kontakt |
| out_of_office | Autosvar | uendret | Vent og prøv på nytt etter retur |
| unclear | Uklart | REPLIED | Send til manuell vurdering |

## Regler

- Ber noen om å slippe mer kontakt, er det alltid `do_not_contact`, uansett tone.
- Er du usikker, velg `unclear`.
- Ikke lagre mer av mailinnholdet enn nødvendig. Sammendrag på én setning.

## Output (JSON)

```json
{"class": "", "suggested_status": "", "next_action": "", "summary": ""}
```
