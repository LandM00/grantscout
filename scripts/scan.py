#!/usr/bin/env python3
"""
Scraper periodico per GrantScout.

Gira su GitHub Actions secondo lo schedule in .github/workflows/scan.yml
(di default ogni 6 ore). App MULTI-UTENTE: ogni persona registrata ha le
proprie parole chiave, la propria frequenza e i propri bandi trovati,
sotto users/{uid}/... in Firestore; l'elenco delle pagine istituzionali
da controllare (collection "sources") è invece CONDIVISO tra tutti.
Ad ogni esecuzione:

  1. Legge l'elenco delle fonti condivise (sources) e SCARICA ciascuna
     pagina UNA SOLA VOLTA per questo giro (fetch_watch_pages),
     indipendentemente da quante persone la stiano monitorando — non ha
     senso richiedere la stessa pagina più volte allo stesso sito esterno
     solo perché più utenti la seguono con parole chiave diverse.
  2. Legge l'elenco degli utenti registrati (collection "users").
  3. Per ciascun utente, separatamente: decide se è il momento di
     scansionare (confrontando il tempo trascorso con la frequenza scelta
     da LUI), interroga l'API del portale Funding & Tenders (Horizon
     Europe) con le SUE parole chiave, confronta le pagine condivise già
     scaricate al passo 1 con le SUE parole chiave, e scrive i risultati
     SOLO nel suo spazio privato (users/{uid}/calls). Un errore per un
     utente non blocca la scansione degli altri.

Non usa nessun modello linguistico: è ricerca per parola chiave e
rilevamento di cambiamenti di pagina, non un giudizio "intelligente" di
rilevanza. Le voci di tipo "watch" vanno sempre verificate a mano.

L'elenco delle pagine istituzionali da controllare NON è fisso nel
codice: vive nella collection Firestore "sources" (ognuna: url, funder,
category, title) e parte vuoto — si aggiunge/toglie/modifica fonti
direttamente dall'app (pannello "Impostazioni ricerca" → "Fonti
monitorate"), senza toccare il codice, e chiunque abbia un account può
farlo (è un catalogo comune, non di proprietà di un singolo utente). Una
fonte produce una segnalazione SOLO se il suo testo contiene una parola
chiave cercata: non basta che la pagina sia semplicemente "cambiata".

Nota: questo script usava anche la Custom Search JSON API di Google per
una ricerca generica sul web, rimossa a settembre 2026 perché Google ha
chiuso quell'API ai nuovi clienti (resta disponibile solo a chi la usava
già prima, fino al 1 gennaio 2027). Al suo posto si aggiungono le fonti
di interesse una per una nella collection "sources", con lo stesso
meccanismo di controllo pagine usato per le fonti istituzionali.
"""

import hashlib
import json
import os
import re
import sys
from collections import deque
from datetime import datetime, timezone
from urllib.parse import urljoin

import requests

try:
    import firebase_admin
    from firebase_admin import credentials, firestore
except ImportError:
    print("Manca firebase-admin. Esegui: pip install -r scripts/requirements.txt")
    sys.exit(1)

try:
    from bs4 import BeautifulSoup
except ImportError:
    BeautifulSoup = None


FREQ_DAYS = {"weekly": 7, "biweekly": 14, "monthly": 30}
DEFAULT_FREQUENCY = "biweekly"
HTTP_HEADERS = {
    # Un User-Agent "da browser" riduce i falsi blocchi (403) da parte di
    # siti con protezioni anti-bot basiche: molti bloccano di default gli
    # User-Agent che si dichiarano script/bot, anche per un uso legittimo
    # come questo (monitoraggio privato, non commerciale, a bassa frequenza).
    # Le intestazioni aggiuntive sotto (Accept, Sec-Fetch-*, ecc.) servono
    # allo stesso scopo: alcune protezioni anti-bot (es. Cloudflare) non
    # guardano solo lo User-Agent ma l'insieme delle intestazioni tipiche
    # di una richiesta di navigazione vera -- un browser reale le invia
    # sempre, uno script che manda solo Host/User-Agent si nota. Non e'
    # una soluzione garantita (contro una verifica che richiede di
    # eseguire JavaScript non puo' funzionare, con "requests" non
    # eseguiamo pagine), ma per blocchi basati solo sulle intestazioni
    # puo' bastare.
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept-Language": "it-IT,it;q=0.9,en;q=0.8",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Encoding": "gzip, deflate, br",
    "Connection": "keep-alive",
    "Upgrade-Insecure-Requests": "1",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Sec-Fetch-User": "?1",
    "Cache-Control": "max-age=0",
}
HTTP_TIMEOUT = 25

# Servizio di terze parti (gratuito) usato come RIPIEGO quando una pagina
# monitorata rifiuta la richiesta diretta da GitHub Actions. Scarica la
# pagina con un proprio browser headless, quindi con un proprio indirizzo
# IP (diverso da quello di GitHub Actions), e restituisce il contenuto
# già ripulito in testo/Markdown.
#
# Verificato a mano (settembre 2026):
#   - interregeurope.eu: la richiesta diretta da 403 (blocco anti-bot),
#     ma passando da questo servizio il contenuto si scarica bene. Il
#     fallback qui sotto risolve quindi questo caso.
#   - regione.puglia.it: la richiesta diretta va in timeout (ConnectTimeout)
#     da GitHub Actions. Anche passando da questo servizio, pero, la
#     richiesta va comunque in timeout -- persino su un file statico senza
#     JavaScript come /robots.txt. Questo indica che il blocco non e
#     "anti-bot" contro uno script che si dichiara tale, ma un blocco piu
#     ampio, a livello di rete, contro gli indirizzi IP dei provider cloud
#     in generale (comune per molti siti della pubblica amministrazione
#     italiana): sia GitHub Actions sia questo servizio girano su
#     infrastrutture cloud, quindi finiscono entrambi bloccati allo stesso
#     modo. Per questo caso non risulta esistere un modo gratuito di
#     aggirare il blocco: servirebbe un proxy con indirizzo IP
#     residenziale, che e un servizio a pagamento.
JINA_READER_PREFIX = "https://r.jina.ai/"
JINA_TIMEOUT = 45  # piu lento della richiesta diretta: usa un browser headless vero


def now_iso():
    return datetime.now(timezone.utc).isoformat()


def init_firestore():
    raw = os.environ.get("FIREBASE_SERVICE_ACCOUNT")
    if not raw:
        print("ERRORE: variabile d'ambiente FIREBASE_SERVICE_ACCOUNT mancante.")
        sys.exit(1)
    cred_dict = json.loads(raw)
    cred = credentials.Certificate(cred_dict)
    firebase_admin.initialize_app(cred)
    return firestore.client()


def get_doc(doc_ref, default=None):
    """Legge un documento Firestore a partire dal suo riferimento già
    pronto (doc_ref), con un valore di ripiego se non esiste. Prima
    costruiva sempre il riferimento a partire dalla radice (db, nome
    collection, id): ora che le impostazioni/lo stato sono per-utente
    (sotto users/{uid}/...), passare direttamente il riferimento è più
    comodo — funziona comunque anche per un documento condiviso a
    livello radice (es. meta/status), basta passargli quel riferimento."""
    snap = doc_ref.get()
    return snap.to_dict() if snap.exists else (default or {})


def list_registered_users(db):
    """Elenco degli utenti registrati: un piccolo documento users/{uid}
    (email, data di registrazione) creato dal client al momento della
    registrazione (vedi docs/index.html) — usato per sapere per chi far
    girare la scansione. Un utente che si è solo autenticato ma il cui
    documento non esiste per qualche motivo (es. scrittura fallita al
    momento della registrazione) verrebbe semplicemente ignorato qui:
    non è un problema di sicurezza (i suoi dati restano privati, le
    regole di Firestore lo proteggono comunque), ma un caso limite da
    tenere a mente se un utente segnala di non vedere mai risultati."""
    users = []
    for doc in db.collection("users").stream():
        data = doc.to_dict() or {}
        users.append({"uid": doc.id, "email": data.get("email", "")})
    return users


def should_run(config, meta):
    frequency = config.get("frequency", DEFAULT_FREQUENCY)
    threshold_days = FREQ_DAYS.get(frequency, FREQ_DAYS[DEFAULT_FREQUENCY])
    last_run = meta.get("lastRun")
    if not last_run:
        return True, "prima esecuzione"
    try:
        last_dt = datetime.fromisoformat(last_run.replace("Z", "+00:00"))
    except ValueError:
        return True, "lastRun illeggibile"
    elapsed_days = (datetime.now(timezone.utc) - last_dt).total_seconds() / 86400
    if elapsed_days >= threshold_days - 1:
        return True, "soglia raggiunta ({:.1f}/{} giorni)".format(elapsed_days, threshold_days)
    return False, "non ancora ({:.1f}/{} giorni)".format(elapsed_days, threshold_days)


def load_sources(db):
    docs = db.collection("sources").stream()
    sources = []
    for doc in docs:
        data = doc.to_dict() or {}
        data["id"] = doc.id
        if data.get("url"):
            sources.append(data)
    return sources


def _delete_all(db, collection_ref):
    """Elimina tutti i documenti di una collection (identificata dal suo
    riferimento già pronto, non più dal nome a partire dalla radice: ora
    che i bandi vivono sotto users/{uid}/calls, serve poter puntare a
    una sotto-collection), a lotti di 400 (limite di Firestore per
    batch). Restituisce quanti ne ha eliminati."""
    docs = list(collection_ref.stream())
    batch = db.batch()
    for i, doc in enumerate(docs):
        batch.delete(doc.reference)
        if (i + 1) % 400 == 0:
            batch.commit()
            batch = db.batch()
    batch.commit()
    return len(docs)


def check_and_apply_reset(db, user_ref):
    """Il pulsante 'Ricomincia da zero' nell'app scrive
    users/{uid}/admin/reset con requested=true: qui lo leggiamo e
    svuotiamo SOLO i bandi di QUESTO utente (users/{uid}/calls).

    A differenza della versione a singolo utente, qui NON esiste più
    un'opzione per svuotare anche le fonti monitorate insieme al reset:
    le fonti sono condivise fra tutti gli utenti (vedi firestore.rules),
    quindi il "cambio argomento" di una persona non può più cancellare
    l'elenco comune per tutti gli altri. Per togliere una fonte condivisa
    si usa il pulsante "Rimuovi" dedicato in "Fonti monitorate", uno per
    uno, come qualsiasi altra modifica all'elenco condiviso."""
    reset_ref = user_ref.collection("admin").document("reset")
    snap = reset_ref.get()
    if not snap.exists:
        return False
    data = snap.to_dict() or {}
    if not data.get("requested"):
        return False
    n_calls = _delete_all(db, user_ref.collection("calls"))
    reset_ref.set({
        "requested": False,
        "lastResetAt": now_iso(),
    })
    print("[{}] Reset richiesto dall'utente: eliminati {} bandi.".format(user_ref.id, n_calls))
    return True


def slugify(text):
    text = re.sub(r"[^a-z0-9]+", "-", str(text).lower()).strip("-")
    return text[:80] or "voce"


def search_funding_tenders_portal(keywords):
    """Interroga l'API pubblica (non ufficialmente documentata) del portale
    EU Funding & Tenders per Horizon Europe. Se l'endpoint cambia o non
    risponde come atteso, la funzione NON deve mai bloccare il resto dello
    scan — ma a differenza di prima, non si limita più a stampare un
    avviso nel log: restituisce anche un riepilogo (`stats`) di quante
    parole chiave sono state cercate con successo e quante hanno fallito,
    così chi chiama può accorgersi (e mostrare nell'app) quando l'intera
    fonte è irraggiungibile, invece che confondere un guasto con "nessun
    risultato nuovo".

    Cerca UNA parola chiave alla volta (non tutte insieme come frase unica):
    unirle in un'unica frase tra virgolette richiederebbe che un bando
    contenga letteralmente tutte quelle parole in quell'ordine esatto, cosa
    che in pratica non succede mai — il risultato sarebbe sempre zero anche
    quando ci sono bandi pertinenti. Cercandole una per volta e unendo i
    risultati (senza doppioni), basta che una call contenga anche solo una
    delle parole chiave per comparire.

    Un bando trovato tramite PIÙ parole chiave diverse (es. sia
    "migrazione" che "agricoltura") è un segnale più forte di uno trovato
    tramite una sola parola generica: per questo non ci fermiamo alla
    prima parola chiave che lo trova (comportamento precedente — la
    seconda occorrenza dello stesso bando veniva scartata come doppione
    senza lasciare traccia), ma accumuliamo TUTTE le parole chiave che
    hanno prodotto ciascun bando, in `matched_terms_by_id`, e la usiamo
    sia per il testo del riepilogo sia per il punteggio di rilevanza
    (vedi compute_match_score).

    Il motore di ricerca del portale UE cerca la parola chiave in tutto
    il testo indicizzato del bando (anche descrizione lunga, allegati,
    ecc.), non solo nel titolo: capita quindi che compaia un bando il
    cui titolo non ha niente a che vedere con la ricerca fatta, solo
    perché la parola compare una volta in un paragrafo secondario. Per
    questo, PRIMA di includere un bando nei risultati, controlliamo che
    almeno una delle parole chiave che l'hanno trovato compaia anche nel
    suo titolo (stesso confronto tollerante di keyword_matches_text): se
    nessuna lo fa, il bando viene scartato come probabilmente non
    pertinente."""
    items_by_id = {}
    matched_terms_by_id = {}
    errors = []
    url = "https://api.tech.ec.europa.eu/search-api/prod/rest/search"
    terms = [k for k in (keywords or []) if k][:6] or ["migration", "agriculture"]
    body = {
        "query": {
            "bool": {
                "must": [
                    {"terms": {"type": ["1"]}},  # 1 = call for proposals
                    {"terms": {"status": ["31094502", "31094501"]}},  # forthcoming, open
                ]
            }
        }
    }
    # Limite di sicurezza sul numero di pagine richieste all'API per ogni
    # parola chiave. Prima veniva sempre e solo richiesta la prima pagina
    # (pageNumber fisso a 1): un bando pertinente presente solo "più in
    # là" (es. il 16-esimo risultato per quella parola, con pagine da 15)
    # non veniva mai visto. Ora si richiedono altre pagine finché quella
    # appena ricevuta è piena (segno che potrebbe essercene un'altra),
    # fino a HORIZON_MAX_PAGES pagine per parola chiave: un limite tenuto
    # basso apposta per non appesantire troppo ogni scansione, dato che
    # in pratica risultati pertinenti oltre la 1a-2a pagina sono rari.
    HORIZON_PAGE_SIZE = 15
    HORIZON_MAX_PAGES = 4
    for term in terms:
        page_number = 1
        while page_number <= HORIZON_MAX_PAGES:
            params = {"apiKey": "SEDIA", "text": '"{}"'.format(term), "pageSize": HORIZON_PAGE_SIZE, "pageNumber": page_number}
            try:
                resp = requests.post(url, params=params, json=body, headers=HTTP_HEADERS, timeout=HTTP_TIMEOUT)
                resp.raise_for_status()
                data = resp.json()
                hits = (data.get("results") or data.get("hits") or [])
                for hit in hits:
                    # L'API mescola, sotto gli stessi risultati e nonostante il
                    # filtro "type"/"status" sopra, anche progetti UE GIA
                    # FINANZIATI (in corso o conclusi) la cui descrizione contiene
                    # semplicemente la parola chiave -- non bandi a cui ci si puo
                    # ancora candidare. Verificato a mano: per query tipiche circa
                    # 7 risultati su 10 erano di questo tipo, non bandi veri.
                    # Si distinguono in modo affidabile dal campo "database": solo
                    # i temi/bandi veri del portale hanno database == "SEDIA"; un
                    # progetto gia finanziato ha questo campo assente. Li
                    # scartiamo qui, altrimenti la maggior parte delle
                    # segnalazioni sarebbe rumore su iniziative non piu aperte.
                    if hit.get("database") != "SEDIA":
                        continue
                    fields = hit.get("metadata", hit)
                    title = _first(fields, ["title", "callTitle"]) or "Bando Horizon Europe"
                    identifier = _first(fields, ["identifier", "callIdentifier", "reference"])
                    deadline_date = _parse_date(_first(fields, ["deadlineDate", "deadline"]))
                    item_id = "horizon-" + slugify(identifier or title)
                    found_terms = matched_terms_by_id.setdefault(item_id, [])
                    if term not in found_terms:
                        found_terms.append(term)
                    if item_id in items_by_id:
                        continue  # titolo/scadenza/etc. già salvati: qui serviva solo registrare il termine
                    items_by_id[item_id] = {
                        "id": item_id,
                        "title": title if not identifier else "{} ({})".format(title, identifier),
                        "funder": "Commissione Europea — Horizon Europe / Funding & Tenders Portal",
                        "category": "funding",
                        # Il filtro "stato" dell'API (non ufficiale) non è affidabile:
                        # a volte restituisce anche call scadute da anni etichettate
                        # come aperte. Calcoliamo lo stato noi, dalla scadenza vera.
                        "status": status_from_deadline(deadline_date),
                        "deadlineDate": deadline_date,
                        "tags": ["UE", "Horizon Europe"],
                        # ATTENZIONE: lo schema "calls-for-proposals?callIdentifier=..."
                        # usato qui in precedenza NON porta piu da nessuna parte -- il
                        # portale (verificato a mano) lo ignora e mostra sempre e solo
                        # la home page vuota, qualunque identificativo gli si passi.
                        # Lo schema corretto, verificato sia su bandi Horizon Europe
                        # aperti che su bandi H2020 chiusi da anni, e
                        # ".../topic-details/<identifier>". Il campo "url" che l'API
                        # stessa a volte restituisce non e affidabile allo stesso modo:
                        # per i bandi piu vecchi punta a un endpoint JSON grezzo (non a
                        # una pagina leggibile), quindi costruiamo il link noi.
                        "url": (
                            "https://ec.europa.eu/info/funding-tenders/opportunities/portal/screen/opportunities/topic-details/" + identifier
                            if identifier else
                            "https://ec.europa.eu/info/funding-tenders/opportunities/portal/screen/home"
                        ),
                        "source": "funding-tenders-api",
                    }
            except Exception as exc:  # noqa: BLE001 — vogliamo continuare comunque
                print("Avviso: ricerca su Funding & Tenders Portal per \"{}\" non riuscita ({}). Salto questo termine (pagina {}).".format(term, exc, page_number))
                errors.append({"term": term, "error": str(exc)})
                break
            if len(hits) < HORIZON_PAGE_SIZE:
                break  # pagina non piena: non ce ne sono altre da chiedere
            page_number += 1

    # Solo ora, con TUTTE le parole chiave di ciascun bando raccolte,
    # calcoliamo punteggio ed etichetta di rilevanza e componiamo il
    # riepilogo finale (elenca tutte le parole chiave trovate, non solo
    # la prima incontrata).
    results = []
    for item_id, item in items_by_id.items():
        found_terms = matched_terms_by_id.get(item_id, [])
        title_tokens = _tokenize(item["title"].lower())
        if not any(keyword_matches_text(t, title_tokens) for t in found_terms):
            continue  # nessuna parola chiave nel titolo del bando: probabilmente non pertinente
        if item.get("status") == "closed":
            # Verificato a mano (settembre 2026): il filtro "stato" dell'API
            # (vedi commento sopra) non e' affidabile fino in fondo -- puo'
            # restituire un bando con scadenza passata da quasi due anni pur
            # avendogli chiesto esplicitamente solo bandi aperti/in arrivo.
            # Lo stato "closed" qui e' calcolato da noi dalla vera data di
            # scadenza (status_from_deadline), quindi e' affidabile: se dice
            # chiuso, e' chiuso per davvero. Non ha senso mostrarlo: non ci si
            # puo' piu' candidare, quindi e' solo rumore nella lista.
            continue
        match_score = compute_match_score(found_terms)
        relevance_tag = relevance_label(match_score)
        if relevance_tag:
            item["tags"] = item["tags"] + [relevance_tag]
        item["matchScore"] = match_score
        if len(found_terms) == 1:
            item["summary"] = (
                "Trovato tramite ricerca automatica per la parola chiave \"{}\" sul portale "
                "Funding & Tenders. Verificare rilevanza e requisiti sulla pagina ufficiale."
            ).format(found_terms[0])
        else:
            quoted = ", ".join('"{}"'.format(t) for t in found_terms)
            item["summary"] = (
                "Trovato tramite ricerca automatica per {} parole chiave ({}) sul portale "
                "Funding & Tenders. Verificare rilevanza e requisiti sulla pagina ufficiale."
            ).format(len(found_terms), quoted)
        results.append(item)

    stats = {
        "termsTotal": len(terms),
        "termsFailed": len(errors),
        "lastErrorSample": errors[0]["error"] if errors else None,
    }
    print("Portale Funding & Tenders: {} risultati unici su {} parole chiave cercate ({} fallite).".format(
        len(results), len(terms), len(errors)))
    return results, stats


def _first(d, keys):
    """Come dict.get, ma prova più chiavi in ordine e gestisce il caso in
    cui il portale UE restituisca il valore come lista (es. titolo in più
    lingue) invece che come testo semplice: in quel caso prende il primo
    elemento non vuoto della lista."""
    for k in keys:
        v = d.get(k)
        if isinstance(v, list):
            v = next((x for x in v if x), None)
        if v:
            return v
    return None


def _parse_date(value):
    if not value:
        return None
    m = re.search(r"(\d{4}-\d{2}-\d{2})", str(value))
    return m.group(1) if m else None


def status_from_deadline(deadline_date):
    """Se non c'è una scadenza, meglio segnare 'da verificare' piuttosto
    che dare per aperta una call senza prove."""
    if not deadline_date:
        return "watch"
    today = datetime.now(timezone.utc).date().isoformat()
    return "closed" if deadline_date < today else "open"


def _normalize_kw(text):
    # Confronto "morbido": ignora maiuscole/minuscole, spazi e punteggiatura
    # finale — usato sia per il controllo doppioni sia per riconoscere una
    # "traduzione" che è in realtà il testo di partenza quasi identico
    # (tipico di un tentativo nella direzione sbagliata, es. "en|it" su un
    # testo già in italiano).
    return re.sub(r"[\s.!?]+$", "", text.strip().lower())


# MyMemory è una translation MEMORY (cerca frasi già tradotte da altri in un
# archivio di documenti reali, soprattutto testi UE), non un motore di
# traduzione automatica generalista: per una parola isolata comune di solito
# trova un match affidabile, ma per una combinazione di più parole meno
# comune può non avere nulla in archivio. In quel caso — o quando la quota
# gratuita giornaliera (condivisa fra tutti gli utenti di GitHub Actions nel
# mondo) è esaurita — l'API risponde comunque con HTTP 200, ma il testo
# restituito non è una vera traduzione: può essere il testo originale quasi
# invariato, una traduzione di bassissima qualità, o il messaggio
# "MYMEMORY WARNING: YOU USED ALL AVAILABLE FREE TRANSLATIONS FOR TODAY..."
# spacciato per traduzione. L'API fornisce un punteggio di affidabilità
# ("match", da 0 a 1) che permette di riconoscere questi casi — il codice
# precedente lo ignorava completamente e accettava alla cieca qualsiasi
# risposta, che è la causa esatta per cui frasi come "agricoltura
# sostenibile" risultavano non tradotte senza nessun errore visibile in log.
# La soglia sotto non è documentata dall'API: è una scelta prudente basata
# sull'uso comune di questo servizio (sotto 0.5 la qualità è tipicamente
# inaffidabile).
TRANSLATION_MATCH_THRESHOLD = 0.5

# Piccolo dizionario di riserva per frasi del dominio di questo progetto
# (migrazione e lavoro agricolo, vedi README) che MyMemory tipicamente NON
# ha in archivio con un punteggio affidabile — verificato a mano: "agricoltura
# sostenibile" è uno di questi casi (vedi commento sopra). Usato SOLO come
# ultimo ripiego, quando il tentativo con il servizio online non produce
# nulla di utilizzabile: non sostituisce MyMemory, lo integra per i termini
# che contano di più per questo progetto specifico. Se in futuro riorienti
# l'app su un altro argomento (il README lo prevede esplicitamente), aggiungi
# qui le coppie utili al tuo nuovo tema — non serve toccare altro codice.
DOMAIN_TRANSLATION_PAIRS = [
    ("agricoltura sostenibile", "sustainable agriculture"),
    ("lavoro agricolo", "agricultural labour"),
    ("lavoro migrante", "migrant labour"),
    ("caporalato", "gangmaster system"),
    ("sfruttamento lavorativo", "labour exploitation"),
    ("filiera agroalimentare", "agri-food supply chain"),
    ("sicurezza alimentare", "food security"),
    ("inclusione sociale", "social inclusion"),
    ("integrazione dei migranti", "migrant integration"),
    ("manodopera agricola", "agricultural workforce"),
    ("diritti dei lavoratori", "workers' rights"),
    ("economia rurale", "rural economy"),
    ("sviluppo rurale", "rural development"),
    ("migrazione economica", "economic migration"),
    ("lavoratori stagionali", "seasonal workers"),
    ("politiche migratorie", "migration policy"),
    ("agricoltura sociale", "social farming"),
]


def _build_domain_translation_lookup(pairs):
    lookup = {}
    for it_text, en_text in pairs:
        lookup[_normalize_kw(it_text)] = en_text
        lookup[_normalize_kw(en_text)] = it_text
    return lookup


# Dizionario vero e proprio usato in fase di ricerca: {frase normalizzata:
# sua traduzione}, costruito una volta sola dalle coppie sopra, in entrambe
# le direzioni (così funziona sia se scrivi la parola chiave in italiano
# che in inglese).
DOMAIN_TRANSLATIONS = _build_domain_translation_lookup(DOMAIN_TRANSLATION_PAIRS)


def _is_usable_translation(original, translated, match_value):
    if not translated:
        return False
    if "MYMEMORY WARNING" in translated.upper():
        return False
    try:
        match_score = float(match_value)
    except (TypeError, ValueError):
        match_score = 0.0
    if match_score < TRANSLATION_MATCH_THRESHOLD:
        return False
    if _normalize_kw(translated) == _normalize_kw(original):
        return False
    return True


def expand_keywords_with_translation(keywords):
    """Aggiunge automaticamente una traduzione italiano<->inglese di ogni
    parola chiave (frase intera, non parola per parola), per ampliare la
    ricerca senza doverle scrivere a mano in entrambe le lingue. Usa
    MyMemory (mymemory.translated.net), un servizio di traduzione gratuito
    con un limite di utilizzo giornaliero condiviso per indirizzo IP e,
    soprattutto, una copertura non garantita per frasi meno comuni (vedi
    commento su TRANSLATION_MATCH_THRESHOLD sopra). Ogni traduzione viene
    validata con il punteggio di affidabilità dell'API prima di essere
    usata; se fallisce (errore di rete, quota esaurita, o traduzione di
    bassa qualità), quella parola chiave resta semplicemente non tradotta —
    non blocca mai il resto della scansione.

    Restituisce (elenco_ampliato, traduzioni):
    - elenco_ampliato: le parole originali più tutte le traduzioni valide
      trovate, senza limiti — usato per il confronto sulle pagine
      monitorate (check_watch_pages), che non ha un tetto a quante parole
      usare e può trovarsi davanti pagine sia in italiano che in inglese.
    - traduzioni: un dizionario {parola originale: sua traduzione migliore},
      usato da main() per costruire la ricerca su Horizon Europe — una
      fonte in lingua inglese con un budget limitato di richieste (una per
      parola chiave), per cui conviene usare la versione inglese quando
      disponibile invece di spendere due richieste sulla stessa idea."""
    if not keywords:
        return keywords, {}

    expanded = list(keywords)
    seen_norm = {_normalize_kw(k) for k in expanded}
    translations = {}
    attempts = 0
    failures = 0
    discarded_low_quality = 0
    from_domain_dict = 0
    for kw in keywords[:8]:  # limite prudente per non consumare troppa quota gratuita
        best_text, best_score = None, -1.0
        for langpair in ("it|en", "en|it"):
            attempts += 1
            try:
                resp = requests.get(
                    "https://api.mymemory.translated.net/get",
                    params={"q": kw, "langpair": langpair},
                    timeout=10,
                )
                resp.raise_for_status()
                data = resp.json()
                response_data = data.get("responseData") or {}
                translated = response_data.get("translatedText")
                match_value = response_data.get("match")
                if not _is_usable_translation(kw, translated, match_value):
                    if translated:
                        discarded_low_quality += 1
                    continue
                try:
                    score = float(match_value)
                except (TypeError, ValueError):
                    score = 0.0
                if score > best_score:
                    best_text, best_score = translated, score
            except Exception:
                failures += 1
                continue

        if best_text and _normalize_kw(best_text) in seen_norm:
            # Il servizio online ha restituito una traduzione "valida" ma
            # identica a quella GIA usata per un'altra parola chiave (es.
            # MyMemory puo rispondere "agriculture" sia per "agricoltura"
            # che per "agricoltura sostenibile", non avendo un match
            # specifico per la frase intera) — verificato accadere
            # davvero in produzione. Non e una traduzione specifica per
            # QUESTA parola chiave: la trattiamo come se il servizio non
            # avesse prodotto nulla di utile, invece di scartarla in
            # silenzio senza mai provare il dizionario di riserva.
            best_text = None

        used_domain_dict = False
        if not best_text:
            # Il servizio online non ha prodotto nulla di utilizzabile per
            # questa parola chiave: proviamo il dizionario di riserva del
            # dominio prima di rinunciare del tutto alla traduzione.
            fallback = DOMAIN_TRANSLATIONS.get(_normalize_kw(kw))
            if fallback:
                best_text = fallback
                used_domain_dict = True

        if best_text and _normalize_kw(best_text) not in seen_norm:
            expanded.append(best_text)
            seen_norm.add(_normalize_kw(best_text))
            translations[kw] = best_text
            if used_domain_dict:
                from_domain_dict += 1

    print(
        "Traduzione automatica parole chiave: {} nuove parole aggiunte "
        "({} dal dizionario di riserva del dominio), "
        "{} scartate (bassa affidabilità, quota esaurita, o identiche al testo di partenza), "
        "{} tentativi falliti su {}.".format(
            len(expanded) - len(keywords), from_domain_dict, discarded_low_quality, failures, attempts)
    )
    return expanded, translations


def build_horizon_terms(keywords, translations, limit=6):
    """Costruisce l'elenco di termini da cercare su Horizon Europe entro il
    budget di richieste disponibile (una per termine, vedi
    search_funding_tenders_portal). A differenza di prima — appendere e
    basta le traduzioni in coda, che le tagliava fuori non appena c'erano
    6 o più parole chiave originali (proprio i casi in cui contano di
    più) — prima passata: un termine per concetto, preferendo la
    traduzione inglese quando disponibile e affidabile, così ogni concetto
    arriva in ricerca almeno una volta anche con molte parole chiave;
    seconda passata: se restano posti liberi nel budget (poche parole
    chiave), aggiunge anche la versione nell'altra lingua degli stessi
    concetti, per non sprecare richieste inutilizzate."""
    terms = []
    seen = set()

    def add(term):
        if term and term not in seen and len(terms) < limit:
            terms.append(term)
            seen.add(term)

    for kw in keywords:
        add(translations.get(kw, kw))
    for kw in keywords:
        if kw in translations:
            add(kw)
    return terms


# Sequenze di lettere (Unicode: gestisce anche accenti come "à") usate per
# scomporre sia le pagine monitorate che le parole chiave in singole parole,
# ignorando numeri e punteggiatura.
_WORD_RE = re.compile(r"[^\W\d_]+", re.UNICODE)

# Sotto questa lunghezza una parola NON viene ridotta: parole corte hanno
# poche lettere di "riserva" da tagliare senza rischiare di confonderla con
# una parola diversa (es. non vogliamo ridurre "casa" o "vita").
_MIN_LEN_FOR_STEM = 5


# Suffissi italiani che formano un sostantivo a partire da un verbo (es.
# "irrigare" -> "irrigazione", "investire" -> "investimento"): se una
# parola chiave finisce con uno di questi, la radice usata per il
# confronto e' la parola SENZA il suffisso, cosi si riconoscono anche le
# altre forme della stessa famiglia (aggettivo, participio, plurale) che
# il solo taglio di una vocale finale non prendeva -- verificato un caso
# reale: cercando "irrigazione" non veniva trovato un bando che parlava
# solo di "investimenti irrigui"/"superficie irrigata", stessa famiglia
# di parole ma radice diversa con il vecchio confronto.
#
# _MIN_STEM_RESULT_LEN evita di applicare il taglio quando la radice
# risultante sarebbe troppo corta e quindi rischiosa (es. "nazione" -
# "azione" darebbe solo "n", che troverebbe quasi qualsiasi parola):
# in quel caso si torna al taglio della sola vocale finale, come prima.
_DERIVATIONAL_SUFFIXES = ("azione", "izione", "amento", "imento")
_MIN_STEM_RESULT_LEN = 5


def _stem(word):
    """Approssima la radice di una parola per riconoscere le variazioni piu
    comuni di plurale/genere, invece di richiedere un confronto letterale
    identico (il comportamento precedente: "migrante" non trovava
    "migranti", "agricola" non trovava "agricole"). Toglie l'ultima
    lettera se e una vocale (variazioni italiane piu comuni: -a/-e/-i/-o)
    o una "s" finale (plurale inglese: migrant/migrants) — oppure, per le
    parole che finiscono con uno dei suffissi in _DERIVATIONAL_SUFFIXES,
    toglie l'intero suffisso (vedi commento sopra).

    Non e un vero stemmer linguistico (userebbe librerie NLP che
    appesantirebbero un progetto pensato per restare semplice e gratuito):
    e deliberatamente permissivo, perche ogni segnalazione di questa app
    va comunque verificata a mano — un falso positivo in piu costa una
    verifica in piu, un falso negativo fa perdere un'opportunita reale
    senza che l'utente se ne accorga mai. ATTENZIONE: e' comunque un
    compromesso, non un confronto "intelligente" — resta possibile che
    due parole con la stessa radice approssimata siano in realta
    concetti diversi (es. "nazione"/"condizione" sono protette apposta
    dalla soglia _MIN_STEM_RESULT_LEN, ma casi simili non previsti qui
    potrebbero non esserlo)."""
    for suffix in _DERIVATIONAL_SUFFIXES:
        if word.endswith(suffix) and len(word) - len(suffix) >= _MIN_STEM_RESULT_LEN:
            return word[: -len(suffix)]
    if len(word) > _MIN_LEN_FOR_STEM and word[-1] in "aeious":
        return word[:-1]
    return word


def _tokenize(text_lower):
    return _WORD_RE.findall(text_lower)


def keyword_matches_text(keyword, tokens, window=6):
    """Confronto tollerante tra una parola chiave (anche di piu parole,
    es. "lavoro agricolo") e il testo gia scomposto in parole (`tokens`,
    vedi _tokenize — passato gia pronto perche lo stesso testo viene
    confrontato con piu parole chiave, non ha senso ri-scomporlo ogni
    volta). Per una keyword di una sola parola: basta che una parola del
    testo INIZI con la sua radice approssimata (vedi _stem) — cosi
    "migrante" trova sia "migrante" che "migranti" nel testo, ma non
    "migratorio" (radice diversa: "migrant" contro "migrator"). Per una
    keyword di piu parole: non richiede piu che siano una sequenza esatta
    adiacente (il comportamento precedente) — basta che le radici di
    tutte le parole della keyword compaiano nel testo entro una finestra
    di poche parole (`window`) l'una dall'altra, in un ordine qualsiasi.
    Cosi "lavoro agricolo" trova anche "lavoro nel settore agricolo"."""
    words = _WORD_RE.findall(keyword.lower())
    if not words:
        return False

    positions_per_word = []
    for word in words:
        stem = _stem(word)
        positions = [i for i, tok in enumerate(tokens) if tok.startswith(stem)]
        if not positions:
            return False  # una parola della keyword non compare per nulla: nessun match
        positions_per_word.append(positions)

    if len(positions_per_word) == 1:
        return True

    # Finestra scorrevole sulla lista di TUTTE le posizioni trovate (di
    # qualsiasi parola della keyword), ordinate: appena la finestra
    # contiene almeno una posizione per ciascuna parola, c'e un match.
    # Evita l'esplosione combinatoria di provare tutte le combinazioni
    # possibili una per una.
    events = sorted(
        (pos, word_idx)
        for word_idx, positions in enumerate(positions_per_word)
        for pos in positions
    )
    needed = len(positions_per_word)
    counts = [0] * needed
    distinct = 0
    window_events = deque()
    for pos, word_idx in events:
        window_events.append((pos, word_idx))
        if counts[word_idx] == 0:
            distinct += 1
        counts[word_idx] += 1
        while window_events and pos - window_events[0][0] > window:
            old_pos, old_idx = window_events.popleft()
            counts[old_idx] -= 1
            if counts[old_idx] == 0:
                distinct -= 1
        if distinct == needed:
            return True
    return False


def _keyword_weight(keyword):
    """Una parola chiave di più parole (es. "agricoltura sostenibile") è
    una frase specifica: trovarla nel testo è un segnale più forte di
    trovare una singola parola molto comune isolata (es. "agricoltura" da
    sola, che compare quasi ovunque su un sito ministeriale — vero anche
    per la sua traduzione "agriculture"). Il peso è semplicemente il
    numero di parole della keyword, così una frase di due parole conta il
    doppio di una parola singola."""
    words = _WORD_RE.findall(keyword.lower())
    return len(words) if words else 1


def compute_match_score(matched_keywords):
    """Punteggio grezzo di rilevanza per una segnalazione: somma dei pesi
    di TUTTE le parole chiave (comprese le traduzioni/varianti) che hanno
    trovato un riscontro, non solo "c'è o non c'è" un match. Più parole
    chiave diverse confermano lo stesso argomento, o più sono frasi
    specifiche invece di parole generiche isolate, più alto il
    punteggio — usato per ordinare le segnalazioni e per mostrare
    all'utente quanto fidarsi di ciascuna a colpo d'occhio, senza dover
    aprire ogni bando per scoprirlo."""
    return sum(_keyword_weight(kw) for kw in matched_keywords if kw)


def relevance_label(score):
    """Etichetta leggibile del punteggio, mostrata come tag nell'app.
    Punteggio 0 (nessuna parola chiave trovata — es. una pagina monitorata
    segnalata solo perché il contenuto è cambiato) non riceve etichetta:
    non è un giudizio "debole", è semplicemente l'assenza di un segnale
    basato su parole chiave, e va trattato diversamente da un match
    debole vero e proprio."""
    if score <= 0:
        return None
    if score == 1:
        return "corrispondenza debole"
    if score <= 3:
        return "corrispondenza media"
    return "corrispondenza forte"


def fetch_page_text(url):
    """Scarica il testo "pulito" di una pagina, con un ripiego automatico
    per i siti che rifiutano la richiesta diretta da GitHub Actions (vedi
    commento su JINA_READER_PREFIX piu sopra per i dettagli e i limiti).

    Prova prima la richiesta diretta (comportamento normale, invariato).
    Solo se fallisce, prova il servizio di ripiego. Se falliscono
    entrambe, solleva l'eccezione della richiesta DIRETTA (piu utile per
    capire la causa reale -- es. 403 vs timeout -- di quella del ripiego)."""
    try:
        resp = requests.get(url, headers=HTTP_HEADERS, timeout=HTTP_TIMEOUT)
        resp.raise_for_status()
        text = resp.text
        if BeautifulSoup is not None:
            text = BeautifulSoup(resp.text, "html.parser").get_text(" ", strip=True)
        return text
    except Exception as direct_exc:  # noqa: BLE001
        try:
            fallback_resp = requests.get(
                JINA_READER_PREFIX + url,
                headers={"x-timeout": "30"},
                timeout=JINA_TIMEOUT,
            )
            fallback_resp.raise_for_status()
            return fallback_resp.text
        except Exception:  # noqa: BLE001
            raise direct_exc


def fetch_page_html(url):
    """Come fetch_page_text, ma restituisce l'HTML grezzo (serve per poter
    trovare i singoli link della pagina, non solo il suo testo) invece del
    testo "pulito". Usata solo per l'arricchimento facoltativo in
    extract_matching_links: se la richiesta diretta fallisce (o il sito
    risponde solo tramite il servizio di ripiego, che restituisce testo
    gia' semplificato e non piu' HTML), restituisce semplicemente None
    invece di solleva un'eccezione -- niente di questa funzione deve mai
    far fallire lo scan: la card generica esistente resta comunque la
    base sicura anche se qui non si riesce ad estrarre nulla."""
    try:
        resp = requests.get(url, headers=HTTP_HEADERS, timeout=HTTP_TIMEOUT)
        resp.raise_for_status()
        return resp.text
    except Exception:  # noqa: BLE001
        return None


# Testo di link di navigazione/istituzionali troppo generici perche' un
# loro eventuale match con una parola chiave sia interessante (es. il
# link "Cerca" di un motore di ricerca interno che casualmente contiene
# la parola "bando" da qualche parte nel markup circostante). Elenco
# volutamente corto: in caso di dubbio si preferisce mostrare un link in
# piu' da verificare a mano, piuttosto che rischiare di scartarne uno
# vero (stessa filosofia usata altrove in questo file).
_NAV_LINK_TEXT_BLOCKLIST = {
    "home", "homepage", "contatti", "contattaci", "privacy", "cookie",
    "note legali", "accessibilita", "accessibilit\u00e0", "mappa del sito",
    "sitemap", "accedi", "login", "area riservata", "cerca", "menu",
    "italiano", "english", "facebook", "twitter", "linkedin", "instagram",
    "youtube", "rss", "torna su", "vai al contenuto", "salta al contenuto",
}

# Massimo numero di link specifici mostrati per una singola fonte fissa:
# una pagina elenco molto ricca potrebbe altrimenti produrre una card
# enorme; oltre questa soglia meglio invitare a controllare la pagina
# intera a mano (il link alla pagina completa resta comunque sempre
# presente in coda alla card, vedi check_watch_pages).
MAX_WATCH_MATCH_ITEMS = 8

# Soglia (in caratteri) sopra la quale il testo di un link viene
# considerato di per se' sufficiente come contesto per il confronto con
# le parole chiave, senza risalire al genitore (vedi commento piu' sotto
# dove viene usata). 40 caratteri bastano a escludere i link generici
# tipo "Scopri di piu'"/"Leggi tutto" (di solito sotto i 20 caratteri) ma
# non un titolo di bando vero (quasi sempre sopra i 40).
_MIN_LINK_TEXT_FOR_OWN_CONTEXT = 40


def extract_matching_links(html, base_url, keywords):
    """Cerca, tra tutti i link <a> della pagina, quelli che sembrano
    puntare a un bando specifico pertinente: il testo del link stesso, o
    quello del suo elemento contenitore (riga di tabella/elemento di
    lista/paragrafo -- serve perche' spesso il link dice solo "Scopri di
    piu'" e la parola chiave e' nel testo a fianco), deve contenere sia
    una parola chiave sia un segnale tipico di bando vero
    (BANDO_SIGNAL_PHRASES, stesso criterio gia' usato a livello di intera
    pagina). Restituisce un elenco di {"title", "url"} (senza doppioni
    per URL, max MAX_WATCH_MATCH_ITEMS), oppure una lista vuota se non
    trova nulla di specifico -- in quel caso chi chiama continua a
    mostrare solo la card generica di sempre, senza regressioni.

    ATTENZIONE: e' un arricchimento best-effort, non un parser affidabile
    al 100% -- ogni sito struttura le sue pagine elenco in modo diverso,
    quindi puo' non trovare nulla anche quando un elenco di bandi c'e'
    davvero (falso negativo qui = si torna alla card generica, non e'
    un problema) oppure, piu' raramente, includere un link non davvero
    pertinente (va comunque verificato a mano come ogni altra
    segnalazione di questa app)."""
    if BeautifulSoup is None or not html:
        return []
    try:
        soup = BeautifulSoup(html, "html.parser")
    except Exception:  # noqa: BLE001
        return []
    seen_urls = set()
    items = []
    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        if not href or href.startswith("#") or href.lower().startswith(("javascript:", "mailto:", "tel:")):
            continue
        text = a.get_text(" ", strip=True)
        if not text or len(text) < 4:
            continue
        if text.strip().lower() in _NAV_LINK_TEXT_BLOCKLIST:
            continue
        absolute_url = urljoin(base_url, href)
        if absolute_url in seen_urls:
            continue
        # Verificato con un test dal vivo su un aggregatore di bandi reale:
        # quando l'intera card di un bando e' un unico link cliccabile (molto
        # comune), il suo genitore immediato e' spesso il contenitore di TUTTA
        # la lista, non solo di questa card -- risalire sempre al genitore
        # faceva "contaminare" il contesto di un bando con i titoli dei bandi
        # vicini nella stessa lista (una parola chiave presente in un solo
        # titolo veniva trovata anche su bandi completamente estranei). Se il
        # link ha gia' un testo proprio sufficientemente ricco, lo usiamo da
        # solo; risaliamo al genitore solo per i link brevi/generici (es.
        # "Scopri di piu'") e comunque solo se quel genitore contiene SOLO
        # questo link -- altrimenti e' lo stesso problema, con una soglia
        # diversa.
        if len(text) >= _MIN_LINK_TEXT_FOR_OWN_CONTEXT:
            context_text = text
        else:
            parent = a.find_parent(["li", "tr", "div", "p", "article"])
            if parent is not None and len(parent.find_all("a", href=True)) == 1:
                context_text = parent.get_text(" ", strip=True)
            else:
                context_text = text
        context_tokens = _tokenize(context_text.lower())
        if not any(kw and keyword_matches_text(kw, context_tokens) for kw in keywords):
            continue
        if not any(keyword_matches_text(sig, context_tokens) for sig in BANDO_SIGNAL_PHRASES):
            continue
        seen_urls.add(absolute_url)
        items.append({"title": text[:160], "url": absolute_url})
        if len(items) >= MAX_WATCH_MATCH_ITEMS:
            break
    return items


# Termini tipici di un vero bando (non di una notizia generica che cita
# solo la parola chiave): una pagina scatta solo se ne contiene almeno
# uno, vedi check_watch_pages. Lista volutamente corta e in italiano +
# inglese (per fonti internazionali tipo COST/IMISCOE), non è pensata
# per essere esaustiva.
BANDO_SIGNAL_PHRASES = [
    "bando", "avviso pubblico", "scadenza", "candidatura", "candidature",
    "presentazione delle domande", "domanda di partecipazione",
    "requisiti di partecipazione", "graduatoria",
    "call for proposals", "call for applications", "how to apply",
    "eligibility criteria", "application deadline", "submission deadline",
]


def fetch_watch_pages(sources, previous_hashes):
    """Scarica il testo di ogni fonte CONDIVISA in 'sources' UNA SOLA
    VOLTA per questo giro di scansione — indipendentemente da quanti
    utenti la stiano monitorando: prima (versione a singolo utente)
    questa funzione faceva anche il confronto con le parole chiave nello
    stesso ciclo, il che nella versione multi-utente avrebbe richiesto la
    stessa pagina un'altra volta per ciascun utente. Qui invece si scarica
    e si prepara il testo (tokenizzato, pronto per keyword_matches_text)
    una volta, e chi chiama (match_watch_pages_for_user, una volta per
    ciascun utente) riusa lo stesso risultato senza fare altre richieste
    di rete per il testo della pagina.

    Restituisce (page_cache, page_status, new_hashes):
    - page_cache: {source_id: {"tokens": [...], "changed": bool}} per le
      pagine raggiunte con successo (assente per le altre — vedi
      page_status per il motivo).
    - page_status: elenco con l'esito (raggiunta o no, ed eventuale
      errore) di OGNI pagina, condiviso e identico per tutti gli utenti
      (la raggiungibilità di una pagina è un fatto sulla pagina, non su
      chi la guarda).
    - new_hashes: hash aggiornati da salvare in meta/status (condiviso):
      "changed" (contenuto diverso dall'ultimo controllo) è anch'esso un
      fatto oggettivo sulla pagina, non specifico di un utente."""
    page_cache = {}
    page_status = []
    new_hashes = dict(previous_hashes)
    for page in sources:
        try:
            text = fetch_page_text(page["url"])
            text_lower = text.lower()
            content_hash = hashlib.sha256(text.encode("utf-8", "ignore")).hexdigest()
            changed = previous_hashes.get(page["id"]) not in (None, content_hash)
            new_hashes[page["id"]] = content_hash

            # Tokenizziamo la pagina UNA sola volta (non ad ogni parola
            # chiave, e non per ogni utente): keyword_matches_text tollera
            # plurali/varianti e frasi non adiacenti, vedi il suo commento
            # sopra. "html" resta assente qui: lo scarichiamo solo se e
            # quando serve davvero (vedi _get_cached_page_html sotto), non
            # per ogni pagina indipendentemente dal fatto che interessi a
            # qualcuno.
            page_cache[page["id"]] = {
                "tokens": _tokenize(text_lower),
                "changed": changed,
            }
            page_status.append({
                "id": page["id"], "url": page["url"], "funder": page.get("funder", ""), "ok": True,
            })
        except Exception as exc:  # noqa: BLE001
            print("Avviso: impossibile controllare {} ({}). Salto.".format(page["url"], exc))
            page_status.append({
                "id": page["id"], "url": page["url"], "funder": page.get("funder", ""),
                "ok": False, "error": str(exc),
            })
    return page_cache, page_status, new_hashes


def _get_cached_page_html(cached_entry, url):
    """Scarica l'HTML grezzo di una pagina SOLO la prima volta che serve
    davvero (il primo utente le cui parole chiave trovano un match su
    quella pagina), e lo ricorda in cached_entry (lo stesso dizionario
    condiviso da page_cache, mutato sul posto) per gli utenti successivi
    nello stesso giro di scansione — cosi' una pagina con match per più
    utenti viene scaricata in HTML al massimo una volta per giro, non una
    volta per utente, e una pagina senza nessun match per nessuno non
    viene scaricata in HTML per nulla (comportamento invariato rispetto
    a prima: era già un arricchimento "lazy", solo se matched_keywords
    non era vuoto)."""
    if "html" not in cached_entry:
        try:
            cached_entry["html"] = fetch_page_html(url)
        except Exception:  # noqa: BLE001
            cached_entry["html"] = None
    return cached_entry["html"]


def match_watch_pages_for_user(keywords, sources, page_cache):
    """Confronta, per UN singolo utente, le sue parole chiave con le
    pagine CONDIVISE già scaricate (page_cache, vedi fetch_watch_pages
    sopra) — non fa nessuna richiesta di rete per il testo della pagina,
    e riusa la cache anche per l'eventuale arricchimento HTML (vedi
    _get_cached_page_html). Stessa identica logica di prima (segnala
    SOLO quando trova davvero una parola chiave E un termine tipico di
    un bando vero, vedi BANDO_SIGNAL_PHRASES), applicata al testo in
    cache invece che scaricandolo di nuovo."""
    results = []
    for page in sources:
        cached = page_cache.get(page["id"])
        if cached is None:
            continue  # pagina non raggiunta questo giro: gia' in page_status (condiviso, non per-utente)

        page_tokens = cached["tokens"]
        matched_keywords = [kw for kw in keywords if kw and keyword_matches_text(kw, page_tokens)]
        if not matched_keywords:
            continue  # nessuna parola chiave trovata: niente da segnalare

        # Isolato nel suo proprio try/except: un errore qui (es. una fonte
        # senza "title"/"category" in Firestore) riguarda solo
        # l'elaborazione del contenuto per QUESTO utente, non la
        # raggiungibilita' della pagina (gia' accertata in
        # fetch_watch_pages) né gli altri utenti.
        try:
            matched_signal = next(
                (sig for sig in BANDO_SIGNAL_PHRASES if keyword_matches_text(sig, page_tokens)),
                None,
            )
            if not matched_signal:
                continue  # parola chiave trovata ma nessun segnale tipico di un bando vero: probabile notizia/menzione generica

            # Arricchimento facoltativo: proviamo a individuare i singoli
            # link della pagina che riguardano davvero una parola chiave,
            # invece di lasciare solo il link generico all'intera pagina.
            # Richiede l'HTML grezzo, in cache condivisa (vedi
            # _get_cached_page_html) -- se non e' disponibile (o non
            # trova nulla di specifico) restiamo sul comportamento di
            # sempre: nessuna regressione.
            match_items = []
            try:
                page_html = _get_cached_page_html(cached, page["url"])
                if page_html:
                    match_items = extract_matching_links(page_html, page["url"], matched_keywords)
            except Exception:  # noqa: BLE001
                match_items = []

            note_parts = []
            if matched_keywords:
                note_parts.append("parole chiave trovate: " + ", ".join(matched_keywords[:5]))
            note_parts.append("contiene anche linguaggio tipico di un bando (\"" + matched_signal + "\")")
            if cached["changed"]:
                note_parts.append("contenuto della pagina cambiato dall'ultimo controllo")
            if match_items:
                summary = (
                    "Trovati {} link specifici in questa pagina che contengono le tue parole chiave "
                    "(vedi elenco sotto) — " + "; ".join(note_parts) + "."
                ).format(len(match_items))
            else:
                summary = "Da verificare manualmente — " + "; ".join(note_parts) + "."

            # Punteggio di rilevanza: 0 se il "segnale" è solo il
            # cambiamento di contenuto (nessuna parola chiave — spesso
            # rumore, es. un banner o una data che cambia), altrimenti
            # cresce con quante/quali parole chiave hanno trovato
            # riscontro (vedi compute_match_score). Niente etichetta
            # quando il punteggio è 0: non vogliamo far sembrare "debole"
            # un segnale che in realtà non ha nessuna parola chiave dietro.
            match_score = compute_match_score(matched_keywords)
            relevance_tag = relevance_label(match_score)

            results.append({
                "id": "watch-" + page["id"],
                # .get() con ripiego, non accesso diretto: una fonte aggiunta
                # senza titolo/categoria (es. a mano su Firestore, o con un
                # form futuro meno rigido) non deve far fallire l'intera
                # segnalazione -- meglio un titolo/categoria generici che
                # nessuna segnalazione.
                "title": page.get("title") or page.get("funder") or page["url"],
                "funder": page.get("funder", ""),
                "category": page.get("category") or "funding",
                "status": "watch",
                "deadlineText": "vedi pagina ufficiale",
                "tags": ["da verificare"] + (["aggiornata"] if cached["changed"] else []) + ([relevance_tag] if relevance_tag else []),
                "summary": summary,
                "matchScore": match_score,
                "url": page["url"],
                "source": "page-watcher",
                # Elenco di link specifici trovati su questa pagina (puo'
                # essere vuoto: in quel caso l'app mostra solo il link
                # generico alla pagina intera, come faceva finora).
                "matches": match_items,
            })
        except Exception as exc:  # noqa: BLE001
            print("Avviso: pagina raggiunta ma elaborazione del contenuto non riuscita per {} ({}). Salto la segnalazione per questa pagina.".format(page["url"], exc))
            continue
    return results


def prune_stale_watch_calls(db, user_ref, current_source_ids):
    """Le segnalazioni delle fonti fisse (source == "page-watcher"), nello
    spazio privato DI QUESTO UTENTE (user_ref.collection("calls")), hanno
    id "watch-<id della fonte>". A differenza dei bandi Horizon Europe
    (vedi prune_stale_horizon_calls sotto), qui NON le riallineiamo ad
    ogni scansione: sono pensate per tracciare uno stato nel tempo, quindi
    una fonte che questo giro non trova piu' un match non deve perdere la
    sua segnalazione precedente (potrebbe essere solo una pagina
    temporaneamente diversa, non un motivo per dimenticare quanto trovato
    prima).

    Rimuoviamo SOLO le segnalazioni la cui fonte e' stata esplicitamente
    rimossa dall'elenco CONDIVISO (pulsante "Rimuovi" in "Fonti
    monitorate", che chiunque abbia un account puo' premere): quelle sono
    inequivocabilmente orfane per tutti gli utenti che le avevano, non
    solo per chi ha premuto il pulsante."""
    docs = user_ref.collection("calls").where("source", "==", "page-watcher").stream()
    batch = db.batch()
    removed = 0
    for doc in docs:
        source_id = doc.id[len("watch-"):] if doc.id.startswith("watch-") else doc.id
        if source_id not in current_source_ids:
            batch.delete(doc.reference)
            removed += 1
            if removed % 400 == 0:
                batch.commit()
                batch = db.batch()
    batch.commit()
    return removed


def prune_stale_horizon_calls(db, user_ref, current_ids):
    """I bandi trovati su Horizon Europe (source == "funding-tenders-api"),
    nello spazio privato DI QUESTO UTENTE, sono il riflesso di una ricerca
    dal vivo con le SUE parole chiave, fatta da zero ad ogni scansione: se
    un bando non compare piu tra i risultati (perche non corrisponde piu
    alle sue parole chiave, o perche in realta era un progetto gia
    finanziato ora giustamente escluso), non ha senso lasciarlo per sempre
    nel suo database privato con dati potenzialmente non piu validi. A
    differenza delle pagine monitorate (che rappresentano uno stato da
    tracciare nel tempo), i risultati Horizon vengono qui riallineati
    esattamente a quanto trovato nell'ultima scansione: chi non c'e piu
    viene rimosso."""
    docs = user_ref.collection("calls").where("source", "==", "funding-tenders-api").stream()
    batch = db.batch()
    removed = 0
    for doc in docs:
        if doc.id not in current_ids:
            batch.delete(doc.reference)
            removed += 1
            if removed % 400 == 0:
                batch.commit()
                batch = db.batch()
    batch.commit()
    return removed


def upsert_calls(db, user_ref, items):
    """Scrive/aggiorna i bandi nello spazio privato DI QUESTO UTENTE
    (merge=True: aggiorna solo i campi presenti, senza cancellare il
    resto del documento).

    "foundAt" (la data di "trovato il" mostrata nell'app) va scritta SOLO
    la prima volta che un bando viene visto: prima leggiamo quali id tra
    quelli di questa scansione esistono gia, e per quelli NON includiamo
    "foundAt" nell'aggiornamento -- con merge=True questo lascia il valore
    gia salvato invariato. Prima invece veniva sovrascritta ad ogni
    scansione anche per i bandi gia noti, quindi "trovato il" mostrava
    sempre la data dell'ultima scansione invece della prima."""
    if not items:
        return 0

    calls_ref = user_ref.collection("calls")
    doc_ids = [item["id"] for item in items]
    existing_ids = set()
    # Una sola tornata di letture (invece di una query per id) per sapere
    # quali bandi esistono gia. get_all non ha il limite di 500 dei batch
    # di scrittura, ma per sicurezza leggiamo comunque a blocchi.
    for start in range(0, len(doc_ids), 300):
        chunk = doc_ids[start:start + 300]
        refs = [calls_ref.document(doc_id) for doc_id in chunk]
        for snapshot in db.get_all(refs):
            if snapshot.exists:
                existing_ids.add(snapshot.id)

    batch = db.batch()
    count = 0
    for item in items:
        doc_id = item.pop("id")
        if doc_id not in existing_ids:
            item["foundAt"] = now_iso()
        batch.set(calls_ref.document(doc_id), item, merge=True)
        count += 1
        if count % 400 == 0:  # limite batch Firestore
            batch.commit()
            batch = db.batch()
    batch.commit()
    return count


def compute_health(previous_health, horizon_stats, page_status):
    """Confronta l'esito di questa scansione con quello delle precedenti
    (letto da meta/status.health) per distinguere un intoppo isolato da un
    guasto persistente, e prepara un elenco di messaggi pronti da mostrare
    nell'app. Un fallimento isolato (una parola chiave o una pagina che
    non risponde una volta sola) NON genera un avviso: capita, ed è
    normale per fonti esterne su cui non abbiamo controllo. Un fallimento
    che si ripete su scansioni consecutive, invece, è il segnale che
    qualcosa si è rotto davvero (endpoint cambiato, pagina spostata, sito
    che blocca le richieste) e va segnalato con chiarezza, non solo nel
    log di GitHub Actions che quasi nessuno controlla a meno che non gli
    venga detto di farlo."""
    previous_health = previous_health or {}

    prev_horizon = previous_health.get("horizon") or {}
    horizon_total_failure = horizon_stats["termsTotal"] > 0 and horizon_stats["termsFailed"] == horizon_stats["termsTotal"]
    horizon_streak = (prev_horizon.get("consecutiveFailStreak", 0) + 1) if horizon_total_failure else 0
    horizon_health = {
        "termsTotal": horizon_stats["termsTotal"],
        "termsFailed": horizon_stats["termsFailed"],
        "consecutiveFailStreak": horizon_streak,
        "lastErrorSample": horizon_stats.get("lastErrorSample"),
    }

    prev_streaks = ((previous_health.get("watchPages") or {}).get("failStreaks")) or {}
    new_streaks = {}
    pages_failed = 0
    for stat in page_status:
        if stat["ok"]:
            continue
        pages_failed += 1
        new_streaks[stat["id"]] = prev_streaks.get(stat["id"], 0) + 1

    failing_persistent = []
    for stat in page_status:
        streak = new_streaks.get(stat["id"], 0)
        if streak >= 2:
            failing_persistent.append({
                "id": stat["id"], "url": stat["url"], "funder": stat.get("funder", ""),
                "streak": streak, "lastError": stat.get("error"),
            })

    watch_pages_health = {
        "pagesTotal": len(page_status),
        "pagesFailed": pages_failed,
        "failStreaks": new_streaks,
        "failingPersistent": failing_persistent,
    }

    issues = []
    if horizon_streak >= 2:
        issues.append(
            "Horizon Europe / Funding & Tenders Portal non è raggiungibile da {} scansioni consecutive "
            "(ultimo errore: {}).".format(horizon_streak, horizon_health["lastErrorSample"] or "sconosciuto")
        )
    for p in failing_persistent:
        issues.append(
            "Pagina monitorata non raggiungibile da {} scansioni consecutive: {} ({}).".format(
                p["streak"], p["funder"] or p["url"], p["url"])
        )

    return {
        "horizon": horizon_health,
        "watchPages": watch_pages_health,
        "issues": issues,
        "hasPersistentIssues": bool(issues),
    }


def close_expired_calls(user_ref):
    today = datetime.now(timezone.utc).date().isoformat()
    docs = user_ref.collection("calls").where("status", "in", ["open", "closing"]).stream()
    n = 0
    for doc in docs:
        data = doc.to_dict()
        deadline = data.get("deadlineDate")
        if deadline and deadline < today:
            doc.reference.update({"status": "closed"})
            n += 1
    return n


def main():
    try:
        db = init_firestore()
    except Exception as exc:  # noqa: BLE001
        print("ERRORE FATALE: impossibile inizializzare la connessione a Firestore ({}: {}). Scansione annullata.".format(
            type(exc).__name__, exc))
        sys.exit(1)

    try:
        _run_scan(db)
    except Exception as exc:  # noqa: BLE001 -- errore imprevisto in un punto qualsiasi dello scan: non deve sparire in
        # silenzio. Lo registriamo (quando possibile) su Firestore stesso, cosi' chi
        # apre l'app vede un motivo chiaro invece di un "ultima scansione" vecchio
        # senza spiegazione, poi rilanciamo l'eccezione: GitHub Actions deve comunque
        # segnare l'esecuzione come fallita (si vede nella scheda Actions).
        print("ERRORE FATALE: la scansione si e' interrotta per un errore imprevisto: {}: {}".format(
            type(exc).__name__, exc))
        try:
            db.collection("meta").document("status").set({
                "lastRunCrashed": True,
                "lastCrashAt": now_iso(),
                "lastCrashError": "{}: {}".format(type(exc).__name__, exc),
            }, merge=True)
        except Exception:  # noqa: BLE001
            print("(impossibile anche solo registrare l'errore su Firestore -- probabilmente Firestore stesso non e' raggiungibile in questo momento)")
        raise


def _run_scan(db):
    """Orchestratore multi-utente: scarica le fonti condivise una sola
    volta per questo giro, poi gira una volta per ciascun utente
    registrato (_run_scan_for_user), scrivendo ogni risultato SOLO nel
    suo spazio privato. Un errore per un singolo utente (es. le sue
    parole chiave producono un bug in un caso limite) viene isolato e
    registrato per lui, e non impedisce la scansione degli altri."""
    sources = load_sources(db)

    users = list_registered_users(db)
    if not users:
        print("Nessun utente registrato: niente da scansionare.")
        return

    global_meta_ref = db.collection("meta").document("status")
    global_meta = get_doc(global_meta_ref, default={})
    previous_hashes = global_meta.get("pageHashes", {})

    page_cache, page_status, new_hashes = fetch_watch_pages(sources, previous_hashes)
    pages_ok = sum(1 for p in page_status if p["ok"])
    print("Fonti condivise scaricate una sola volta per questo giro: {}/{} raggiunte.".format(
        pages_ok, len(page_status)))

    current_source_ids = {page["id"] for page in sources}

    ran_count = 0
    for user in users:
        uid = user["uid"]
        user_ref = db.collection("users").document(uid)
        try:
            ran = _run_scan_for_user(
                db, user_ref, sources, page_cache, page_status, pages_ok, current_source_ids)
            if ran:
                ran_count += 1
        except Exception as exc:  # noqa: BLE001 -- un utente non deve bloccare gli altri
            print("[{}] ERRORE: la scansione per questo utente si e' interrotta per un errore imprevisto: {}: {}".format(
                uid, type(exc).__name__, exc))
            try:
                user_ref.collection("meta").document("status").set({
                    "lastRunCrashed": True,
                    "lastCrashAt": now_iso(),
                    "lastCrashError": "{}: {}".format(type(exc).__name__, exc),
                }, merge=True)
            except Exception:  # noqa: BLE001
                print("[{}] (impossibile anche solo registrare l'errore su Firestore per questo utente)".format(uid))

    global_meta_ref.set({
        "pageHashes": new_hashes,
        "lastSharedFetch": now_iso(),
    }, merge=True)

    print("Scansione completata: {}/{} utenti scansionati questo giro (gli altri non erano ancora al momento giusto secondo la loro frequenza).".format(
        ran_count, len(users)))


def _run_scan_for_user(db, user_ref, sources, page_cache, page_status, pages_ok, current_source_ids):
    """Esegue lo scan per UN singolo utente registrato: legge le sue
    impostazioni private (users/{uid}/config), decide se e' il momento
    (in base alla SUA frequenza), interroga Horizon Europe con le SUE
    parole chiave, confronta le fonti condivise gia' scaricate
    (page_cache, vedi fetch_watch_pages) con le SUE parole chiave, e
    scrive/pota SOLO nel suo spazio privato. Restituisce True se ha
    davvero eseguito una scansione, False se ha saltato perche' non era
    ancora il momento (secondo la sua frequenza)."""
    uid = user_ref.id
    config = get_doc(user_ref.collection("config").document("main"), default={"keywords": [], "frequency": DEFAULT_FREQUENCY})
    meta = get_doc(user_ref.collection("meta").document("status"), default={})

    was_reset = check_and_apply_reset(db, user_ref)

    forced = os.environ.get("FORCE_RUN") == "true"
    run_due, reason = should_run(config, meta)
    print("[{}] Verifica frequenza: {}".format(uid, reason))
    if forced:
        print("[{}] Esecuzione forzata (avviata a mano da GitHub Actions): scansione comunque in corso.".format(uid))
    if not run_due and not was_reset and not forced:
        print("[{}] Non e' ancora il momento di eseguire la scansione. Salto.".format(uid))
        return False

    keywords = config.get("keywords") or []
    if isinstance(keywords, str):
        # Difensivo: se per un motivo qualsiasi (bug futuro, modifica a mano su
        # Firestore) "keywords" risultasse una stringa invece di un elenco,
        # iterarci sopra direttamente la scomporrebbe carattere per carattere
        # (ogni lettera trattata come parola chiave separata) invece di dare un
        # errore chiaro -- la trattiamo come un'unica parola chiave.
        keywords = [keywords]

    sources_checked = []
    all_new_items = []

    if not keywords:
        # Nessuna parola chiave impostata (es. utente appena registrato,
        # non ancora configurato): nessun ripiego su un argomento
        # predefinito (a differenza della versione a singolo utente, che
        # aveva un argomento di ricerca fisso) -- semplicemente nessun
        # risultato da cercare, finche' l'utente non imposta le sue
        # parole chiave in "Impostazioni ricerca".
        print("[{}] Nessuna parola chiave impostata: nessun risultato da cercare.".format(uid))
        search_keywords, horizon_terms = [], []
        horizon_items, horizon_stats = [], {"termsTotal": 0, "termsFailed": 0, "lastErrorSample": None}
    else:
        print("[{}] Scansione in corso con parole chiave: {}".format(uid, keywords))
        search_keywords, translations = expand_keywords_with_translation(keywords)
        if search_keywords != keywords:
            print("[{}] Parole chiave ampliate con traduzione automatica: {}".format(uid, search_keywords))

        horizon_terms = build_horizon_terms(keywords, translations)
        if horizon_terms != search_keywords[:len(horizon_terms)]:
            print("[{}] Termini usati per la ricerca su Horizon Europe: {}".format(uid, horizon_terms))

        horizon_items, horizon_stats = search_funding_tenders_portal(horizon_terms)

    horizon_ids_now = {item["id"] for item in horizon_items}
    sources_checked.append(
        "Horizon Europe / Funding & Tenders Portal: {} risultati ({}/{} parole chiave riuscite)".format(
            len(horizon_items), horizon_stats["termsTotal"] - horizon_stats["termsFailed"], horizon_stats["termsTotal"])
    )
    all_new_items.extend(horizon_items)

    watch_items = match_watch_pages_for_user(search_keywords, sources, page_cache) if search_keywords else []
    sources_checked.append(
        "Pagine monitorate (condivise, configurabili in Firestore): {} segnalazioni — {}/{} pagine raggiunte".format(
            len(watch_items), pages_ok, len(page_status))
    )
    all_new_items.extend(watch_items)

    health = compute_health(meta.get("health"), horizon_stats, page_status)
    if health["issues"]:
        print("[{}] ATTENZIONE — problemi persistenti rilevati:".format(uid))
        for issue in health["issues"]:
            print("  - " + issue)

    # Prima si scrivono i nuovi risultati, POI si chiudono le call scadute:
    # così una call trovata solo ora ma con scadenza già passata (capita con
    # l'API non ufficiale del portale UE) viene corretta nella stessa
    # esecuzione, non in quella successiva.
    written = upsert_calls(db, user_ref, all_new_items)

    # La pulizia dei bandi Horizon obsoleti si fa SOLO se la ricerca di
    # questa scansione e' riuscita per intero (nessuna parola chiave
    # fallita): altrimenti "non trovato piu'" potrebbe voler dire solo che
    # l'API non ha risposto per quel termine, non che il bando non esiste
    # piu' -- e cancellarlo sarebbe un errore, non una pulizia.
    if horizon_stats["termsFailed"] == 0:
        pruned = prune_stale_horizon_calls(db, user_ref, horizon_ids_now)
    else:
        pruned = 0
        print("[{}] Pulizia bandi Horizon obsoleti saltata: {}/{} parole chiave fallite in questa scansione.".format(
            uid, horizon_stats["termsFailed"], horizon_stats["termsTotal"]))

    # Segnalazioni delle fonti fisse orfane (fonte rimossa dall'elenco
    # condiviso): vedi il commento di prune_stale_watch_calls per il
    # perche' e' scoperto solo qui (non nel client web) e solo per le
    # fonti rimosse (non per quelle che semplicemente non trovano piu' un
    # match questo giro).
    pruned_watch = prune_stale_watch_calls(db, user_ref, current_source_ids)

    closed_count = close_expired_calls(user_ref)

    user_ref.collection("meta").document("status").set({
        "lastRun": now_iso(),
        "lastRunCrashed": False,
        "sourcesChecked": sources_checked,
        "health": health,
        "notes": "{} voci scritte/aggiornate, {} bandi Horizon obsoleti rimossi, {} segnalazioni orfane rimosse (fonte rimossa), {} bandi contrassegnati come scaduti.".format(
            written, pruned, pruned_watch, closed_count),
    }, merge=True)

    print("[{}] Fatto: {} voci aggiornate, {} bandi Horizon obsoleti rimossi, {} segnalazioni orfane rimosse, {} bandi chiusi automaticamente.".format(
        uid, written, pruned, pruned_watch, closed_count))
    return True


if __name__ == "__main__":
    main()