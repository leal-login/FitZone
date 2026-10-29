# app.py : application Flask de la salle de sport (Projet 15 : abonnements et séances)
# Lancement : python app.py puis ouvrir http://127.0.0.1:5000

import hmac
import os
import re
import secrets
import sqlite3
import unicodedata
from datetime import date, datetime, time, timedelta
from functools import wraps

from flask import (Flask, abort, flash, g, jsonify, make_response, redirect, render_template,
                   request, session, url_for)
from werkzeug.security import check_password_hash, generate_password_hash

app = Flask(__name__)

# Clé nécessaire aux messages flash et aux sessions (connexion).
# Valeur de développement : à remplacer par une valeur secrète si le site était mis en ligne.
app.secret_key = os.environ.get("SECRET_KEY", "cle-de-developpement-salle-de-sport")
app.config.update(
    SESSION_COOKIE_HTTPONLY=True,    # le cookie de session n'est pas lisible par JavaScript
    SESSION_COOKIE_SAMESITE="Lax",   # le cookie n'est pas envoyé par les sites tiers
)

CHEMIN_BASE = os.path.join(app.root_path, "projet.db")

# Noms des jours : l'indice 0 correspond à lundi (comme date.weekday() et la colonne jour_semaine)
JOURS = ["Lundi", "Mardi", "Mercredi", "Jeudi", "Vendredi", "Samedi", "Dimanche"]
MOIS = ["janvier", "février", "mars", "avril", "mai", "juin", "juillet", "août",
        "septembre", "octobre", "novembre", "décembre"]

# On réserve au plus 7 jours à l'avance (règle de gestion RG7)
DELAI_RESERVATION_JOURS = 7

# Empreinte « factice » : quand le numéro saisi n'existe pas, on vérifie quand même un mot de passe
# contre cette empreinte, pour que la réponse prenne le même temps. Ainsi on ne peut pas deviner
# quels numéros sont inscrits en mesurant le temps de réponse.
EMPREINTE_FACTICE = generate_password_hash("empreinte-factice")


# ---------------------------------------------------------------------------
# Base de données
# ---------------------------------------------------------------------------
def get_db():
    """Renvoie la connexion SQLite de la requête en cours (ouverte une seule fois par requête)."""
    if "db" not in g:
        g.db = sqlite3.connect(CHEMIN_BASE)
        g.db.row_factory = sqlite3.Row            # permet d'écrire ligne["nom"] au lieu de ligne[0]
        g.db.execute("PRAGMA foreign_keys = ON")  # à refaire à CHAQUE connexion
    return g.db


@app.teardown_appcontext
def fermer_db(erreur=None):
    """Ferme la connexion à la fin de la requête, même en cas d'erreur."""
    db = g.pop("db", None)
    if db is not None:
        db.close()


# ---------------------------------------------------------------------------
# Fonctions utilitaires
# ---------------------------------------------------------------------------
def lundi_de(jour):
    """Renvoie le lundi de la semaine qui contient `jour`."""
    return jour - timedelta(days=jour.weekday())


def lire_date(texte):
    """Convertit « AAAA-MM-JJ » en date ; renvoie None si le texte est invalide."""
    try:
        return date.fromisoformat(texte)
    except (TypeError, ValueError):
        return None


def debut_seance(date_iso, heure):
    """Renvoie le moment exact où commence une séance (date « AAAA-MM-JJ » et heure « HH:MM »)."""
    return datetime.combine(date.fromisoformat(date_iso), time.fromisoformat(heure))


def normaliser_telephone(texte):
    """Nettoie un numéro saisi (espaces, points, tirets, indicatif +237) et renvoie les 9 chiffres
    commençant par 6, ou None si le numéro n'est pas valide."""
    numero = re.sub(r"[\s.\-]", "", texte or "")
    if numero.startswith("+237"):
        numero = numero[4:]
    elif numero.startswith("237") and len(numero) == 12:
        numero = numero[3:]
    return numero if re.fullmatch(r"6[0-9]{8}", numero) else None


@app.template_filter("fcfa")
def filtre_fcfa(montant):
    """Affiche un montant avec séparateur de milliers : 10000 devient « 10 000 FCFA »."""
    return f"{montant:,}".replace(",", "\u00a0") + "\u00a0FCFA"


@app.template_filter("duree")
def filtre_duree(jours):
    """Affiche une durée en jours : « 1 jour », « 30 jours »."""
    return f"{jours} jour" if jours == 1 else f"{jours} jours"


@app.template_filter("date_fr")
def filtre_date_fr(texte):
    """Affiche une date ISO en français : « 2026-11-01 » devient « 1er novembre 2026 »."""
    jour = lire_date(texte)
    if jour is None:
        return texte
    numero = "1er" if jour.day == 1 else str(jour.day)
    return f"{numero} {MOIS[jour.month - 1]} {jour.year}"


@app.template_filter("slug")
def filtre_slug(texte):
    """Texte sans accents ni majuscules : « Aérobic » devient « aerobic » (sert à colorer une discipline)."""
    sans_accents = unicodedata.normalize("NFD", texte or "").encode("ascii", "ignore").decode("ascii")
    return re.sub(r"[^a-z0-9]+", "-", sans_accents.lower()).strip("-")


@app.template_filter("prenom")
def filtre_prenom(nom):
    """Premier mot d'un nom complet : « Aline Mbarga » devient « Aline »."""
    return (nom or "").split(" ")[0]


@app.template_filter("initiales")
def filtre_initiales(nom):
    """Initiales (deux au plus) pour l'avatar : « Aline Mbarga » devient « AM »."""
    return "".join(mot[0] for mot in (nom or "").split()[:2]).upper()


@app.template_filter("mois_fr")
def filtre_mois_fr(texte):
    """Affiche un mois ISO en français : « 2026-10 » devient « octobre 2026 »."""
    try:
        annee, mois = texte.split("-")
        return f"{MOIS[int(mois) - 1]} {annee}"
    except (AttributeError, ValueError, IndexError):
        return texte


@app.template_filter("jour_fr")
def filtre_jour_fr(texte):
    """Affiche une date ISO avec son jour : « 2026-10-27 » devient « Mardi 27 octobre »."""
    jour = lire_date(texte)
    if jour is None:
        return texte
    numero = "1er" if jour.day == 1 else str(jour.day)
    return f"{JOURS[jour.weekday()]} {numero} {MOIS[jour.month - 1]}"


# ---------------------------------------------------------------------------
# Sécurité : jeton CSRF et utilisateur connecté
# ---------------------------------------------------------------------------
# Toute action en POST doit porter un jeton secret propre à la session. Sans lui, un site
# malveillant pourrait faire envoyer un formulaire à l'insu d'un utilisateur connecté.
def jeton_csrf():
    """Renvoie le jeton CSRF de la session (le crée au besoin)."""
    if "csrf" not in session:
        session["csrf"] = secrets.token_hex(16)
    return session["csrf"]


app.jinja_env.globals["jeton_csrf"] = jeton_csrf


@app.before_request
def verifier_csrf():
    """Refuse tout POST dont le jeton est absent ou faux."""
    if request.method == "POST":
        envoye = request.form.get("csrf_token") or request.headers.get("X-CSRF-Token", "")
        attendu = session.get("csrf")
        if not attendu or not hmac.compare_digest(envoye, attendu):
            abort(400)


@app.before_request
def charger_utilisateur():
    """Retrouve le membre ou le gérant connecté à partir de la session (g.membre, g.admin).
    L'identité vient toujours de la session, jamais d'un champ de formulaire."""
    g.membre = None
    g.admin = None
    if request.endpoint == "static":
        return
    if "membre_id" in session:
        g.membre = get_db().execute(
            "SELECT id, nom, telephone FROM membres WHERE id = ?", (session["membre_id"],)).fetchone()
        if g.membre is None:                      # le membre n'existe plus : on oublie la session
            session.pop("membre_id")
    if "admin_id" in session:
        g.admin = get_db().execute(
            "SELECT id, login FROM administrateurs WHERE id = ?", (session["admin_id"],)).fetchone()
        if g.admin is None:
            session.pop("admin_id")


def membre_requis(vue):
    """Décorateur : la page n'est accessible qu'à un membre connecté."""
    @wraps(vue)
    def enveloppe(*args, **kwargs):
        if g.membre is None:
            flash("Connectez-vous pour accéder à votre espace.", "info")
            return redirect(url_for("connexion"))
        return vue(*args, **kwargs)
    return enveloppe


def connexion_requise(vue):
    """Décorateur : la page n'est accessible qu'au gérant connecté (espace d'administration)."""
    @wraps(vue)
    def enveloppe(*args, **kwargs):
        if g.admin is None:
            flash("Connectez-vous pour accéder à l’administration.", "info")
            return redirect(url_for("admin_connexion"))
        return vue(*args, **kwargs)
    return enveloppe


# ---------------------------------------------------------------------------
# Calculs métier
# ---------------------------------------------------------------------------
def construire_planning(db, lundi, maintenant, membre_id=None):
    """Prépare le planning d'une semaine : pour chaque jour, les séances avec leurs places restantes.

    Si un membre est connecté (`membre_id`), chaque séance indique aussi s'il l'a déjà réservée
    (`reservee`) et le numéro de sa réservation (`reservation_id`), utile pour l'annuler."""
    aujourdhui = maintenant.date()
    dimanche = lundi + timedelta(days=6)

    cours = db.execute(
        "SELECT id, discipline, coach, jour_semaine, heure, salle, places_max "
        "FROM cours ORDER BY heure, salle").fetchall()

    # Nombre de places déjà prises pour chaque séance (cours + date) de la semaine
    lignes = db.execute(
        "SELECT cours_id, date_seance, COUNT(*) AS nombre FROM reservations_cours "
        "WHERE date_seance BETWEEN ? AND ? GROUP BY cours_id, date_seance",
        (lundi.isoformat(), dimanche.isoformat())).fetchall()
    prises = {(ligne["cours_id"], ligne["date_seance"]): ligne["nombre"] for ligne in lignes}

    # Réservations du membre connecté sur la semaine
    siennes = {}
    if membre_id is not None:
        lignes = db.execute(
            "SELECT id, cours_id, date_seance FROM reservations_cours "
            "WHERE membre_id = ? AND date_seance BETWEEN ? AND ?",
            (membre_id, lundi.isoformat(), dimanche.isoformat())).fetchall()
        siennes = {(ligne["cours_id"], ligne["date_seance"]): ligne["id"] for ligne in lignes}

    jours = []
    for decalage in range(7):
        jour = lundi + timedelta(days=decalage)
        seances = []
        for c in cours:
            if c["jour_semaine"] != decalage:
                continue
            cle = (c["id"], jour.isoformat())
            restantes = c["places_max"] - prises.get(cle, 0)
            commencee = debut_seance(jour.isoformat(), c["heure"]) <= maintenant   # passée ou en cours
            hors_delai = jour > aujourdhui + timedelta(days=DELAI_RESERVATION_JOURS)
            seances.append({
                "cours_id": c["id"],
                "discipline": c["discipline"],
                "coach": c["coach"],
                "heure": c["heure"],
                "salle": c["salle"],
                "places_max": c["places_max"],
                "places_restantes": max(restantes, 0),
                "complet": restantes <= 0,
                "passee": commencee,
                "reservable": restantes > 0 and not commencee and not hors_delai and cle not in siennes,
                "reservee": cle in siennes,
                "reservation_id": siennes.get(cle),
            })
        jours.append({
            "date": jour.isoformat(),
            "nom": JOURS[decalage],
            "aujourdhui": jour == aujourdhui,
            "seances": seances,
        })

    return {
        "debut": lundi.isoformat(),
        "fin": dimanche.isoformat(),
        "aujourdhui": aujourdhui.isoformat(),
        "jours": jours,
    }


def etat_abonnement(db, membre_id, aujourdhui):
    """Décrit la situation d'un membre : abonnement en cours, à venir, expiré ou aucun.

    La date de fin est incluse : « jours restants » vaut 0 le dernier jour. La progression compte
    les jours consommés (aujourd'hui compris) sur la durée totale : 100 % le dernier jour."""
    requete = ("SELECT a.date_debut, a.date_fin, f.nom AS formule FROM abonnements a "
               "JOIN formules f ON f.id = a.formule_id WHERE a.membre_id = ? ")
    aujourdhui_iso = aujourdhui.isoformat()
    etat = {"etat": "aucun", "formule": None, "date_debut": None, "date_fin": None,
            "duree_jours": None, "jours_restants": None, "jours_consommes": None, "progression": None}

    # 1) Abonnement en cours à la date du jour
    ligne = db.execute(requete + "AND a.date_debut <= ? AND a.date_fin >= ? ORDER BY a.date_fin DESC LIMIT 1",
                       (membre_id, aujourdhui_iso, aujourdhui_iso)).fetchone()
    if ligne:
        debut, fin = date.fromisoformat(ligne["date_debut"]), date.fromisoformat(ligne["date_fin"])
        duree = (fin - debut).days + 1
        restants = (fin - aujourdhui).days
        etat.update(etat="en_cours", duree_jours=duree, jours_restants=restants,
                    jours_consommes=duree - restants,
                    progression=round(100 * (duree - restants) / duree))
    else:
        # 2) Sinon un abonnement qui commence plus tard
        ligne = db.execute(requete + "AND a.date_debut > ? ORDER BY a.date_debut LIMIT 1",
                           (membre_id, aujourdhui_iso)).fetchone()
        if ligne:
            etat["etat"] = "a_venir"
        else:
            # 3) Sinon le dernier abonnement, déjà terminé
            ligne = db.execute(requete + "AND a.date_fin < ? ORDER BY a.date_fin DESC LIMIT 1",
                               (membre_id, aujourdhui_iso)).fetchone()
            if ligne:
                etat["etat"] = "expire"
    if ligne:
        etat.update(formule=ligne["formule"], date_debut=ligne["date_debut"], date_fin=ligne["date_fin"])
    return etat


# ---------------------------------------------------------------------------
# Réservations : règles de gestion RG3 à RG8
# ---------------------------------------------------------------------------
def compter_places_prises(db, cours_id, jour_iso):
    """Nombre de réservations déjà enregistrées pour une séance (cours + date)."""
    return db.execute("SELECT COUNT(*) FROM reservations_cours WHERE cours_id = ? AND date_seance = ?",
                      (cours_id, jour_iso)).fetchone()[0]


def reserver_place(db, membre_id, cours_id_texte, date_texte, maintenant):
    """Enregistre la réservation d'un membre pour une séance (cours + date).

    Renvoie (réussite, message à afficher). Toutes les règles sont vérifiées ici, côté serveur,
    dans l'ordre le plus utile à l'utilisateur ; la base de données les revérifie ensuite
    (contrainte UNIQUE et déclencheurs) comme dernier filet de sécurité."""
    # 1) Les valeurs reçues sont du texte venant du navigateur : on les contrôle
    try:
        cours_id = int(cours_id_texte)
    except (TypeError, ValueError):
        return False, "Demande invalide : séance inconnue."
    jour = lire_date(date_texte)
    if jour is None:
        return False, "Demande invalide : date incorrecte."

    # 2) Le cours existe
    cours = db.execute("SELECT id, discipline, jour_semaine, heure, places_max FROM cours WHERE id = ?",
                       (cours_id,)).fetchone()
    if cours is None:
        return False, "Ce cours n’existe pas."

    # 3) Date de la séance (RG7)
    aujourdhui = maintenant.date()
    if jour < aujourdhui:
        return False, "Cette séance est déjà passée."
    if jour > aujourdhui + timedelta(days=DELAI_RESERVATION_JOURS):
        return False, f"Vous pouvez réserver au plus {DELAI_RESERVATION_JOURS} jours à l’avance."
    if jour.weekday() != cours["jour_semaine"]:
        return False, f"La date choisie ne correspond pas au jour de ce cours ({JOURS[cours['jour_semaine']].lower()})."
    if debut_seance(jour.isoformat(), cours["heure"]) <= maintenant:
        return False, "Cette séance a déjà commencé."

    libelle = f"{cours['discipline']} le {filtre_jour_fr(jour.isoformat()).lower()} à {cours['heure']}"

    # 4) Abonnement valide à la date de la SÉANCE, et non à la date de la réservation (RG3, RG4)
    abonnement = db.execute(
        "SELECT 1 FROM abonnements WHERE membre_id = ? AND date_debut <= ? AND date_fin >= ?",
        (membre_id, jour.isoformat(), jour.isoformat())).fetchone()
    if abonnement is None:
        return False, (f"Votre abonnement n’est pas valide le {filtre_date_fr(jour.isoformat())}. "
                       "Renouvelez-le à l’accueil de la salle pour réserver.")

    # 5) Une seule réservation par membre, cours et date (RG5)
    deja = db.execute("SELECT 1 FROM reservations_cours WHERE membre_id = ? AND cours_id = ? AND date_seance = ?",
                      (membre_id, cours_id, jour.isoformat())).fetchone()
    if deja:
        return False, f"Vous avez déjà réservé cette séance ({libelle})."

    # 6) Capacité (RG6)
    if compter_places_prises(db, cours_id, jour.isoformat()) >= cours["places_max"]:
        return False, f"Cette séance est complète ({libelle})."

    # 7) Enregistrement. Si deux membres prennent la dernière place en même temps, la base refuse
    # le second : on le signale sans planter.
    try:
        db.execute("INSERT INTO reservations_cours (cours_id, membre_id, date_seance) VALUES (?, ?, ?)",
                   (cours_id, membre_id, jour.isoformat()))
        db.commit()
    except sqlite3.IntegrityError:
        db.rollback()
        return False, "La réservation n’a pas pu être enregistrée : la place vient peut-être d’être prise. Réessayez."
    return True, f"Réservation confirmée : {libelle}."


def annuler_reservation(db, membre_id, reservation_id, maintenant):
    """Annule une réservation du membre, jusqu'à l'heure de début de la séance (RG8).
    Renvoie (réussite, message). Le membre ne peut annuler que SES réservations."""
    ligne = db.execute(
        "SELECT r.id, r.date_seance, c.discipline, c.heure FROM reservations_cours r "
        "JOIN cours c ON c.id = r.cours_id WHERE r.id = ? AND r.membre_id = ?",
        (reservation_id, membre_id)).fetchone()
    if ligne is None:      # même message si la réservation n'existe pas ou appartient à quelqu'un d'autre
        return False, "Réservation introuvable."
    if debut_seance(ligne["date_seance"], ligne["heure"]) <= maintenant:
        return False, "La séance a déjà commencé : l’annulation n’est plus possible."
    db.execute("DELETE FROM reservations_cours WHERE id = ? AND membre_id = ?", (reservation_id, membre_id))
    db.commit()
    libelle = f"{ligne['discipline']} le {filtre_jour_fr(ligne['date_seance']).lower()} à {ligne['heure']}"
    return True, f"Votre réservation est annulée : {libelle}."


def lister_reservations_a_venir(db, membre_id, maintenant):
    """Réservations à venir d'un membre. Chaque ligne indique si elle est encore annulable
    (la séance n'a pas commencé) : le gabarit n'affichera le bouton « Annuler » que dans ce cas."""
    lignes = db.execute(
        "SELECT r.id, r.date_seance, c.heure, c.discipline, c.coach, c.salle, c.places_max, "
        "  (SELECT COUNT(*) FROM reservations_cours x WHERE x.cours_id = r.cours_id "
        "   AND x.date_seance = r.date_seance) AS places_prises "
        "FROM reservations_cours r JOIN cours c ON c.id = r.cours_id "
        "WHERE r.membre_id = ? AND r.date_seance >= ? ORDER BY r.date_seance, c.heure",
        (membre_id, maintenant.date().isoformat())).fetchall()
    resultat = []
    for ligne in lignes:
        reservation = dict(ligne)
        reservation["annulable"] = debut_seance(ligne["date_seance"], ligne["heure"]) > maintenant
        resultat.append(reservation)
    return resultat


def historique_reservations(db, membre_id, maintenant, limite=50):
    """Séances déjà commencées ou terminées d'un membre, les plus récentes d'abord."""
    lignes = db.execute(
        "SELECT r.id, r.date_seance, c.heure, c.discipline, c.coach, c.salle, c.places_max, "
        "  (SELECT COUNT(*) FROM reservations_cours x WHERE x.cours_id = r.cours_id "
        "   AND x.date_seance = r.date_seance) AS places_prises "
        "FROM reservations_cours r JOIN cours c ON c.id = r.cours_id "
        "WHERE r.membre_id = ? AND r.date_seance <= ? ORDER BY r.date_seance DESC, c.heure DESC",
        (membre_id, maintenant.date().isoformat())).fetchall()
    commencees = [dict(ligne, annulable=False) for ligne in lignes
                  if debut_seance(ligne["date_seance"], ligne["heure"]) <= maintenant]
    return commencees[:limite]


def resume_membre(db, membre_id, maintenant):
    """Trois compteurs du tableau de bord : séances à venir, séances de la semaine, séances suivies."""
    lignes = db.execute(
        "SELECT r.date_seance, c.heure FROM reservations_cours r JOIN cours c ON c.id = r.cours_id "
        "WHERE r.membre_id = ?", (membre_id,)).fetchall()
    lundi = lundi_de(maintenant.date()).isoformat()
    dimanche = (lundi_de(maintenant.date()) + timedelta(days=6)).isoformat()
    a_venir = cette_semaine = passees = 0
    for ligne in lignes:
        if debut_seance(ligne["date_seance"], ligne["heure"]) > maintenant:
            a_venir += 1
        else:
            passees += 1
        if lundi <= ligne["date_seance"] <= dimanche:
            cette_semaine += 1
    return {"a_venir": a_venir, "cette_semaine": cette_semaine, "passees": passees}


def lister_abonnements_membre(db, membre_id, aujourdhui):
    """Tous les abonnements d'un membre, le plus récent d'abord, avec leur état."""
    jour = aujourdhui.isoformat()
    lignes = db.execute(
        "SELECT a.id, f.nom AS formule, a.date_debut, a.date_fin, a.montant_paye FROM abonnements a "
        "JOIN formules f ON f.id = a.formule_id WHERE a.membre_id = ? ORDER BY a.date_debut DESC",
        (membre_id,)).fetchall()
    resultat = []
    for ligne in lignes:
        etat = "expire" if ligne["date_fin"] < jour else ("a_venir" if ligne["date_debut"] > jour else "en_cours")
        resultat.append(dict(ligne, etat=etat))
    return resultat


# ---------------------------------------------------------------------------
# Gestion des membres et des abonnements (espace gérant) : règles RG1, RG2, RG9, RG11
# ---------------------------------------------------------------------------
ALPHABET_CODE = "23456789ABCDEFGHJKMNPQRSTUVWXYZ"   # sans 0, O, 1, I, L : évite les confusions à la lecture
LONGUEUR_CODE = 6
DELAI_EXPIRATION_JOURS = 7          # « expire bientôt » : fin dans les 7 prochains jours (RG11)
DEBUT_MAXIMUM_JOURS = 366           # un abonnement ne commence pas plus d'un an à l'avance
MONTANT_MAXIMUM = 10_000_000
FILTRES_ABONNEMENTS = ("tous", "en_cours", "expirant", "expires")


def generer_code_acces():
    """Génère un code d'accès lisible et imprévisible (module secrets, adapté à la sécurité)."""
    return "".join(secrets.choice(ALPHABET_CODE) for _ in range(LONGUEUR_CODE))


def echapper_like(texte):
    """Empêche les caractères % et _ saisis par l'utilisateur d'agir comme jokers dans un LIKE."""
    return texte.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def creer_membre(db, nom_saisi, telephone_saisi, aujourdhui):
    """Enregistre un nouveau membre et génère son code d'accès (RG1).
    Renvoie (réussite, message, membre). Le code n'existe en clair que dans le résultat : la base
    ne garde que son empreinte, et le gérant le remet au membre."""
    nom = " ".join((nom_saisi or "").split())             # espaces multiples et retours à la ligne nettoyés
    if not 2 <= len(nom) <= 80 or not nom.isprintable():
        return False, "Le nom doit contenir entre 2 et 80 caractères.", None
    telephone = normaliser_telephone(telephone_saisi)
    if telephone is None:
        return False, "Numéro invalide : saisissez 9 chiffres commençant par 6.", None
    if db.execute("SELECT 1 FROM membres WHERE telephone = ?", (telephone,)).fetchone():
        return False, "Un membre est déjà enregistré avec ce numéro.", None

    code = generer_code_acces()
    try:
        curseur = db.execute(
            "INSERT INTO membres (nom, telephone, date_inscription, empreinte_code) VALUES (?, ?, ?, ?)",
            (nom, telephone, aujourdhui.isoformat(), generate_password_hash(code)))
        db.commit()
    except sqlite3.IntegrityError:                         # deux enregistrements simultanés du même numéro
        db.rollback()
        return False, "Un membre est déjà enregistré avec ce numéro.", None
    return True, f"Membre enregistré : {nom}.", {
        "id": curseur.lastrowid, "nom": nom, "telephone": telephone, "code": code}


def reinitialiser_code(db, membre_id):
    """Génère un nouveau code d'accès pour un membre qui a oublié le sien. L'ancien code cesse de marcher."""
    membre = db.execute("SELECT id, nom, telephone FROM membres WHERE id = ?", (membre_id,)).fetchone()
    if membre is None:
        return False, "Membre introuvable.", None
    code = generer_code_acces()
    db.execute("UPDATE membres SET empreinte_code = ? WHERE id = ?", (generate_password_hash(code), membre_id))
    db.commit()
    return True, f"Nouveau code d’accès généré pour {membre['nom']}.", {
        "id": membre["id"], "nom": membre["nom"], "telephone": membre["telephone"], "code": code}


def lister_membres(db, recherche, aujourdhui):
    """Liste des membres, avec recherche facultative par nom ou téléphone.
    Chaque ligne indique si le membre est actif (abonnement en cours) et la date de fin de son dernier abonnement."""
    motif = "%" + echapper_like((recherche or "").strip()) + "%"
    jour = aujourdhui.isoformat()
    lignes = db.execute(
        "SELECT m.id, m.nom, m.telephone, m.date_inscription, "
        "  (SELECT MAX(a.date_fin) FROM abonnements a WHERE a.membre_id = m.id) AS derniere_fin, "
        "  EXISTS (SELECT 1 FROM abonnements a WHERE a.membre_id = m.id "
        "          AND a.date_debut <= ? AND a.date_fin >= ?) AS actif "
        "FROM membres m WHERE m.nom LIKE ? ESCAPE '\\' OR m.telephone LIKE ? ESCAPE '\\' "
        "ORDER BY m.nom COLLATE NOCASE, m.id", (jour, jour, motif, motif)).fetchall()
    return [dict(ligne, actif=bool(ligne["actif"])) for ligne in lignes]


def calculer_debut_par_defaut(db, membre_id, aujourdhui):
    """Date de début d'un nouvel abonnement quand le gérant ne la précise pas (RG9) : aujourd'hui, ou le
    lendemain de la fin du dernier abonnement en cours ou à venir (un renouvellement anticipé ne fait
    pas perdre de jours au membre, et deux abonnements ne se chevauchent jamais)."""
    fin = db.execute("SELECT MAX(date_fin) FROM abonnements WHERE membre_id = ? AND date_fin >= ?",
                     (membre_id, aujourdhui.isoformat())).fetchone()[0]
    if fin is None:
        return aujourdhui
    return max(aujourdhui, date.fromisoformat(fin) + timedelta(days=1))


def trouver_chevauchement(db, membre_id, debut, fin):
    """Renvoie l'abonnement du membre qui chevauche la période [debut, fin], ou None.
    Deux périodes se chevauchent si chacune commence avant (ou le jour de) la fin de l'autre."""
    return db.execute(
        "SELECT a.date_debut, a.date_fin, f.nom AS formule FROM abonnements a "
        "JOIN formules f ON f.id = a.formule_id "
        "WHERE a.membre_id = ? AND ? <= a.date_fin AND ? >= a.date_debut LIMIT 1",
        (membre_id, debut.isoformat(), fin.isoformat())).fetchone()


def verifier_abonnement(db, membre_id_texte, formule_id_texte, debut_texte, montant_texte, aujourdhui):
    """Contrôle les saisies d'un abonnement et calcule ses dates et son montant (RG2, RG9).
    Renvoie (message d'erreur ou None, valeurs ou None). N'écrit rien dans la base."""
    try:
        membre_id = int(membre_id_texte)
    except (TypeError, ValueError):
        return "Choisissez un membre.", None
    membre = db.execute("SELECT id, nom FROM membres WHERE id = ?", (membre_id,)).fetchone()
    if membre is None:
        return "Ce membre n’existe pas.", None

    try:
        formule_id = int(formule_id_texte)
    except (TypeError, ValueError):
        return "Choisissez une formule.", None
    formule = db.execute("SELECT id, nom, duree_jours, prix FROM formules WHERE id = ?", (formule_id,)).fetchone()
    if formule is None:
        return "Cette formule n’existe pas.", None

    # Date de début : vide = calculée automatiquement (renouvellement à la fin de l'abonnement actuel)
    debut_texte = (debut_texte or "").strip()
    if debut_texte == "":
        debut = calculer_debut_par_defaut(db, membre_id, aujourdhui)
    else:
        debut = lire_date(debut_texte)
        if debut is None:
            return "Date de début invalide : utilisez le format AAAA-MM-JJ.", None
        if debut < aujourdhui:
            return "L’abonnement ne peut pas commencer avant aujourd’hui.", None
        if debut > aujourdhui + timedelta(days=DEBUT_MAXIMUM_JOURS):
            return "L’abonnement ne peut pas commencer plus d’un an à l’avance.", None
    fin = debut + timedelta(days=formule["duree_jours"] - 1)       # la date de fin est incluse (RG2)

    # Montant : vide = prix de la formule ; sinon un nombre entier de FCFA (remise possible)
    montant_texte = (montant_texte or "").strip()
    if montant_texte == "":
        montant = formule["prix"]
    elif re.fullmatch(r"[0-9]{1,8}", montant_texte) and int(montant_texte) <= MONTANT_MAXIMUM:
        montant = int(montant_texte)
    else:
        return "Montant invalide : saisissez un nombre entier de FCFA, sans signe ni espace.", None

    existant = trouver_chevauchement(db, membre_id, debut, fin)
    if existant:
        suggestion = calculer_debut_par_defaut(db, membre_id, aujourdhui)
        return (f"Cet abonnement chevauche l’abonnement {existant['formule']} de {membre['nom']} "
                f"du {filtre_date_fr(existant['date_debut'])} au {filtre_date_fr(existant['date_fin'])}. "
                f"Laissez la date de début vide : il commencera le {filtre_date_fr(suggestion.isoformat())}."), None

    return None, {"membre_id": membre["id"], "nom": membre["nom"], "formule_id": formule["id"],
                  "formule": formule["nom"], "duree_jours": formule["duree_jours"],
                  "date_debut": debut.isoformat(), "date_fin": fin.isoformat(), "montant": montant,
                  "debut_automatique": debut_texte == ""}


def enregistrer_abonnement(db, membre_id_texte, formule_id_texte, debut_texte, montant_texte, aujourdhui):
    """Enregistre un abonnement ou un renouvellement. Renvoie (réussite, message, valeurs)."""
    erreur, valeurs = verifier_abonnement(db, membre_id_texte, formule_id_texte, debut_texte,
                                          montant_texte, aujourdhui)
    if erreur:
        return False, erreur, None
    try:
        curseur = db.execute(
            "INSERT INTO abonnements (membre_id, formule_id, date_debut, date_fin, montant_paye) "
            "VALUES (?, ?, ?, ?, ?)",
            (valeurs["membre_id"], valeurs["formule_id"], valeurs["date_debut"], valeurs["date_fin"],
             valeurs["montant"]))
        db.commit()
    except sqlite3.IntegrityError:        # filet de sécurité : le déclencheur de la base refuse un chevauchement
        db.rollback()
        return False, "L’abonnement n’a pas pu être enregistré : il chevauche un abonnement existant.", None
    valeurs["id"] = curseur.lastrowid
    message = (f"Abonnement {valeurs['formule']} enregistré pour {valeurs['nom']} : "
               f"du {filtre_date_fr(valeurs['date_debut'])} au {filtre_date_fr(valeurs['date_fin'])}.")
    if valeurs["debut_automatique"] and valeurs["date_debut"] > aujourdhui.isoformat():
        message += " Il commence à la fin de l’abonnement actuel."
    return True, message, valeurs


def abonnements_expirant(db, aujourdhui, jours=DELAI_EXPIRATION_JOURS):
    """Abonnements qui se terminent dans les `jours` prochains jours (aujourd'hui compris), RG11.
    Un abonnement déjà suivi d'un renouvellement n'est pas compté : le membre n'a rien à renouveler."""
    return db.execute(
        "SELECT a.id, m.id AS membre_id, m.nom, m.telephone, f.nom AS formule, a.date_fin, "
        "       CAST(julianday(a.date_fin) - julianday(?) AS INTEGER) AS jours_restants "
        "FROM abonnements a JOIN membres m ON m.id = a.membre_id JOIN formules f ON f.id = a.formule_id "
        "WHERE a.date_fin BETWEEN ? AND ? "
        "  AND NOT EXISTS (SELECT 1 FROM abonnements b WHERE b.membre_id = a.membre_id AND b.date_debut > a.date_fin) "
        "ORDER BY a.date_fin, m.nom",
        (aujourdhui.isoformat(), aujourdhui.isoformat(),
         (aujourdhui + timedelta(days=jours)).isoformat())).fetchall()


def lister_abonnements(db, aujourdhui, filtre="tous"):
    """Liste des abonnements avec leur état (en_cours, a_venir, expire) et un indicateur « expire bientôt ».
    Filtres : tous, en_cours, expirant (expire bientôt), expires."""
    jour = aujourdhui.isoformat()
    bientot = {ligne["id"] for ligne in abonnements_expirant(db, aujourdhui)}
    lignes = db.execute(
        "SELECT a.id, m.id AS membre_id, m.nom, m.telephone, f.nom AS formule, a.date_debut, a.date_fin, "
        "       a.montant_paye FROM abonnements a JOIN membres m ON m.id = a.membre_id "
        "JOIN formules f ON f.id = a.formule_id ORDER BY a.date_debut DESC, a.id DESC").fetchall()
    resultat = []
    for ligne in lignes:
        if ligne["date_fin"] < jour:
            etat = "expire"
        elif ligne["date_debut"] > jour:
            etat = "a_venir"
        else:
            etat = "en_cours"
        abonnement = dict(ligne, etat=etat, expire_bientot=ligne["id"] in bientot)
        if (filtre == "en_cours" and etat != "en_cours") or (filtre == "expires" and etat != "expire") \
                or (filtre == "expirant" and not abonnement["expire_bientot"]):
            continue
        resultat.append(abonnement)
    return resultat


# ---------------------------------------------------------------------------
# Gestion des cours et des inscrits (espace gérant)
# ---------------------------------------------------------------------------
HEURE_VALIDE = re.compile(r"(?:[01][0-9]|2[0-3]):[0-5][0-9]")
PLACES_MAXIMUM = 200
CHAMPS_COURS = ("discipline", "coach", "jour_semaine", "heure", "salle", "places_max")


def lire_entier(texte, minimum, maximum):
    """Convertit un texte en entier (chiffres 0 à 9 uniquement) ; renvoie None s'il est invalide ou hors limites."""
    if not isinstance(texte, str) or not re.fullmatch(r"[0-9]{1,6}", texte.strip()):
        return None
    valeur = int(texte.strip())
    return valeur if minimum <= valeur <= maximum else None


def nettoyer_texte(texte, minimum, maximum):
    """Supprime les espaces en trop ; renvoie None si la longueur est hors limites ou si le texte
    contient des caractères de contrôle."""
    if not isinstance(texte, str):
        return None
    texte = " ".join(texte.split())
    return texte if minimum <= len(texte) <= maximum and texte.isprintable() else None


def verifier_cours(db, saisie, aujourdhui, cours_id=None):
    """Contrôle les saisies d'un cours (création si cours_id est None, sinon modification).
    Renvoie (message d'erreur ou None, valeurs nettoyées ou None). N'écrit rien dans la base."""
    discipline = nettoyer_texte(saisie.get("discipline"), 2, 40)
    if discipline is None:
        return "La discipline doit contenir entre 2 et 40 caractères.", None
    coach = nettoyer_texte(saisie.get("coach"), 2, 60)
    if coach is None:
        return "Le nom du coach doit contenir entre 2 et 60 caractères.", None
    jour_semaine = lire_entier(saisie.get("jour_semaine"), 0, 6)
    if jour_semaine is None:
        return "Choisissez un jour de la semaine.", None
    heure = (saisie.get("heure") or "").strip() if isinstance(saisie.get("heure"), str) else ""
    if not HEURE_VALIDE.fullmatch(heure):
        return "L’heure doit être au format HH:MM, par exemple 18:30.", None
    salle = nettoyer_texte(saisie.get("salle"), 1, 30)
    if salle is None:
        return "Le nom de la salle doit contenir entre 1 et 30 caractères.", None
    places = lire_entier(saisie.get("places_max"), 1, PLACES_MAXIMUM)
    if places is None:
        return f"Le nombre de places doit être un entier entre 1 et {PLACES_MAXIMUM}.", None

    # Une salle ne peut pas accueillir deux cours au même moment
    conflit = db.execute(
        "SELECT discipline FROM cours WHERE salle = ? COLLATE NOCASE AND jour_semaine = ? AND heure = ? "
        "AND id IS NOT ?", (salle, jour_semaine, heure, cours_id)).fetchone()
    if conflit:
        return (f"{salle} est déjà utilisée le {JOURS[jour_semaine].lower()} à {heure} "
                f"par le cours {conflit['discipline']}."), None

    if cours_id is not None:
        actuel = db.execute("SELECT jour_semaine, heure FROM cours WHERE id = ?", (cours_id,)).fetchone()
        jour_iso = aujourdhui.isoformat()
        a_venir = db.execute("SELECT COUNT(*) FROM reservations_cours WHERE cours_id = ? AND date_seance >= ?",
                             (cours_id, jour_iso)).fetchone()[0]
        # Déplacer un cours qui a des réservations à venir les rendrait fausses (jour ou heure différents)
        if a_venir and (actuel["jour_semaine"], actuel["heure"]) != (jour_semaine, heure):
            return (f"Ce cours a {a_venir} réservation(s) à venir : son jour et son heure ne peuvent plus "
                    "changer. Créez un nouveau cours à la place."), None
        # Réduire les places sous le nombre de réservations déjà prises serait incohérent
        plus_remplie = db.execute(
            "SELECT date_seance, COUNT(*) AS nombre FROM reservations_cours WHERE cours_id = ? AND date_seance >= ? "
            "GROUP BY date_seance ORDER BY nombre DESC LIMIT 1", (cours_id, jour_iso)).fetchone()
        if plus_remplie and places < plus_remplie["nombre"]:
            return (f"Impossible de réduire à {places} places : la séance du {filtre_date_fr(plus_remplie['date_seance'])} "
                    f"compte déjà {plus_remplie['nombre']} réservations."), None

    return None, {"discipline": discipline, "coach": coach, "jour_semaine": jour_semaine, "heure": heure,
                  "salle": salle, "places_max": places}


def creer_cours(db, saisie, aujourdhui):
    """Ajoute un cours hebdomadaire au planning. Renvoie (réussite, message, id du cours)."""
    erreur, valeurs = verifier_cours(db, saisie, aujourdhui)
    if erreur:
        return False, erreur, None
    try:
        curseur = db.execute(
            "INSERT INTO cours (discipline, coach, jour_semaine, heure, salle, places_max) VALUES (?, ?, ?, ?, ?, ?)",
            (valeurs["discipline"], valeurs["coach"], valeurs["jour_semaine"], valeurs["heure"],
             valeurs["salle"], valeurs["places_max"]))
        db.commit()
    except sqlite3.IntegrityError:        # filet de sécurité : contrainte UNIQUE (salle, jour, heure) de la base
        db.rollback()
        return False, "Le cours n’a pas pu être ajouté : cette salle est déjà utilisée à ce moment.", None
    return True, (f"Cours ajouté : {valeurs['discipline']}, {JOURS[valeurs['jour_semaine']].lower()} "
                  f"à {valeurs['heure']}."), curseur.lastrowid


def modifier_cours(db, cours_id, saisie, aujourdhui):
    """Modifie un cours existant. Renvoie (réussite, message)."""
    if db.execute("SELECT 1 FROM cours WHERE id = ?", (cours_id,)).fetchone() is None:
        return False, "Ce cours n’existe pas."
    erreur, valeurs = verifier_cours(db, saisie, aujourdhui, cours_id)
    if erreur:
        return False, erreur
    try:
        db.execute(
            "UPDATE cours SET discipline = ?, coach = ?, jour_semaine = ?, heure = ?, salle = ?, places_max = ? "
            "WHERE id = ?", (valeurs["discipline"], valeurs["coach"], valeurs["jour_semaine"], valeurs["heure"],
                             valeurs["salle"], valeurs["places_max"], cours_id))
        db.commit()
    except sqlite3.IntegrityError:
        db.rollback()
        return False, "Le cours n’a pas pu être modifié : cette salle est déjà utilisée à ce moment."
    return True, f"Cours modifié : {valeurs['discipline']}, {JOURS[valeurs['jour_semaine']].lower()} à {valeurs['heure']}."


def supprimer_cours(db, cours_id):
    """Supprime un cours qui n'a jamais eu de réservation. Renvoie (réussite, message).
    Un cours qui a un historique de réservations est conservé : il faut le modifier à la place."""
    cours = db.execute("SELECT discipline, jour_semaine, heure FROM cours WHERE id = ?", (cours_id,)).fetchone()
    if cours is None:
        return False, "Ce cours n’existe pas."
    nombre = db.execute("SELECT COUNT(*) FROM reservations_cours WHERE cours_id = ?", (cours_id,)).fetchone()[0]
    if nombre:
        return False, (f"Ce cours compte {nombre} réservation(s), passées ou à venir : il ne peut pas être supprimé. "
                       "Modifiez-le plutôt.")
    try:
        db.execute("DELETE FROM cours WHERE id = ?", (cours_id,))
        db.commit()
    except sqlite3.IntegrityError:        # une réservation est apparue entre-temps : la clé étrangère protège
        db.rollback()
        return False, "Ce cours vient de recevoir une réservation : il ne peut pas être supprimé."
    return True, f"Cours supprimé : {cours['discipline']}, {JOURS[cours['jour_semaine']].lower()} à {cours['heure']}."


def lister_cours_admin(db, aujourdhui):
    """Planning hebdomadaire pour le gérant, avec le nombre de réservations à venir de chaque cours."""
    lignes = db.execute(
        "SELECT c.id, c.discipline, c.coach, c.jour_semaine, c.heure, c.salle, c.places_max, "
        "  (SELECT COUNT(*) FROM reservations_cours r WHERE r.cours_id = c.id AND r.date_seance >= ?) "
        "    AS reservations_a_venir "
        "FROM cours c ORDER BY c.jour_semaine, c.heure, c.salle", (aujourdhui.isoformat(),)).fetchall()
    return [dict(ligne, jour_nom=JOURS[ligne["jour_semaine"]],
                 prochaine_seance=(aujourdhui + timedelta(days=(ligne["jour_semaine"] - aujourdhui.weekday()) % 7)).isoformat())
            for ligne in lignes]


def dates_autour(jour_semaine, aujourdhui, avant=2, apres=4):
    """Dates d'un cours : les `avant` dernières séances et les `apres` prochaines (aujourd'hui compris),
    pour proposer au gérant les séances dont il peut consulter les inscrits."""
    prochaine = aujourdhui + timedelta(days=(jour_semaine - aujourdhui.weekday()) % 7)
    return [(prochaine + timedelta(weeks=k)).isoformat() for k in range(-avant, apres)]


def inscrits_seance(db, cours_id_texte, date_texte, aujourdhui):
    """Liste des membres inscrits à une séance (cours + date). Renvoie (message d'erreur ou None, contexte)."""
    try:
        cours_id = int(cours_id_texte)
    except (TypeError, ValueError):
        return "Choisissez un cours.", None
    cours = db.execute("SELECT id, discipline, coach, jour_semaine, heure, salle, places_max FROM cours WHERE id = ?",
                       (cours_id,)).fetchone()
    if cours is None:
        return "Ce cours n’existe pas.", None
    jour = lire_date(date_texte)
    if jour is None:
        return "Date invalide : utilisez le format AAAA-MM-JJ.", None
    if jour.weekday() != cours["jour_semaine"]:
        return f"La date choisie ne correspond pas au jour de ce cours ({JOURS[cours['jour_semaine']].lower()}).", None

    inscrits = db.execute(
        "SELECT m.id, m.nom, m.telephone, r.date_reservation FROM reservations_cours r "
        "JOIN membres m ON m.id = r.membre_id WHERE r.cours_id = ? AND r.date_seance = ? "
        "ORDER BY r.date_reservation, m.nom", (cours_id, jour.isoformat())).fetchall()
    return None, {
        "cours": dict(cours, jour_nom=JOURS[cours["jour_semaine"]]),
        "jour": jour.isoformat(),
        "passee": jour < aujourdhui,
        "inscrits": [dict(ligne) for ligne in inscrits],
        "places_prises": len(inscrits),
        "places_restantes": max(cours["places_max"] - len(inscrits), 0),
        "dates": dates_autour(cours["jour_semaine"], aujourdhui),
    }


# ---------------------------------------------------------------------------
# Statistiques du gérant
# ---------------------------------------------------------------------------
def recette_par_mois(db, mois_max=12):
    """Recette par mois et par formule : somme de montant_paye, groupée par mois de date_debut
    (le paiement est supposé fait le jour du début). Les mois les plus récents d'abord."""
    lignes = db.execute(
        "SELECT substr(a.date_debut, 1, 7) AS mois, f.nom AS formule, SUM(a.montant_paye) AS recette, "
        "COUNT(*) AS nombre FROM abonnements a JOIN formules f ON f.id = a.formule_id "
        "GROUP BY mois, f.id ORDER BY mois DESC, f.duree_jours").fetchall()
    mois = {}
    for ligne in lignes:
        if ligne["mois"] not in mois:
            if len(mois) >= mois_max:
                break
            mois[ligne["mois"]] = {"mois": ligne["mois"], "total": 0, "formules": []}
        mois[ligne["mois"]]["formules"].append(
            {"formule": ligne["formule"], "recette": ligne["recette"], "nombre": ligne["nombre"]})
        mois[ligne["mois"]]["total"] += ligne["recette"]
    return list(mois.values())


def remplissage_des_cours(db, aujourdhui, semaines=4):
    """Taux de remplissage de chaque cours sur les `semaines` dernières semaines (aujourd'hui exclu) :
    réservations enregistrées ÷ (places maximales × nombre de séances). Chaque cours a exactement une
    séance par semaine. Une réservation annulée est supprimée : elle ne compte plus."""
    debut = (aujourdhui - timedelta(weeks=semaines)).isoformat()
    fin = (aujourdhui - timedelta(days=1)).isoformat()
    lignes = db.execute(
        "SELECT c.id, c.discipline, c.jour_semaine, c.heure, c.salle, c.places_max, "
        "  (SELECT COUNT(*) FROM reservations_cours r WHERE r.cours_id = c.id "
        "   AND r.date_seance BETWEEN ? AND ?) AS reservations FROM cours c", (debut, fin)).fetchall()
    resultat = []
    for ligne in lignes:
        capacite = ligne["places_max"] * semaines
        resultat.append({
            "cours_id": ligne["id"], "discipline": ligne["discipline"], "jour": JOURS[ligne["jour_semaine"]],
            "heure": ligne["heure"], "salle": ligne["salle"], "places_max": ligne["places_max"],
            "seances": semaines, "reservations": ligne["reservations"],
            "taux": round(100 * ligne["reservations"] / capacite, 1)})
    return sorted(resultat, key=lambda c: (-c["taux"], c["cours_id"]))


def compter_membres_actifs(db, aujourdhui):
    """Membres ayant un abonnement valide aujourd'hui, et leur part parmi tous les membres."""
    jour = aujourdhui.isoformat()
    actifs = db.execute("SELECT COUNT(DISTINCT membre_id) FROM abonnements WHERE date_debut <= ? AND date_fin >= ?",
                        (jour, jour)).fetchone()[0]
    total = db.execute("SELECT COUNT(*) FROM membres").fetchone()[0]
    return {"actifs": actifs, "total": total, "taux": round(100 * actifs / total, 1) if total else None}


def taux_renouvellement(db, aujourdhui):
    """Part des abonnements arrivés à échéance dont le membre a ensuite pris un nouvel abonnement.
    Les abonnements « à la séance » (1 jour) sont exclus : ils prennent fin aussitôt et fausseraient le taux."""
    ligne = db.execute(
        "SELECT COUNT(*) AS echus, COALESCE(SUM(EXISTS (SELECT 1 FROM abonnements b "
        "  WHERE b.membre_id = a.membre_id AND b.date_debut > a.date_fin)), 0) AS renouveles "
        "FROM abonnements a JOIN formules f ON f.id = a.formule_id "
        "WHERE a.date_fin < ? AND f.duree_jours > 1", (aujourdhui.isoformat(),)).fetchone()
    return {"echus": ligne["echus"], "renouveles": ligne["renouveles"],
            "taux": round(100 * ligne["renouveles"] / ligne["echus"], 1) if ligne["echus"] else None}


def statistiques(db, aujourdhui):
    """Les quatre statistiques du gérant, prêtes à être affichées ou renvoyées en JSON."""
    return {"date": aujourdhui.isoformat(),
            "recette": recette_par_mois(db),
            "remplissage": remplissage_des_cours(db, aujourdhui),
            "membres": compter_membres_actifs(db, aujourdhui),
            "renouvellement": taux_renouvellement(db, aujourdhui)}


# ---------------------------------------------------------------------------
# Pages publiques
# ---------------------------------------------------------------------------
def charger_formules(db):
    """Formules avec prix par jour et indicateur « populaire » (la plus souscrite parmi les abonnements
    de plus d'un jour). Utilisé par l'accueil et la page des formules."""
    lignes = db.execute(
        "SELECT f.id, f.nom, f.duree_jours, f.prix, "
        "  (SELECT COUNT(*) FROM abonnements a WHERE a.formule_id = f.id) AS nombre "
        "FROM formules f ORDER BY f.duree_jours").fetchall()
    formules = [dict(ligne, prix_par_jour=round(ligne["prix"] / ligne["duree_jours"]) if ligne["duree_jours"] > 1 else None,
                     populaire=False) for ligne in lignes]
    candidates = [f for f in formules if f["duree_jours"] > 1 and f["nombre"] > 0]
    if candidates:
        max(candidates, key=lambda f: f["nombre"])["populaire"] = True
    return formules


@app.route("/")
def accueil():
    """Accueil : présentation, formules d'abonnement (prix lus dans la base) et explication pour s'abonner.
    Données transmises : formules (liste de dictionnaires), disciplines (liste de textes)."""
    db = get_db()
    disciplines = [ligne["discipline"] for ligne in db.execute(
        "SELECT DISTINCT discipline FROM cours ORDER BY discipline COLLATE NOCASE")]
    return render_template("accueil.html", formules=charger_formules(db), disciplines=disciplines)


@app.route("/formules")
def formules():
    """Page des formules d'abonnement."""
    return render_template("formules.html", formules=charger_formules(get_db()))


@app.route("/planning")
def planning():
    """Planning de la semaine : la grille est chargée par JavaScript (fetch sur /api/planning)."""
    return render_template("planning.html")


# ---------------------------------------------------------------------------
# Connexion et déconnexion
# ---------------------------------------------------------------------------
@app.route("/connexion", methods=["GET", "POST"])
def connexion():
    """Connexion d'un membre avec son numéro de téléphone et son code d'accès."""
    if g.membre:
        return redirect(url_for("espace_membre"))

    erreur = None
    telephone_saisi = ""
    if request.method == "POST":
        telephone_saisi = request.form.get("telephone", "").strip()
        code = request.form.get("code", "")
        telephone = normaliser_telephone(telephone_saisi)
        if telephone is None or not code:
            erreur = "Saisissez un numéro valide (9 chiffres commençant par 6) et votre code d’accès."
        else:
            membre = get_db().execute(
                "SELECT id, nom, empreinte_code FROM membres WHERE telephone = ?", (telephone,)).fetchone()
            empreinte = membre["empreinte_code"] if membre else EMPREINTE_FACTICE
            if check_password_hash(empreinte, code) and membre:
                session.clear()                    # nouvelle session : évite de réutiliser une ancienne
                session["membre_id"] = membre["id"]
                flash(f"Bienvenue, {membre['nom']}.", "succes")
                return redirect(url_for("espace_membre"))
            # Même message dans les deux cas : on ne révèle pas si le numéro existe
            erreur = "Numéro de téléphone ou code d’accès incorrect."
    return render_template("connexion.html", mode="membre", erreur=erreur, telephone=telephone_saisi)


@app.route("/admin/connexion", methods=["GET", "POST"])
def admin_connexion():
    """Connexion du gérant avec son login et son mot de passe."""
    if g.admin:
        return redirect(url_for("admin_tableau_de_bord"))

    erreur = None
    login_saisi = ""
    if request.method == "POST":
        login_saisi = request.form.get("login", "").strip()
        mot_de_passe = request.form.get("mot_de_passe", "")
        if not login_saisi or not mot_de_passe:
            erreur = "Saisissez votre identifiant et votre mot de passe."
        else:
            admin = get_db().execute(
                "SELECT id, login, empreinte FROM administrateurs WHERE login = ?", (login_saisi,)).fetchone()
            empreinte = admin["empreinte"] if admin else EMPREINTE_FACTICE
            if check_password_hash(empreinte, mot_de_passe) and admin:
                session.clear()
                session["admin_id"] = admin["id"]
                flash("Connexion réussie.", "succes")
                return redirect(url_for("admin_tableau_de_bord"))
            erreur = "Identifiant ou mot de passe incorrect."
    return render_template("connexion.html", mode="admin", erreur=erreur, login=login_saisi)


@app.route("/deconnexion", methods=["POST"])
def deconnexion():
    """Déconnexion (en POST : une simple adresse ne doit pas pouvoir déconnecter quelqu'un)."""
    session.clear()
    flash("Vous êtes déconnecté.", "info")
    return redirect(url_for("accueil"))


# ---------------------------------------------------------------------------
# Espace membre
# ---------------------------------------------------------------------------
@app.route("/membre")
@membre_requis
def espace_membre():
    """Tableau de bord du membre. Données transmises : abonnement (dictionnaire), resume (compteurs),
    prochaines (3 prochaines séances), membre_depuis (date d'inscription)."""
    db = get_db()
    maintenant = datetime.now()
    a_venir = [r for r in lister_reservations_a_venir(db, g.membre["id"], maintenant) if r["annulable"]]
    inscription = db.execute("SELECT date_inscription FROM membres WHERE id = ?", (g.membre["id"],)).fetchone()[0]
    return render_template("membre.html", abonnement=etat_abonnement(db, g.membre["id"], maintenant.date()),
                           resume=resume_membre(db, g.membre["id"], maintenant), prochaines=a_venir[:3],
                           membre_depuis=inscription)


@app.route("/membre/reservations")
@membre_requis
def membre_reservations():
    """Réservations du membre en deux onglets (?onglet=a_venir ou historique).
    Données transmises : onglet, reservations (liste de dictionnaires, avec annulable), nombre_a_venir."""
    db = get_db()
    maintenant = datetime.now()
    a_venir = [r for r in lister_reservations_a_venir(db, g.membre["id"], maintenant) if r["annulable"]]
    onglet = "historique" if request.args.get("onglet") == "historique" else "a_venir"
    reservations = a_venir if onglet == "a_venir" else historique_reservations(db, g.membre["id"], maintenant)
    return render_template("membre_reservations.html", onglet=onglet, reservations=reservations,
                           nombre_a_venir=len(a_venir))


@app.route("/membre/abonnement")
@membre_requis
def membre_abonnement():
    """Détail de l'abonnement du membre et historique. Données transmises : abonnement, historique."""
    db = get_db()
    aujourdhui = date.today()
    return render_template("membre_abonnement.html", abonnement=etat_abonnement(db, g.membre["id"], aujourdhui),
                           historique=lister_abonnements_membre(db, g.membre["id"], aujourdhui))


@app.route("/reserver", methods=["POST"])
@membre_requis
def reserver():
    """Réserve une place. Champs du formulaire : cours_id et date_seance (AAAA-MM-JJ).
    Le membre est celui de la session, jamais un champ du formulaire."""
    reussite, message = reserver_place(get_db(), g.membre["id"], request.form.get("cours_id"),
                                       request.form.get("date_seance"), datetime.now())
    flash(message, "succes" if reussite else "erreur")
    return redirect(url_for("espace_membre" if reussite else "planning"))


@app.route("/annuler/<int:reservation_id>", methods=["POST"])
@membre_requis
def annuler(reservation_id):
    """Annule une réservation du membre connecté (en POST uniquement)."""
    reussite, message = annuler_reservation(get_db(), g.membre["id"], reservation_id, datetime.now())
    flash(message, "succes" if reussite else "erreur")
    return redirect(url_for("espace_membre"))


# ---------------------------------------------------------------------------
# Administration
# ---------------------------------------------------------------------------
@app.route("/admin")
@connexion_requise
def admin_tableau_de_bord():
    """Tableau de bord du gérant. Données transmises : membres_actifs, membres_total, expirations (nombre),
    expirants (5 premiers abonnements à renouveler), recette_mois, reservations_a_venir."""
    db = get_db()
    aujourdhui = date.today()
    jour = aujourdhui.isoformat()
    membres_actifs = db.execute(
        "SELECT COUNT(DISTINCT membre_id) FROM abonnements WHERE date_debut <= ? AND date_fin >= ?",
        (jour, jour)).fetchone()[0]
    membres_total = db.execute("SELECT COUNT(*) FROM membres").fetchone()[0]
    expirants = abonnements_expirant(db, aujourdhui)
    mois_courant = jour[:7]
    recette = next((m["total"] for m in recette_par_mois(db) if m["mois"] == mois_courant), 0)
    a_venir = db.execute("SELECT COUNT(*) FROM reservations_cours WHERE date_seance >= ?", (jour,)).fetchone()[0]
    return render_template("admin.html", membres_actifs=membres_actifs, membres_total=membres_total,
                           expirations=len(expirants), expirants=[dict(e) for e in expirants[:5]],
                           recette_mois=recette, reservations_a_venir=a_venir)


def page_sans_cache(gabarit, **contexte):
    """Affiche une page qui contient un code d'accès : le navigateur ne doit pas la garder en mémoire."""
    reponse = make_response(render_template(gabarit, **contexte))
    reponse.headers["Cache-Control"] = "no-store"
    return reponse


@app.route("/admin/membres")
@connexion_requise
def admin_membres():
    """Liste des membres avec recherche (?q=). Données transmises : membres (liste de dictionnaires), recherche."""
    recherche = request.args.get("q", "").strip()[:50]
    membres = lister_membres(get_db(), recherche, date.today())
    return render_template("admin_membres.html", membres=membres, recherche=recherche)


@app.route("/admin/membres/nouveau", methods=["GET", "POST"])
@connexion_requise
def admin_membre_nouveau():
    """Création d'un membre. Le code d'accès généré est affiché une seule fois, sur la page de résultat."""
    erreur = None
    nom = telephone = ""
    if request.method == "POST":
        nom = request.form.get("nom", "").strip()
        telephone = request.form.get("telephone", "").strip()
        reussite, message, membre = creer_membre(get_db(), nom, telephone, date.today())
        if reussite:
            return page_sans_cache("admin_code_acces.html", titre="Membre enregistré", message=message, membre=membre)
        erreur = message
    return render_template("admin_membre_nouveau.html", erreur=erreur, nom=nom, telephone=telephone)


@app.route("/admin/membres/<int:membre_id>/code", methods=["POST"])
@connexion_requise
def admin_membre_code(membre_id):
    """Génère un nouveau code d'accès pour un membre (code oublié)."""
    reussite, message, membre = reinitialiser_code(get_db(), membre_id)
    if not reussite:
        flash(message, "erreur")
        return redirect(url_for("admin_membres"))
    return page_sans_cache("admin_code_acces.html", titre="Nouveau code d’accès", message=message, membre=membre)


@app.route("/admin/abonnements")
@connexion_requise
def admin_abonnements():
    """Liste des abonnements (?filtre=tous|en_cours|expirant|expires).
    Données transmises : abonnements, filtre, nombre_expirant."""
    filtre = request.args.get("filtre", "tous")
    if filtre not in FILTRES_ABONNEMENTS:
        filtre = "tous"
    db = get_db()
    aujourdhui = date.today()
    return render_template("admin_abonnements.html", abonnements=lister_abonnements(db, aujourdhui, filtre),
                           filtre=filtre, nombre_expirant=len(abonnements_expirant(db, aujourdhui)))


@app.route("/admin/abonnements/nouveau", methods=["GET", "POST"])
@connexion_requise
def admin_abonnement_nouveau():
    """Enregistrement d'un abonnement ou d'un renouvellement. Champs : membre_id, formule_id,
    date_debut (facultatif), montant (facultatif). Si la date est vide, elle est calculée (RG9)."""
    db = get_db()
    valeurs = {"membre_id": request.values.get("membre_id", ""), "formule_id": request.values.get("formule_id", ""),
               "date_debut": request.form.get("date_debut", ""), "montant": request.form.get("montant", "")}
    erreur = None
    if request.method == "POST":
        reussite, message, _ = enregistrer_abonnement(db, valeurs["membre_id"], valeurs["formule_id"],
                                                      valeurs["date_debut"], valeurs["montant"], date.today())
        if reussite:
            flash(message, "succes")
            return redirect(url_for("admin_abonnements"))
        erreur = message
    membres = db.execute("SELECT id, nom, telephone FROM membres ORDER BY nom COLLATE NOCASE, id").fetchall()
    formules_liste = db.execute("SELECT id, nom, duree_jours, prix FROM formules ORDER BY duree_jours").fetchall()
    return render_template("admin_abonnement_nouveau.html", erreur=erreur, valeurs=valeurs,
                           membres=membres, formules=formules_liste)


@app.route("/admin/cours")
@connexion_requise
def admin_cours():
    """Planning hebdomadaire. Données transmises : cours (liste de dictionnaires avec jour_nom et
    reservations_a_venir)."""
    return render_template("admin_cours.html", cours=lister_cours_admin(get_db(), date.today()))


@app.route("/admin/cours/nouveau", methods=["GET", "POST"])
@connexion_requise
def admin_cours_nouveau():
    """Ajout d'un cours. Données transmises : mode, cours_id, valeurs (saisies), erreur, jours."""
    erreur = None
    valeurs = {champ: request.form.get(champ, "") if request.method == "POST" else "" for champ in CHAMPS_COURS}
    if request.method == "POST":
        reussite, message, _ = creer_cours(get_db(), valeurs, date.today())
        if reussite:
            flash(message, "succes")
            return redirect(url_for("admin_cours"))
        erreur = message
    return render_template("admin_cours_form.html", mode="nouveau", cours_id=None, valeurs=valeurs,
                           erreur=erreur, jours=list(enumerate(JOURS)))


@app.route("/admin/cours/<int:cours_id>/modifier", methods=["GET", "POST"])
@connexion_requise
def admin_cours_modifier(cours_id):
    """Modification d'un cours. Mêmes données transmises que pour l'ajout."""
    db = get_db()
    cours = db.execute("SELECT * FROM cours WHERE id = ?", (cours_id,)).fetchone()
    if cours is None:
        abort(404)
    erreur = None
    if request.method == "POST":
        valeurs = {champ: request.form.get(champ, "") for champ in CHAMPS_COURS}
        reussite, message = modifier_cours(db, cours_id, valeurs, date.today())
        if reussite:
            flash(message, "succes")
            return redirect(url_for("admin_cours"))
        erreur = message
    else:
        valeurs = {champ: str(cours[champ]) for champ in CHAMPS_COURS}
    return render_template("admin_cours_form.html", mode="modifier", cours_id=cours_id, valeurs=valeurs,
                           erreur=erreur, jours=list(enumerate(JOURS)))


@app.route("/admin/cours/<int:cours_id>/supprimer", methods=["POST"])
@connexion_requise
def admin_cours_supprimer(cours_id):
    """Suppression d'un cours (en POST uniquement)."""
    reussite, message = supprimer_cours(get_db(), cours_id)
    flash(message, "succes" if reussite else "erreur")
    return redirect(url_for("admin_cours"))


@app.route("/admin/seance")
@connexion_requise
def admin_seance():
    """Inscrits d'une séance (?cours_id=&date=AAAA-MM-JJ). Données transmises : cours, jour, passee,
    inscrits, places_prises, places_restantes, dates (séances proposées)."""
    erreur, contexte = inscrits_seance(get_db(), request.args.get("cours_id"), request.args.get("date"), date.today())
    if erreur:
        flash(erreur, "erreur")
        return redirect(url_for("admin_cours"))
    return render_template("admin_seance.html", **contexte)


@app.route("/admin/statistiques")
@connexion_requise
def admin_statistiques():
    """Page des statistiques (?mois=AAAA-MM). Données transmises : stats (voir statistiques()), mois
    (liste des mois disponibles), mois_choisi, recette_choisie (dictionnaire du mois), recette_max,
    tendance (variation en % par rapport au mois précédent, ou None), disciplines, remplissage_global."""
    stats = statistiques(get_db(), date.today())
    mois = [m["mois"] for m in stats["recette"]]
    mois_choisi = request.args.get("mois")
    if mois_choisi not in mois:
        mois_choisi = mois[0] if mois else None
    recette_choisie, tendance = None, None
    if mois_choisi:
        position = mois.index(mois_choisi)
        recette_choisie = stats["recette"][position]
        if position + 1 < len(mois) and stats["recette"][position + 1]["total"]:
            precedent = stats["recette"][position + 1]["total"]
            tendance = round(100 * (recette_choisie["total"] - precedent) / precedent)
    recette_max = max((f["recette"] for f in recette_choisie["formules"]), default=0) if recette_choisie else 0

    regroupement = {}
    for cours in stats["remplissage"]:
        total = regroupement.setdefault(cours["discipline"], [0, 0])
        total[0] += cours["reservations"]
        total[1] += cours["places_max"] * cours["seances"]
    disciplines = sorted(({"discipline": d, "taux": round(100 * r / c, 1) if c else 0.0}
                          for d, (r, c) in regroupement.items()), key=lambda x: -x["taux"])
    capacite = sum(v[1] for v in regroupement.values())
    remplissage_global = round(100 * sum(v[0] for v in regroupement.values()) / capacite, 1) if capacite else 0.0
    return render_template("admin_statistiques.html", stats=stats, mois=mois, mois_choisi=mois_choisi,
                           recette_choisie=recette_choisie, recette_max=recette_max, tendance=tendance,
                           disciplines=disciplines, remplissage_global=remplissage_global)


# ---------------------------------------------------------------------------
# API JSON
# ---------------------------------------------------------------------------
@app.route("/api/planning")
def api_planning():
    """Planning d'une semaine en JSON. Paramètre facultatif : ?debut=AAAA-MM-JJ (n'importe quel
    jour de la semaine voulue). Seules la semaine en cours et la suivante sont consultables."""
    maintenant = datetime.now()
    lundi_courant = lundi_de(maintenant.date())

    texte = request.args.get("debut")
    if texte is None:
        lundi = lundi_courant
    else:
        jour = lire_date(texte)
        if jour is None:
            return jsonify(erreur="Date invalide : utiliser le format AAAA-MM-JJ."), 400
        lundi = lundi_de(jour)
        if not lundi_courant <= lundi <= lundi_courant + timedelta(days=7):
            return jsonify(erreur="Seules la semaine en cours et la suivante sont consultables."), 400

    membre_id = g.membre["id"] if g.membre else None   # facultatif : ajoute les réservations du membre
    return jsonify(construire_planning(get_db(), lundi, maintenant, membre_id))


@app.route("/api/abonnement")
def api_abonnement():
    """Abonnement du membre connecté en JSON (jours restants, progression)."""
    if g.membre is None:
        return jsonify(erreur="Connexion requise."), 401
    return jsonify(etat_abonnement(get_db(), g.membre["id"], date.today()))


@app.route("/api/admin/abonnement-apercu")
def api_admin_abonnement_apercu():
    """Aperçu d'un abonnement (dates de début et de fin, montant) sans l'enregistrer, pour le formulaire
    du gérant. Paramètres : membre_id, formule_id, date_debut et montant (facultatifs)."""
    if g.admin is None:
        return jsonify(erreur="Connexion requise."), 401
    erreur, valeurs = verifier_abonnement(get_db(), request.args.get("membre_id"), request.args.get("formule_id"),
                                          request.args.get("date_debut"), request.args.get("montant"), date.today())
    if erreur:
        return jsonify(erreur=erreur), 400
    return jsonify(valeurs)


@app.route("/api/statistiques")
def api_statistiques():
    """Les quatre statistiques en JSON (gérant uniquement)."""
    if g.admin is None:
        return jsonify(erreur="Connexion requise."), 401
    return jsonify(statistiques(get_db(), date.today()))


# ---------------------------------------------------------------------------
# Erreurs
# ---------------------------------------------------------------------------
@app.errorhandler(404)
def page_introuvable(erreur):
    """Page 404 personnalisée."""
    return render_template("404.html"), 404


@app.errorhandler(400)
def requete_refusee(erreur):
    """Requête refusée (jeton CSRF absent ou périmé)."""
    return render_template("erreur.html", titre="Formulaire expiré",
                           message="Votre formulaire n’a pas pu être vérifié. Rechargez la page puis recommencez."), 400


@app.errorhandler(405)
def methode_refusee(erreur):
    """Méthode non autorisée (par exemple ouvrir /deconnexion avec une simple adresse)."""
    return render_template("erreur.html", titre="Action non autorisée",
                           message="Cette action n’est pas disponible par cette adresse."), 405


if __name__ == "__main__":
    if not os.path.exists(CHEMIN_BASE):
        print("La base projet.db est introuvable. Lancer d'abord : python donnees_test.py")
    else:
        app.run(debug=False)   # debug=False dans la version rendue
