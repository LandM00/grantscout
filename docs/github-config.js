// Config per il pulsante "Avvia scansione ora": permette di lanciare lo
// scraper (il workflow GitHub Actions che normalmente parte da solo ogni
// 6 ore) direttamente dall'app, senza andare su GitHub.
//
// ATTENZIONE: questo token è visibile a chiunque guardi il codice sorgente
// di questa pagina — è vero per qualunque sito statico come questo, non
// c'è modo di nasconderlo davvero senza un server proprio. Per questo:
//   1. Usa un "Fine-grained personal access token" (non quelli "classic"):
//      https://github.com/settings/tokens?type=beta
//   2. "Repository access" -> "Only select repositories" -> scegli SOLO
//      questo repository (grantscout).
//   3. "Permissions" -> "Repository permissions" -> "Actions" -> imposta
//      "Read and write". Lascia tutti gli altri permessi su "No access"
//      (in particolare NON dare accesso a "Contents" né a "Secrets").
//   4. Genera il token e incollalo qui sotto al posto del testo di esempio.
//
// Con questi permessi, il peggio che può succedere se qualcuno trova il
// token è che lanci scansioni a vuoto: fastidioso ma innocuo — non può
// leggere né modificare il codice, i segreti o altri dati del progetto, e
// i minuti di GitHub Actions sono comunque gratuiti sui repository
// pubblici. Vedi SETUP.md, sezione "Avvia scansione dall'app".
window.GITHUB_CONFIG = {
  token: "INCOLLA_QUI_IL_TUO_TOKEN_GITHUB",
  owner: "LandM00",
  repo: "grantscout",
  workflowFile: "scan.yml",
  ref: "main"
};
