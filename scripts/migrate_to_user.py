#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Migrazione una tantum dai dati "vecchio stile" (condivisi, ad un unico
account) al nuovo modello multi-utente: copia le impostazioni e i bandi
trovati finora dentro /users/{uid}/... per l'utente indicato, poi rimuove
le vecchie copie in cima (/config/main e /calls/*), che con le nuove regole
di sicurezza non sono comunque più leggibili da nessuno.

Va eseguito UNA SOLA VOLTA, dopo che il proprio account è stato creato
nell'app (così l'uid esiste già in Firebase Authentication), tramite il
workflow GitHub Actions "Migra i dati verso il tuo account" (richiede
l'uid come input) -- non richiede mai credenziali da parte dell'utente:
usa lo stesso segreto FIREBASE_SERVICE_ACCOUNT già configurato per lo
scraper.

Cose che NON tocca, perché restano condivise tra tutti gli utenti nel
nuovo modello: /sources/* (fonti monitorate) e /meta/status (stato
condiviso della scansione, es. pageHashes/lastSharedFetch).
"""
import json
import os
import sys

import firebase_admin
from firebase_admin import auth, credentials, firestore


def init_firestore():
    raw = os.environ.get("FIREBASE_SERVICE_ACCOUNT")
    if not raw:
        print("ERRORE: variabile d'ambiente FIREBASE_SERVICE_ACCOUNT mancante.")
        sys.exit(1)
    cred_dict = json.loads(raw)
    cred = credentials.Certificate(cred_dict)
    firebase_admin.initialize_app(cred)
    return firestore.client()


def main():
    uid = (os.environ.get("MIGRATE_UID") or "").strip()
    if not uid:
        print("ERRORE: variabile d'ambiente MIGRATE_UID mancante (l'uid dell'account a cui migrare i dati).")
        sys.exit(1)

    db = init_firestore()

    # Verifica che l'uid corrisponda davvero a un account già registrato:
    # un uid sbagliato (typo, copiato male) altrimenti creerebbe silenziosamente
    # dati "orfani" sotto un uid che nessuno potrà mai vedere.
    try:
        user_record = auth.get_user(uid)
    except auth.UserNotFoundError:
        print("ERRORE: nessun account Firebase Authentication trovato con uid='{}'.".format(uid))
        print("Controlla di aver copiato l'uid corretto (visibile in Firebase Console -> Authentication).")
        sys.exit(1)
    print("Migrazione verso l'account: {} (uid={})".format(user_record.email or "(senza email)", uid))

    user_ref = db.collection("users").document(uid)
    # Assicura che esista il documento /users/{uid} con l'email, cosi'
    # scan.py (list_registered_users) lo trova anche se l'utente non ha
    # ancora fatto altre azioni nell'app dopo la registrazione.
    user_ref.set({"email": user_record.email or ""}, merge=True)

    # --- Impostazioni (parole chiave, frequenza) ---
    old_config_ref = db.collection("config").document("main")
    old_config_snap = old_config_ref.get()
    config_migrated = False
    if old_config_snap.exists:
        data = old_config_snap.to_dict() or {}
        user_ref.collection("config").document("main").set(data, merge=True)
        old_config_ref.delete()
        config_migrated = True
        print("Impostazioni migrate: parole chiave={}, frequenza={}".format(
            data.get("keywords"), data.get("frequency")))
    else:
        print("Nessuna impostazione condivisa da migrare (/config/main non esiste).")

    # --- Bandi trovati finora ---
    old_calls_ref = db.collection("calls")
    old_calls_docs = list(old_calls_ref.stream())
    calls_migrated = 0
    for doc in old_calls_docs:
        user_ref.collection("calls").document(doc.id).set(doc.to_dict() or {})
        old_calls_ref.document(doc.id).delete()
        calls_migrated += 1
    print("Bandi migrati: {}".format(calls_migrated))

    print()
    print("Fatto. Riepilogo:")
    print("  - impostazioni migrate: {}".format("si" if config_migrated else "no (nessuna da migrare)"))
    print("  - bandi migrati: {}".format(calls_migrated))
    print("  - fonti monitorate (/sources): non toccate, restano condivise con tutti gli utenti.")


if __name__ == "__main__":
    main()
