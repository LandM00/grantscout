# Guida all'attivazione — AgriMigra Watch

Circa 20-30 minuti la prima volta, tutto gratuito. Serve: un account
Google (per Firebase) e un account GitHub (dici di averlo già).

## 1. Crea un progetto Firebase gratuito

1. Vai su <https://console.firebase.google.com> ed entra con un account Google.
2. "Aggiungi progetto" → dagli un nome, es. `agrimigra-watch`.
3. Quando chiede di Google Analytics, puoi disattivarlo (non serve).
4. Crea il progetto.

## 2. Attiva il database (Firestore)

1. Nel menu a sinistra: **Build → Firestore Database**.
2. "Crea database" → modalità **produzione** → scegli una regione europea
   (es. `eur3 (europe-west)`).

## 3. Incolla le regole di sicurezza

1. Nella pagina di Firestore, scheda **Regole**.
2. Apri il file `firestore.rules` di questo progetto (in VSCode), copia
   tutto il contenuto e incollalo al posto di quello presente.
3. Clicca **Pubblica**.

Queste regole dicono: ogni persona ha il proprio account (vedi punto 13)
e vede/gestisce SOLO le proprie parole chiave e i propri bandi trovati —
nessuna lettura è possibile senza avere effettuato l'accesso. L'elenco
delle fonti monitorate (le pagine da controllare) resta invece condiviso:
chiunque abbia un account può leggerlo e modificarlo, è un catalogo
comune. Nessuno tranne lo scraper può scrivere i risultati dei bandi.

## 4. Registra un'app web e prendi la configurazione

1. Icona ingranaggio in alto a sinistra → **Impostazioni progetto**.
2. In basso, "Le tue app" → icona **`</>`** (Web).
3. Dai un nickname qualsiasi (es. `agrimigra-web`) → **Registra app**.
   Non serve attivare Firebase Hosting.
4. Ti mostra un blocco di codice con `const firebaseConfig = {...}`:
   tienilo a portata di mano per il prossimo passo.

## 5. Incolla la configurazione nel progetto

Apri `docs/firebase-config.js` in VSCode e sostituisci i valori segnaposto
con quelli copiati al passo 4 (apiKey, authDomain, projectId, ecc.).
Questi valori NON sono segreti: possono stare tranquillamente nel codice
pubblico dell'app.

## 6. Crea la chiave per lo scraper (Admin SDK)

1. Impostazioni progetto → scheda **Account di servizio**.
2. **Genera nuova chiave privata** → conferma. Si scarica un file `.json`.
3. Questo file **è segreto**: non va mai messo nel repository (è già
   escluso in `.gitignore` per sicurezza). Lo useremo solo al passo 8.

## 7. Crea il repository GitHub e carica il progetto

1. Su github.com crea un nuovo repository, es. `agrimigra-watch`
   (pubblico o privato, per questo progetto è indifferente).
2. Da VSCode, apri il terminale nella cartella del progetto ed esegui:

   ```bash
   git init
   git remote add origin https://github.com/TUO-UTENTE/agrimigra-watch.git
   git add .
   git commit -m "Prima versione di AgriMigra Watch"
   git branch -M main
   git push -u origin main
   ```

## 8. Aggiungi la chiave come "secret" su GitHub

1. Sul repository, su github.com: **Settings → Secrets and variables →
   Actions → New repository secret**.
2. Nome: `FIREBASE_SERVICE_ACCOUNT`
3. Valore: apri il file `.json` scaricato al passo 6 con un editor di
   testo, copia **tutto** il contenuto e incollalo qui.
4. Salva.

## 9. Attiva GitHub Pages

1. **Settings → Pages**.
2. Source: "Deploy from a branch" → Branch: `main`, cartella: `/docs` → Save.
3. Dopo 1-2 minuti l'app è online, di solito a un indirizzo come:
   `https://TUO-UTENTE.github.io/agrimigra-watch/`

## 10. Lancia subito la prima scansione (senza aspettare 6 ore)

1. Sul repository: scheda **Actions** → workflow "Scansione bandi
   AgriMigra Watch" → **Run workflow** → Run workflow.
2. Aspetta un minuto o due, poi apri/ricarica il link dell'app: dovresti
   vedere i bandi comparire.

## 11. Installa l'app sul telefono (icona sulla home, come un'app vera)

- **Android (Chrome)**: apri il link → menu `⋮` in alto a destra →
  "Aggiungi a schermata Home".
- **iPhone/iPad (deve essere Safari, non Chrome)**: apri il link → icona
  di condivisione (quadrato con freccia in su) → "Aggiungi a Home".

## 12. Manda il link a tuo fratello

Gli basta il link di GitHub Pages: lo apre, lo installa sul telefono con
gli stessi passaggi del punto 11, e crea il proprio account con
"Registrati" (vedi punto 13) — da quel momento vede e gestisce solo le
proprie parole chiave e i propri bandi trovati, mai i tuoi. L'elenco
delle fonti monitorate resta invece condiviso tra tutti gli account.

## 13. Crea il tuo account (e, se serve, porta i vecchi dati)

Da questa versione l'app richiede un vero account personale (email +
password) invece del vecchio codice condiviso: ogni persona crea il
proprio, e da quel momento vede/gestisce solo i propri dati.

1. Su Firebase Console, nel menu a sinistra vai su **Build →
   Authentication** (se è la prima volta, clicca "Inizia").
2. Scheda **Sign-in method** → clicca **Email/Password** → attivalo
   (basta il primo interruttore) → Salva. È l'unico passo da fare qui su
   Firebase Console: l'account vero e proprio si crea direttamente
   nell'app, al passo successivo.
3. Apri il sito e, nel modulo in alto, usa la scheda **Registrati**:
   inserisci una tua email vera e una password di almeno 6 caratteri, poi
   premi **Crea account**.
4. Se avevi già parole chiave o bandi trovati dalla versione precedente
   (quella con il codice condiviso), portali nel tuo nuovo account con il
   workflow dedicato, da lanciare UNA SOLA VOLTA:
   1. Su Firebase Console → Authentication → scheda **Users**, trova la
      riga del tuo account appena creato e copia il valore nella colonna
      **User UID**.
   2. Sul repository GitHub: scheda **Actions** → workflow "Migra i dati
      verso il tuo account" → **Run workflow** → incolla l'uid copiato →
      Run workflow.
   3. Dopo qualche secondo le vecchie impostazioni/bandi condivisi sono
      spariti dalla cima del database e sono comparsi dentro il tuo
      account (le fonti monitorate non c'entrano: restano dove sono,
      condivise).

Ogni account resta valido finché non lo elimini da Firebase Console →
Authentication → Users. Ogni browser resta collegato finché non premi
"Esci" o cancelli i dati del sito, poi richiederà di nuovo email e
password.

## 14. Avvia scansione dall'app (senza andare su GitHub)

Il pulsante "Avvia scansione ora" (sotto le impostazioni ricerca) può
lanciare la scansione direttamente dall'app, senza passare dalla scheda
Actions di GitHub — utile se vuoi provare più argomenti di ricerca uno
dopo l'altro in pochi minuti.

**Nota**: una prima versione di questa funzione metteva il token di
GitHub direttamente nel codice del sito. GitHub lo ha rilevato appena
pubblicato e lo ha revocato automaticamente in pochi secondi (lo fa per
qualunque suo token trovato in un repository pubblico, anche se lo
autorizzi tu dal blocco "push protection") — quindi quell'approccio non
funziona proprio, non solo "è rischioso". Questa versione tiene invece
il token vero fuori dal repository, dentro un piccolo servizio esterno
gratuito ("Cloudflare Worker") che fa da intermediario.

1. Crea un account gratuito su **cloudflare.com** (non serve carta di
   credito per il piano Workers gratuito).
2. Nel pannello Cloudflare, vai su **Workers & Pages** → **Create** →
   **Create Worker**. Dai un nome (es. "grantscout-scan-trigger") →
   **Deploy** (per ora con il codice di esempio, lo sostituiamo subito).
3. Apri il Worker appena creato → **Edit code**, cancella tutto il
   contenuto e incolla il file `cloudflare-worker/scan-trigger.js` di
   questo repository → **Deploy**.
4. Torna alla pagina del Worker → **Settings** → **Variables and
   Secrets** → **Add**:
   - `GITHUB_TOKEN` (tipo **Secret**): un NUOVO fine-grained personal
     access token GitHub (**github.com/settings/tokens?type=beta** →
     Generate new token → Repository access: solo questo repository →
     Permissions → Actions → **Read and write**, tutto il resto su "No
     access"). Non lo metterai mai nel codice del sito, quindi qui può
     restare il vero token.
   - `SCAN_SHARED_SECRET` (tipo **Secret**): una password a piacere che
     userà solo l'app per "presentarsi" al Worker (non è un token
     GitHub, quindi anche se trapelasse il danno massimo è che qualcuno
     lanci scansioni a vuoto). Puoi usare questa, già generata per te:
     `579adebd930722194d3b0dc6fc079a0d18302481` — oppure inventane
     un'altra.
   Salva.
5. In cima alla pagina del Worker copia il suo indirizzo pubblico (una
   URL tipo `https://grantscout-scan-trigger.<tuo-nome>.workers.dev`).
6. Apri `docs/scan-config.js` nel progetto e incolla lì:
   - `workerUrl`: l'indirizzo copiato al punto 5;
   - `sharedSecret`: la stessa password messa in `SCAN_SHARED_SECRET`
     al punto 4.
7. Salva, fai commit e push.
8. Dopo aver effettuato l'accesso con il tuo account, premi "Avvia
   scansione ora": dopo qualche secondo dovresti vedere il messaggio di
   conferma, e dopo 1-3 minuti i nuovi risultati.

**Sulla sicurezza**: con questo schema il token GitHub vero non tocca
mai il repository né il codice del sito — resta solo dentro le
"Secrets" di Cloudflare, che non sono mai visibili pubblicamente. Nel
codice del sito finisce solo `sharedSecret`, una password a basso
rischio che il Worker controlla prima di accettare qualunque richiesta:
anche se qualcuno la trovasse, potrebbe solo lanciare scansioni a
vuoto — fastidioso ma innocuo, e comunque gratuito sui repository
pubblici. Se non configuri `docs/scan-config.js`, il pulsante ti avvisa
e resta comunque possibile lanciare la scansione a mano dalla scheda
Actions di GitHub, come prima.

---

## Limiti da conoscere (onestà prima di tutto)

- **Il pulsante "Avvia scansione ora"** funziona solo se hai configurato
  `docs/scan-config.js` e il Worker Cloudflare (punto 14): altrimenti ti
  avvisa e la scansione va lanciata a mano dalla scheda Actions di
  GitHub (come al punto 10) — un'operazione che richiede accesso a
  GitHub.
- **Anche con il pulsante configurato**, il risultato non è immediato: la
  scansione vera gira su GitHub Actions e di solito ci vogliono 1-3
  minuti prima che compaiano i nuovi risultati nell'app.
- **Le voci "Da verificare"** sono segnalazioni automatiche generate
  quando una fonte monitorata contiene una parola chiave cercata — non un
  riassunto intelligente. Vanno sempre controllate sulla fonte ufficiale
  prima di fidarsene. L'elenco delle fonti monitorate è condiviso tra
  tutti gli account: lo gestisci dal pannello "Impostazioni ricerca" →
  "Fonti monitorate", aggiungendo o togliendo una fonte alla volta (non
  esiste più un pulsante che svuota tutto insieme, perché toccherebbe
  anche le fonti usate dagli altri account).
- **L'integrazione Horizon Europe** usa un endpoint pubblico del portale
  Funding & Tenders che non è ufficialmente documentato dalla Commissione
  Europea: funziona nella maggior parte dei casi, ma se smette di
  rispondere lo script salta semplicemente quella fonte e continua con le
  altre, senza bloccarsi.
- **Se il repository resta inattivo 60 giorni** (nessun commit), GitHub
  disattiva da solo lo schedule automatico: basta riattivarlo dalla
  scheda Actions ("Enable workflow") quando serve di nuovo.
- **Costi**: con un uso normale resti sempre nei piani gratuiti — Firebase
  Spark (fino a 50.000 letture e 20.000 scritture al giorno, qui ne
  servono poche decine) e GitHub Actions (anche su repository privato,
  circa 240 minuti al mese usati su 2.000 gratuiti).
