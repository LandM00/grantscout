// Config per il pulsante "Avvia scansione ora". A differenza della prima
// versione, qui NON c'è nessun token di GitHub: il token vero vive solo
// dentro un piccolo "Cloudflare Worker" esterno (vedi SETUP.md, sezione
// 14), che è l'unico a poter parlare con l'API di GitHub. Qui ci sono
// solo due valori a basso rischio:
//   - workerUrl: l'indirizzo pubblico di quel Worker;
//   - sharedSecret: una password condivisa che il Worker controlla
//     prima di accettare la richiesta (non è un token GitHub).
// Il peggio che può succedere se qualcuno trova questi due valori è che
// lanci scansioni a vuoto: fastidioso ma innocuo.
window.SCAN_CONFIG = {
  workerUrl: "INCOLLA_QUI_URL_DEL_WORKER",
  sharedSecret: "INCOLLA_QUI_LA_PASSWORD_CONDIVISA"
};
