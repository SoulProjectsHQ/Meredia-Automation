---
name: cold-email
version: 1
used_by: src/email
---

# Kald e-post

Skriv ett utkast til en kald e-post fra Meredia Digital. Utkastet er alltid DRAFT. Et menneske godkjenner før noe sendes.

## Input

- company_name
- contact_name (kun hvis bekreftet, ellers bruk generell hilsen)
- konkret observasjon fra research, med kilde
- recommended_service
- identified_problem

## Struktur

1. Kort personlig inngang
2. Hvorfor vi tar kontakt
3. Ett konkret, relevant problem eller forbedringsområde
4. Hvordan vi hjelper
5. Lav terskel for svar

## Regler

- 70 til 140 ord. Kort.
- Norsk, naturlig språk. Skriv som en profesjonell person, ikke en markedsføringsrobot.
- Bruk "vi" om Meredia. Kundeverdi først. Ikke list opp teknologier, forklar resultatet kunden får.
- Ett konkret poeng. Ikke overlast med tjenester.
- Observasjoner er formulert som mulighet: "Dette kan være et område hvor vi kan bidra."
- Ikke påstå sikkerhetsproblemer uten dokumentasjon.
- Ikke nevn lead score.
- Forbudte uttrykk: revolusjonerende, markedsledende, unik løsning, game changer, verdensklasse, "jeg håper denne mailen finner deg vel". Ikke aggressivt salgsspråk.

## Eksempel på ramme

Emne: Nettsiden deres

Hei [navn],

Jeg kom over [bedrift] og tok en titt på nettsiden deres. Jeg la spesielt merke til [konkret observasjon].

Vi i Meredia Digital hjelper små og mellomstore bedrifter med nettsider og den digitale arbeidshverdagen, blant annet e-post, brukere, tilganger, PC-er og sikkerhet. For dere tror jeg særlig [konkret tjeneste] kan være relevant.

Jeg kan gjerne sette opp et forslag på hvordan vi ville løst dette for [bedrift]. Er det interessant?

Vennlig hilsen
Aleksander Joon Meløysund
Meredia Digital

## Output

```json
{"subject": "", "body": ""}
```
