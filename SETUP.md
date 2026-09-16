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

Queste regole dicono: chiunque abbia il link può leggere tutto; per
modificare le impostazioni (parole chiave, fonti, o svuotare i bandi
trovati) serve il codice di accesso spiegato al punto 13, quindi l'app
resta consultabile da chiunque ma solo chi conosce il codice può cambiare
qualcosa. Nessuno tranne lo scraper può scrivere i risultati dei bandi.

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
gli stessi passaggi del punto 11, e da lì può modificare parole chiave e
frequenza e guardare i bandi trovati — nessun account richiesto per usarla.

## 13. Proteggi le modifiche con un codice di accesso

Le regole di Firestore (`firestore.rules`) ora permettono a chiunque abbia
il link di leggere i bandi, ma per modificare qualcosa (parole chiave,
fonti monitorate, o il pulsante "Ricomincia da zero") serve un codice a 6
cifre che scegli tu. Chi ha solo il link può guardare i bandi ma non
toccare nulla; chi conosce anche il codice può modificare — senza dover
creare un account, inserire un'email o ricordare una password vera.

Tecnicamente il codice sblocca un accesso Firebase dedicato (Firebase
Authentication, email/password), ma questo resta invisibile a chi usa
l'app: vede solo un campo "Codice a 6 cifre" e un pulsante "Sblocca".

1. Su Firebase Console, nel menu a sinistra vai su **Build →
   Authentication** (se è la prima volta, clicca "Inizia").
2. Scheda **Sign-in method** → clicca **Email/Password** → attivalo
   (basta il primo interruttore) → Salva.
3. Scheda **Users** → **Aggiungi utente**.
4. Come email metti un indirizzo qualsiasi non tuo, ad esempio
   `accesso@grantscout-app.invalid` (non deve esistere davvero: serve solo
   come "nome utente" interno, non userà una vera casella email).
5. Come password scegli le **6 cifre** che vuoi usare come codice di
   accesso (es. `482913`) — Firebase richiede almeno 6 caratteri, quindi
   niente codici più corti.
6. Se hai usato un'email diversa da quella dell'esempio al passo 4, apri
   `docs/index.html`, cerca la riga `var LOGIN_EMAIL = ...` e sostituisci
   l'indirizzo con quello che hai usato (il codice invece non va scritto
   da nessuna parte nel codice: lo digiti tu, o chi condividi l'app, ogni
   volta che serve).
7. Torna alla scheda **Regole** di Firestore Database e incolla di nuovo
   il contenuto di `firestore.rules` di questo progetto (è cambiato:
   adesso richiede il codice per scrivere) → **Pubblica**.
8. Apri il sito, vai su "Impostazioni ricerca", inserisci il codice nel
   campo "Codice di accesso per modificare" e premi **Sblocca**: se tutto
   è a posto, il messaggio diventa "Sbloccato: puoi modificare" e puoi
   salvare le impostazioni normalmente.
9. Condividi il codice con chi vuoi che possa modificare (a voce, o in un
   messaggio privato) — non va mai scritto sul sito stesso o in un posto
   pubblico.

Il codice resta valido finché non lo cambi tu (rifacendo i passi 3-5 con
una password diversa, oppure eliminando e ricreando l'utente). Ogni
browser resta sbloccato finché non premi "Blocca" o cancelli i dati del
sito, poi richiederà di nuovo il codice.

---

## Limiti da conoscere (onestà prima di tutto)

- **Il pulsante "Aggiorna vista"** non avvia una nuova scansione: mostra
  gli ultimi dati già raccolti. Per forzare una scansione immediata va
  lanciata a mano dalla scheda Actions di GitHub (come al punto 10) — è
  un'operazione che serve fare a te, non a tuo fratello, perché richiede
  accesso a GitHub.
- **Le voci "Da verificare"** sono segnalazioni automatiche generate
  quando una fonte monitorata contiene una parola chiave cercata — non un
  riassunto intelligente. Vanno sempre controllate sulla fonte ufficiale
  prima di fidarsene. L'elenco delle fonti monitorate parte vuoto: le
  aggiungi tu dal pannello "Impostazioni ricerca" → "Fonti monitorate",
  e quando cambi completamente argomento di ricerca puoi usare il
  pulsante "Cambia argomento" per svuotare bandi e fonti insieme in un
  colpo solo.
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
