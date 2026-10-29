# donnees_test.py : crée projet.db, y applique schema.sql puis la remplit de données de test réalistes.
# Lancement : python donnees_test.py
#
# Volumes produits : 4 formules, 70 membres, 100 abonnements (en cours et expirés), 12 cours
# hebdomadaires, 300 réservations, 1 compte administrateur.
# random.seed(42) : les choix aléatoires sont identiques à chaque lancement. Seules les dates
# changent, car elles sont calculées à partir d'aujourd'hui (il y a toujours des abonnements
# en cours, des abonnements qui expirent bientôt et des séances à venir).
#
# La base refuse toute donnée qui viole une règle (chevauchement d'abonnements, réservation sans
# abonnement valide, cours complet...) : le script est donc écrit pour ne produire que des données valides.

import os
import random
import sqlite3
from datetime import date, timedelta

from werkzeug.security import generate_password_hash

# --- Chemins : le script fonctionne quel que soit le dossier depuis lequel on le lance ---
DOSSIER = os.path.dirname(os.path.abspath(__file__))
CHEMIN_BASE = os.path.join(DOSSIER, "projet.db")
CHEMIN_SCHEMA = os.path.join(DOSSIER, "schema.sql")

# --- Identifiants de test (à indiquer dans le README) ---
ADMIN_LOGIN = "admin"
ADMIN_MOT_DE_PASSE = "Admin!2026"
MEMBRE_TEST_TELEPHONE = "677000001"   # le premier membre, toujours abonné en cours
MEMBRE_CODE = "Membre!2026"           # code d'accès commun à tous les membres de test

# --- Volumes demandés par le sujet (minimum) ---
NB_MEMBRES = 70
NB_MEMBRES_AVEC_ABONNEMENT = 60       # les 10 autres sont des membres sans abonnement
NB_ABONNEMENTS = 100
NB_RESERVATIONS = 300

# Répartition des 60 membres abonnés selon l'état de leur DERNIER abonnement
NB_EXPIRE_BIENTOT = 8                 # fin dans les 7 prochains jours (liste « expire sous 7 jours »)
NB_EN_COURS = 32                      # abonnement en cours, fin dans plus de 7 jours
# Les 20 membres restants (60 - 8 - 32) ont un dernier abonnement expiré.

# Fenêtre des réservations de test : 28 jours dans le passé (pour les statistiques)
# jusqu'à 7 jours dans le futur (règle RG7 : on réserve au plus 7 jours à l'avance)
JOURS_PASSES = 28
JOURS_FUTURS = 7

NOMS = ["Mbarga", "Ngo Bassa", "Fotso", "Atangana", "Njoya", "Ekane", "Tchoupo", "Bello",
        "Nkoulou", "Essomba", "Kamga", "Mvondo", "Tagne", "Owona", "Fouda", "Nana"]
PRENOMS = ["Aline", "Paul", "Kevin", "Marie", "Ibrahim", "Sandra", "Rose", "Moussa",
           "Estelle", "Brice", "Carine", "Hervé", "Larissa", "Franck", "Nadège", "Cédric"]

# (nom, durée en jours, prix en FCFA) : le mensuel à 10 000 F est une valeur relevée à Yaoundé,
# les autres prix sont un choix de conception.
FORMULES = [
    ("À la séance", 1, 1500),
    ("Mensuel", 30, 10000),
    ("Trimestriel", 90, 27000),
    ("Annuel", 365, 100000),
]

# (discipline, coach, jour de la semaine 0=lundi, heure, salle, places maximales)
COURS = [
    ("Zumba", "Rose Ekane", 1, "18:00", "Salle A", 8),
    ("Zumba", "Rose Ekane", 3, "18:00", "Salle A", 15),
    ("Zumba", "Rose Ekane", 5, "10:00", "Salle A", 20),
    ("Aérobic", "Sandra Njoya", 0, "17:30", "Salle A", 15),
    ("Aérobic", "Sandra Njoya", 2, "17:30", "Salle A", 15),
    ("Aérobic", "Sandra Njoya", 5, "09:00", "Salle B", 10),
    ("Taekwondo", "Ibrahim Atangana", 1, "17:00", "Salle B", 12),
    ("Taekwondo", "Ibrahim Atangana", 3, "17:00", "Salle B", 12),
    ("Taekwondo", "Ibrahim Atangana", 6, "09:00", "Salle B", 10),
    ("Musculation", "Moussa Bello", 0, "07:00", "Salle C", 10),
    ("Musculation", "Moussa Bello", 2, "07:00", "Salle C", 10),
    ("Musculation", "Kevin Tchoupo", 4, "18:30", "Salle C", 10),
]

AUJOURDHUI = date.today()


def iso(jour):
    """Convertit une date Python en texte ISO « AAAA-MM-JJ » (format stocké en base)."""
    return jour.isoformat()


def connecter():
    """Ouvre la base en activant les clés étrangères (à refaire à CHAQUE connexion)."""
    db = sqlite3.connect(CHEMIN_BASE)
    db.execute("PRAGMA foreign_keys = ON")
    return db


def creer_tables(db):
    """Applique schema.sql : supprime les anciennes tables puis les recrée vides."""
    with open(CHEMIN_SCHEMA, encoding="utf-8") as f:
        db.executescript(f.read())
    db.execute("PRAGMA foreign_keys = ON")   # executescript peut réinitialiser la connexion : on réactive


def inserer_formules(db):
    """Remplit la table formules et renvoie {nom: (id, durée, prix)}."""
    db.executemany("INSERT INTO formules (nom, duree_jours, prix) VALUES (?, ?, ?)", FORMULES)
    lignes = db.execute("SELECT nom, id, duree_jours, prix FROM formules").fetchall()
    return {nom: (id_, duree, prix) for nom, id_, duree, prix in lignes}


def inserer_cours(db):
    """Remplit la table cours et renvoie la liste [(id, jour_semaine, places_max)]."""
    db.executemany(
        "INSERT INTO cours (discipline, coach, jour_semaine, heure, salle, places_max) "
        "VALUES (?, ?, ?, ?, ?, ?)", COURS)
    return db.execute("SELECT id, jour_semaine, places_max FROM cours ORDER BY id").fetchall()


def abonnement_depuis_fin(formule, fin):
    """Calcule un abonnement qui SE TERMINE à la date `fin` (fin incluse : début = fin - durée + 1)."""
    f_id, duree, prix = formule
    debut = fin - timedelta(days=duree - 1)
    return (f_id, debut, fin, prix)


def preparer_chaines_abonnements(formules):
    """Construit, pour chaque membre abonné, une suite d'abonnements qui ne se chevauchent pas.

    On part du DERNIER abonnement (dont l'état est choisi : expire bientôt, en cours ou expiré)
    puis on remonte dans le passé. Un renouvellement commence le lendemain de la fin du
    précédent (règle RG9) ; parfois un « trou » sépare deux abonnements (membre revenu plus tard).
    Renvoie {numéro de membre: [(formule_id, début, fin, montant), ...]} classé par date.
    """
    noms_formules = list(formules)                      # ordre : séance, mensuel, trimestriel, annuel
    poids_precedents = [2, 5, 2, 1]                     # formules des anciens abonnements
    poids_longs = [6, 3, 1]                             # dernier abonnement en cours : mensuel surtout
    formules_longues = [formules["Mensuel"], formules["Trimestriel"], formules["Annuel"]]

    # Chaque membre abonné reçoit 1, 2 ou 3 abonnements, 100 au total
    nombres = {m: 1 for m in range(1, NB_MEMBRES_AVEC_ABONNEMENT + 1)}
    for _ in range(NB_ABONNEMENTS - NB_MEMBRES_AVEC_ABONNEMENT):
        candidats = [m for m, n in nombres.items() if n < 3]
        nombres[random.choice(candidats)] += 1

    # Répartition des états du dernier abonnement ; le membre n° 1 (compte de test) est « en cours »
    membres = list(nombres)
    random.shuffle(membres)
    position = membres.index(1)
    membres[position], membres[NB_EXPIRE_BIENTOT] = membres[NB_EXPIRE_BIENTOT], membres[position]
    expire_bientot = set(membres[:NB_EXPIRE_BIENTOT])
    en_cours = set(membres[NB_EXPIRE_BIENTOT:NB_EXPIRE_BIENTOT + NB_EN_COURS])

    chaines = {}
    for membre, nombre in nombres.items():
        # 1) Dernier abonnement
        if membre in expire_bientot or membre in en_cours:
            formule = random.choices(formules_longues, weights=poids_longs)[0]
            duree = formule[1]
            if membre in expire_bientot:
                decalage = random.randint(0, 7)                     # fin entre aujourd'hui et J+7
            else:
                decalage = random.randint(8, duree - 1)             # fin au-delà de J+7, début <= aujourd'hui
            fin = AUJOURDHUI + timedelta(days=decalage)
        else:
            formule = formules[random.choices(noms_formules, weights=poids_precedents)[0]]
            fin = AUJOURDHUI - timedelta(days=random.randint(1, 150))   # abonnement déjà expiré
        chaine = [abonnement_depuis_fin(formule, fin)]

        # 2) Abonnements précédents, en remontant dans le temps
        for _ in range(nombre - 1):
            debut_suivant = chaine[0][1]
            trou = 0 if random.random() < 0.6 else random.randint(1, 30)
            fin_precedent = debut_suivant - timedelta(days=1 + trou)
            formule = formules[random.choices(noms_formules, weights=poids_precedents)[0]]
            chaine.insert(0, abonnement_depuis_fin(formule, fin_precedent))
        chaines[membre] = chaine
    return chaines


def generer_telephones(nombre):
    """Génère des numéros camerounais uniques (9 chiffres, commençant par 6)."""
    numeros = {MEMBRE_TEST_TELEPHONE}
    while len(numeros) < nombre:
        numeros.add("6" + random.choice("5789") + f"{random.randint(0, 9999999):07d}")
    numeros.discard(MEMBRE_TEST_TELEPHONE)
    liste = list(numeros)
    random.shuffle(liste)
    return [MEMBRE_TEST_TELEPHONE] + liste              # le premier numéro est celui du membre de test


def inserer_membres(db, chaines):
    """Remplit la table membres. Le code d'accès est haché une seule fois puis réutilisé."""
    empreinte = generate_password_hash(MEMBRE_CODE)
    telephones = generer_telephones(NB_MEMBRES)
    for numero in range(1, NB_MEMBRES + 1):
        if numero == 1:
            nom = "Aline Mbarga"                        # membre de test, toujours le même
        else:
            nom = f"{random.choice(PRENOMS)} {random.choice(NOMS)}"
        if numero in chaines:                           # inscrit quelques jours avant son 1er abonnement
            inscription = chaines[numero][0][1] - timedelta(days=random.randint(0, 7))
        else:                                           # membre sans abonnement : inscrit récemment
            inscription = AUJOURDHUI - timedelta(days=random.randint(1, 45))
        db.execute(
            "INSERT INTO membres (nom, telephone, date_inscription, empreinte_code) VALUES (?, ?, ?, ?)",
            (nom, telephones[numero - 1], iso(inscription), empreinte))


def inserer_abonnements(db, chaines):
    """Remplit la table abonnements à partir des suites préparées."""
    for membre, chaine in chaines.items():
        for formule_id, debut, fin, montant in chaine:
            db.execute(
                "INSERT INTO abonnements (membre_id, formule_id, date_debut, date_fin, montant_paye) "
                "VALUES (?, ?, ?, ?, ?)", (membre, formule_id, iso(debut), iso(fin), montant))


def charger_abonnements(db):
    """Renvoie [(membre_id, début, fin)] avec de vraies dates, pour choisir des réservations valides."""
    lignes = db.execute("SELECT membre_id, date_debut, date_fin FROM abonnements").fetchall()
    return [(m, date.fromisoformat(d), date.fromisoformat(f)) for m, d, f in lignes]


def tenter_reservation(db, cours_id, membre_id, jour):
    """Essaie d'enregistrer une réservation. La base refuse si une règle est violée (doublon,
    cours complet, abonnement non valide, mauvais jour...) : on renvoie alors False."""
    try:
        db.execute("INSERT INTO reservations_cours (cours_id, membre_id, date_seance) VALUES (?, ?, ?)",
                   (cours_id, membre_id, iso(jour)))
        return True
    except sqlite3.IntegrityError:
        return False


def dates_du_cours(debut, fin, jour_semaine):
    """Liste les dates entre `debut` et `fin` (incluses) qui tombent le bon jour de la semaine."""
    jours = []
    courant = debut
    while courant <= fin:
        if courant.weekday() == jour_semaine:
            jours.append(courant)
        courant += timedelta(days=1)
    return jours


def prochaine_date(jour_semaine):
    """Prochaine date (aujourd'hui incluse) tombant ce jour de la semaine."""
    jour = AUJOURDHUI
    while jour.weekday() != jour_semaine:
        jour += timedelta(days=1)
    return jour


def nombre_reservations(db):
    return db.execute("SELECT COUNT(*) FROM reservations_cours").fetchone()[0]


def remplir_seance(db, cours, abonnements):
    """Remplit complètement la prochaine séance d'un cours (pour avoir des séances « complètes »)."""
    cours_id, jour_semaine, places_max = cours
    jour = prochaine_date(jour_semaine)
    candidats = [m for m, debut, fin in abonnements if debut <= jour <= fin]
    random.shuffle(candidats)
    for membre in candidats:
        deja = db.execute("SELECT COUNT(*) FROM reservations_cours WHERE cours_id = ? AND date_seance = ?",
                          (cours_id, iso(jour))).fetchone()[0]
        if deja >= places_max:
            break
        tenter_reservation(db, cours_id, membre, jour)


def reserver_pour_membre_test(db, cours_liste, abonnements, nombre=2):
    """Donne au membre de test (n° 1) quelques réservations à venir, pour que son espace ne soit pas vide."""
    mes_abonnements = [(debut, fin) for m, debut, fin in abonnements if m == 1]
    obtenues = 0
    for decalage in range(JOURS_FUTURS + 1):
        jour = AUJOURDHUI + timedelta(days=decalage)
        if not any(debut <= jour <= fin for debut, fin in mes_abonnements):
            continue
        for cours_id, jour_semaine, _ in cours_liste:
            if jour_semaine == jour.weekday() and tenter_reservation(db, cours_id, 1, jour):
                obtenues += 1
                break                                   # une séance par jour au maximum
        if obtenues >= nombre:
            return


def inserer_reservations(db, cours_liste, abonnements):
    """Crée NB_RESERVATIONS réservations valides, passées et à venir."""
    # 1) Deux séances à venir complètes (les cours aux plus petites capacités)
    for cours in sorted(cours_liste, key=lambda c: c[2])[:2]:
        remplir_seance(db, cours, abonnements)

    # 2) Quelques séances à venir pour le membre de test
    reserver_pour_membre_test(db, cours_liste, abonnements)

    # 3) Réservations aléatoires : les petits cours sont plus demandés
    poids = [1 / places for _, _, places in cours_liste]
    debut_fenetre = AUJOURDHUI - timedelta(days=JOURS_PASSES)
    fin_fenetre = AUJOURDHUI + timedelta(days=JOURS_FUTURS)
    tentatives = 0
    while nombre_reservations(db) < NB_RESERVATIONS and tentatives < 100000:
        tentatives += 1
        membre, debut, fin = random.choice(abonnements)
        bas, haut = max(debut, debut_fenetre), min(fin, fin_fenetre)
        if bas > haut:
            continue                                    # cet abonnement ne couvre pas la fenêtre
        cours_id, jour_semaine, _ = random.choices(cours_liste, weights=poids)[0]
        jours_possibles = dates_du_cours(bas, haut, jour_semaine)
        if jours_possibles:
            tenter_reservation(db, cours_id, membre, random.choice(jours_possibles))


def inserer_administrateur(db):
    """Crée le compte du gérant (mot de passe haché)."""
    db.execute("INSERT INTO administrateurs (login, empreinte) VALUES (?, ?)",
               (ADMIN_LOGIN, generate_password_hash(ADMIN_MOT_DE_PASSE)))


def afficher_resume(db):
    """Affiche les volumes obtenus et quelques indicateurs de contrôle."""
    aujourdhui = iso(AUJOURDHUI)
    dans_7_jours = iso(AUJOURDHUI + timedelta(days=7))
    print("--- Volumes ---")
    for table in ("formules", "membres", "abonnements", "cours", "reservations_cours", "administrateurs"):
        nombre = db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        print(f"{table} : {nombre} lignes")
    en_cours = db.execute("SELECT COUNT(DISTINCT membre_id) FROM abonnements WHERE date_debut <= ? AND date_fin >= ?",
                          (aujourdhui, aujourdhui)).fetchone()[0]
    bientot = db.execute("SELECT COUNT(*) FROM abonnements WHERE date_fin BETWEEN ? AND ?",
                         (aujourdhui, dans_7_jours)).fetchone()[0]
    complets = db.execute(
        "SELECT COUNT(*) FROM (SELECT r.cours_id, r.date_seance FROM reservations_cours r "
        "JOIN cours c ON c.id = r.cours_id WHERE r.date_seance >= ? "
        "GROUP BY r.cours_id, r.date_seance HAVING COUNT(*) >= c.places_max)", (aujourdhui,)).fetchone()[0]
    print("--- Contrôles ---")
    print(f"membres actifs (abonnement en cours) : {en_cours}")
    print(f"abonnements qui expirent dans les 7 jours : {bientot}")
    print(f"séances à venir complètes : {complets}")
    print("--- Comptes de test ---")
    print(f"administrateur : login {ADMIN_LOGIN} / mot de passe {ADMIN_MOT_DE_PASSE}")
    print(f"membre : téléphone {MEMBRE_TEST_TELEPHONE} / code {MEMBRE_CODE}")


def main():
    random.seed(42)                                     # mêmes choix aléatoires à chaque lancement
    db = connecter()
    creer_tables(db)
    formules = inserer_formules(db)
    cours_liste = inserer_cours(db)
    chaines = preparer_chaines_abonnements(formules)
    inserer_membres(db, chaines)
    inserer_abonnements(db, chaines)
    inserer_reservations(db, cours_liste, charger_abonnements(db))
    inserer_administrateur(db)
    db.commit()
    afficher_resume(db)
    db.close()


if __name__ == "__main__":
    main()
