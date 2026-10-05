---
name: scoring
version: 1
used_by: src/scoring
---

# Lead-scoring

Gi poeng per kategori basert på research. Koden summerer og klassifiserer. Du skal ikke regne ut totalen.

| Kategori | Maks | Spørsmål |
| --- | --- | --- |
| size_fit | 20 | Passer størrelsen målgruppen? |
| clear_problem | 20 | Er det et tydelig problem Meredia løser? |
| financial_capacity | 15 | Er det sannsynlig økonomisk kapasitet? |
| reachable_contact | 15 | Finnes en tilgjengelig beslutningstaker eller kontakt? |
| professional_firm | 10 | Er det en profesjonell tjenestebedrift? |
| growth_or_change | 10 | Er det vekst eller endring? |
| improvement_potential | 10 | Har nettside eller digital løsning tydelig forbedringspotensial? |

## Regler

- Heltall mellom 0 og kategoriens maks. Poeng utenfor området avvises av koden.
- Mangler dokumentasjon for en kategori, gi lavt poeng og skriv `UNVERIFIED` i begrunnelsen.
- Begrunn hver kategori i én setning med kilde.
- Scoren er intern og skal aldri nevnes i noe som sendes til kunden.

## Output (JSON)

```json
{
  "size_fit": 0,
  "clear_problem": 0,
  "financial_capacity": 0,
  "reachable_contact": 0,
  "professional_firm": 0,
  "growth_or_change": 0,
  "improvement_potential": 0,
  "reasons": {"size_fit": "", "clear_problem": ""}
}
```
