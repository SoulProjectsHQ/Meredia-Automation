---
name: qualification
version: 1
used_by: src/prospecting
---

# Lead-kvalifisering

Vurder om selskapet passer Meredia Digital, basert på research-output. Prioriter kvalitet fremfor antall.

## Målgruppe

Norske SMB, normalt 6 til 20 ansatte, opp til omtrent 50. Prioritert rekkefølge:

1. Regnskapsfirmaer
2. Advokatfirmaer
3. HR- og administrasjonskonsulenter
4. Rådgivningsbedrifter
5. Små profesjonelle tjenestebedrifter
6. Media- og markedsføringsbedrifter med svak digital infrastruktur eller nettside

Avvis bedrifter hvor Meredias tjenester åpenbart ikke passer.

## Vurder

- Bedriften: størrelse, bransje, lokasjon, nettside, organisasjonsstatus, vekst, profesjonalitet.
- Mulige problemer: gammel nettside, dårlig mobilvisning, uklare tjenester, svak SEO, teknisk feil, private e-postadresser i profesjonell sammenheng, uklare kontaktpunkter, mange ansatte uten tydelig IT-funksjon, vekst, rekruttering, flere lokasjoner, nyetablert.
- Kontaktperson: daglig leder, eier, partner, administrativ leder, IT-ansvarlig, driftsansvarlig. For små virksomheter er daglig leder eller eier normalt riktig.

## Regler

- Kun dokumenterte observasjoner teller som problem. Resten formuleres som mulig forbedringsområde.
- Bruk generell bedriftsmail hvis personlig bedriftsmail ikke er funnet. Ikke gjett adresser.
- Foreslå tjenesten som gir mest verdi for akkurat dette selskapet. Ikke press standardpakker.

## Output (JSON)

```json
{
  "fits_target_group": true,
  "reason_for_fit": "",
  "identified_problem": "",
  "recommended_service": "",
  "contact_recommendation": {"name": "UNKNOWN", "role": "UNKNOWN", "email": "UNKNOWN", "verified": false},
  "reject_reason": ""
}
```
