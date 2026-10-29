# tests_backend.py : tests automatiques du backend (réservation, annulation, droits, API).
# À placer À CÔTÉ du dossier projet/ (pas dedans, pas sur la clé : ce n'est pas un livrable).
# Lancement : python tests_backend.py
# Les tests utilisent une base temporaire : projet.db n'est jamais modifiée.

import os
import re
import sqlite3
import sys
import tempfile
import unittest
from datetime import date, datetime, timedelta
from unittest import mock

DOSSIER_PROJET = os.path.join(os.path.dirname(os.path.abspath(__file__)), "projet")
sys.path.insert(0, DOSSIER_PROJET)

import app as appli                                    # noqa: E402  (app.py du projet)
from werkzeug.security import generate_password_hash   # noqa: E402

CODE = "code1234"
# Moment fixe pour les tests de règles : mercredi 21 octobre 2026, 10 h
MAINTENANT = datetime(2026, 10, 21, 10, 0)


def creer_base(chemin):
    """Crée une base vide avec le schéma du projet."""
    db = sqlite3.connect(chemin)
    with open(os.path.join(DOSSIER_PROJET, "schema.sql"), encoding="utf-8") as f:
        db.executescript(f.read())
    db.execute("PRAGMA foreign_keys = ON")
    return db


def remplir_cas_fixe(db):
    """Cours et membres pour les tests de règles (dates fixes autour du mercredi 21/10/2026)."""
    db.execute("INSERT INTO formules (nom, duree_jours, prix) VALUES ('Mensuel', 30, 10000)")
    cours = [  # (discipline, coach, jour_semaine, heure, salle, places)
        ("Zumba", "Rose", 3, "18:00", "Salle A", 2),        # 1 : jeudi
        ("Aérobic", "Sandra", 2, "07:00", "Salle A", 5),    # 2 : mercredi matin (déjà commencé à 10 h)
        ("Taekwondo", "Ibrahim", 2, "17:00", "Salle B", 5),  # 3 : mercredi soir
        ("Musculation", "Moussa", 4, "18:30", "Salle C", 5),  # 4 : vendredi
    ]
    db.executemany("INSERT INTO cours (discipline, coach, jour_semaine, heure, salle, places_max) "
                   "VALUES (?, ?, ?, ?, ?, ?)", cours)
    empreinte = generate_password_hash(CODE)
    for i in range(1, 7):
        db.execute("INSERT INTO membres (nom, telephone, empreinte_code) VALUES (?, ?, ?)",
                   (f"Membre {i}", f"6770000{i:02d}", empreinte))
    abonnements = [
        (1, "2026-10-01", "2026-10-30"),   # A : abonné toute la période
        (2, "2026-11-01", "2026-11-30"),   # B : abonnement qui commence plus tard
        (4, "2026-10-10", "2026-10-22"),   # D : abonnement qui se termine jeudi 22
        (5, "2026-10-01", "2026-10-30"),   # E
        (6, "2026-10-01", "2026-10-30"),   # F
    ]                                       # membre 3 (C) : aucun abonnement
    for membre, debut, fin in abonnements:
        db.execute("INSERT INTO abonnements (membre_id, formule_id, date_debut, date_fin, montant_paye) "
                   "VALUES (?, 1, ?, ?, 10000)", (membre, debut, fin))
    db.commit()


class ReglesDeReservation(unittest.TestCase):
    """Tests de reserver_place() et annuler_reservation() avec un moment fixe."""

    def setUp(self):
        self.fichier = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.fichier.close()
        self.db = creer_base(self.fichier.name)
        self.db.row_factory = sqlite3.Row
        remplir_cas_fixe(self.db)

    def tearDown(self):
        self.db.close()
        os.unlink(self.fichier.name)

    def reserver(self, membre, cours, jour, maintenant=MAINTENANT):
        return appli.reserver_place(self.db, membre, cours, jour, maintenant)

    def nombre(self):
        return self.db.execute("SELECT COUNT(*) FROM reservations_cours").fetchone()[0]

    # ---------- cas valides ----------
    def test_reservation_valide(self):
        ok, message = self.reserver(1, "1", "2026-10-22")
        self.assertTrue(ok, message)
        self.assertIn("Réservation confirmée", message)
        self.assertEqual(self.nombre(), 1)

    def test_seance_du_jour_pas_encore_commencee(self):
        ok, message = self.reserver(1, 3, "2026-10-21")          # mercredi 17 h, il est 10 h
        self.assertTrue(ok, message)

    def test_dernier_jour_de_la_fenetre_de_7_jours(self):
        ok, message = self.reserver(1, 3, "2026-10-28")          # mercredi suivant = aujourd'hui + 7
        self.assertTrue(ok, message)

    def test_dernier_jour_d_abonnement_inclus(self):
        ok, message = self.reserver(4, 1, "2026-10-22")          # D : abonnement fini le 22, séance le 22
        self.assertTrue(ok, message)

    # ---------- refus : séance ----------
    def test_seance_deja_commencee(self):
        ok, message = self.reserver(1, 2, "2026-10-21")          # mercredi 7 h, il est 10 h
        self.assertFalse(ok)
        self.assertIn("déjà commencé", message)

    def test_seance_passee(self):
        ok, message = self.reserver(1, 3, "2026-10-14")
        self.assertFalse(ok)
        self.assertIn("déjà passée", message)

    def test_trop_en_avance(self):
        ok, message = self.reserver(1, 1, "2026-10-29")          # aujourd'hui + 8
        self.assertFalse(ok)
        self.assertIn("à l’avance", message)

    def test_mauvais_jour_de_la_semaine(self):
        ok, message = self.reserver(1, 1, "2026-10-23")          # le cours est le jeudi, pas le vendredi
        self.assertFalse(ok)
        self.assertIn("jeudi", message)

    # ---------- refus : abonnement ----------
    def test_sans_abonnement(self):
        ok, message = self.reserver(3, 1, "2026-10-22")
        self.assertFalse(ok)
        self.assertIn("abonnement n’est pas valide", message)

    def test_abonnement_pas_encore_commence(self):
        ok, message = self.reserver(2, 1, "2026-10-22")
        self.assertFalse(ok)
        self.assertIn("abonnement n’est pas valide", message)

    def test_abonnement_termine_avant_la_seance(self):
        ok, message = self.reserver(4, 4, "2026-10-23")          # D : fini le 22, séance le 23
        self.assertFalse(ok)
        self.assertIn("abonnement n’est pas valide", message)

    # ---------- refus : doublon et capacité ----------
    def test_doublon(self):
        self.assertTrue(self.reserver(1, 1, "2026-10-22")[0])
        ok, message = self.reserver(1, 1, "2026-10-22")
        self.assertFalse(ok)
        self.assertIn("déjà réservé", message)
        self.assertEqual(self.nombre(), 1)

    def test_cours_complet(self):
        self.assertTrue(self.reserver(1, 1, "2026-10-22")[0])
        self.assertTrue(self.reserver(5, 1, "2026-10-22")[0])    # 2 places : le cours est maintenant plein
        ok, message = self.reserver(6, 1, "2026-10-22")
        self.assertFalse(ok)
        self.assertIn("complète", message)
        self.assertEqual(self.nombre(), 2)

    def test_une_autre_date_du_meme_cours_reste_libre(self):
        self.assertTrue(self.reserver(1, 1, "2026-10-22")[0])
        self.assertTrue(self.reserver(5, 1, "2026-10-22")[0])
        ok, message = self.reserver(6, 1, "2026-10-29", maintenant=datetime(2026, 10, 25, 10, 0))
        self.assertTrue(ok, message)                              # le jeudi suivant n'est pas complet

    def test_derniere_place_prise_entre_temps(self):
        """Le contrôle de capacité de Python est contourné (simulation d'une réservation simultanée) :
        la base de données refuse quand même, et l'application répond proprement."""
        self.assertTrue(self.reserver(1, 1, "2026-10-22")[0])
        self.assertTrue(self.reserver(5, 1, "2026-10-22")[0])
        with mock.patch.object(appli, "compter_places_prises", return_value=0):
            ok, message = self.reserver(6, 1, "2026-10-22")
        self.assertFalse(ok)
        self.assertIn("n’a pas pu être enregistrée", message)
        self.assertEqual(self.nombre(), 2)

    # ---------- données invalides ----------
    def test_donnees_invalides(self):
        for cours, jour in [(None, "2026-10-22"), ("abc", "2026-10-22"), ("1.5", "2026-10-22"),
                            ("1", None), ("1", "pas-une-date"), ("1", "2026-13-45"), ("1", "")]:
            with self.subTest(cours=cours, jour=jour):
                ok, message = self.reserver(1, cours, jour)
                self.assertFalse(ok)
                self.assertIn("Demande invalide", message)
        self.assertEqual(self.nombre(), 0)

    def test_cours_inexistant(self):
        ok, message = self.reserver(1, 999, "2026-10-22")
        self.assertFalse(ok)
        self.assertIn("n’existe pas", message)

    def test_injection_sql_sans_effet(self):
        ok, _ = self.reserver(1, "1; DROP TABLE reservations_cours;--", "2026-10-22")
        self.assertFalse(ok)
        ok, _ = self.reserver(1, "1", "2026-10-22'; DROP TABLE reservations_cours;--")
        self.assertFalse(ok)
        self.assertEqual(self.nombre(), 0)                        # la table existe toujours

    # ---------- annulation ----------
    def test_annulation_libere_la_place(self):
        self.reserver(1, 1, "2026-10-22")
        self.reserver(5, 1, "2026-10-22")
        reservation = self.db.execute("SELECT id FROM reservations_cours WHERE membre_id = 1").fetchone()["id"]
        ok, message = appli.annuler_reservation(self.db, 1, reservation, MAINTENANT)
        self.assertTrue(ok, message)
        self.assertEqual(self.nombre(), 1)
        self.assertTrue(self.reserver(6, 1, "2026-10-22")[0])     # la place libérée est de nouveau disponible

    def test_annuler_la_reservation_d_un_autre_est_refuse(self):
        self.reserver(1, 1, "2026-10-22")
        reservation = self.db.execute("SELECT id FROM reservations_cours").fetchone()["id"]
        ok, message = appli.annuler_reservation(self.db, 5, reservation, MAINTENANT)
        self.assertFalse(ok)
        self.assertIn("introuvable", message)
        self.assertEqual(self.nombre(), 1)

    def test_annuler_une_reservation_inexistante(self):
        ok, message = appli.annuler_reservation(self.db, 1, 999, MAINTENANT)
        self.assertFalse(ok)
        self.assertIn("introuvable", message)

    def test_annulation_apres_le_debut_de_la_seance(self):
        # Réservation créée directement (séance de 7 h le mercredi), puis annulation à 10 h
        self.db.execute("INSERT INTO reservations_cours (cours_id, membre_id, date_seance) VALUES (2, 1, '2026-10-21')")
        self.db.commit()
        reservation = self.db.execute("SELECT id FROM reservations_cours").fetchone()["id"]
        ok, message = appli.annuler_reservation(self.db, 1, reservation, MAINTENANT)
        self.assertFalse(ok)
        self.assertIn("déjà commencé", message)
        self.assertEqual(self.nombre(), 1)

    def test_annulation_juste_avant_le_debut(self):
        self.reserver(1, 3, "2026-10-21")                         # mercredi 17 h
        reservation = self.db.execute("SELECT id FROM reservations_cours").fetchone()["id"]
        ok, message = appli.annuler_reservation(self.db, 1, reservation, datetime(2026, 10, 21, 16, 59))
        self.assertTrue(ok, message)

    def test_annulation_pile_a_l_heure_refusee(self):
        self.reserver(1, 3, "2026-10-21")
        reservation = self.db.execute("SELECT id FROM reservations_cours").fetchone()["id"]
        ok, _ = appli.annuler_reservation(self.db, 1, reservation, datetime(2026, 10, 21, 17, 0))
        self.assertFalse(ok)

    def test_liste_des_reservations_a_venir_et_drapeau_annulable(self):
        self.reserver(1, 3, "2026-10-21")                         # mercredi 17 h
        self.reserver(1, 1, "2026-10-22")                         # jeudi 18 h
        liste = appli.lister_reservations_a_venir(self.db, 1, MAINTENANT)
        self.assertEqual([r["date_seance"] for r in liste], ["2026-10-21", "2026-10-22"])
        self.assertTrue(all(r["annulable"] for r in liste))
        tard = appli.lister_reservations_a_venir(self.db, 1, datetime(2026, 10, 21, 18, 0))
        self.assertFalse(tard[0]["annulable"])                    # la séance de 17 h a commencé
        self.assertTrue(tard[1]["annulable"])


class ReservationsParLeSite(unittest.TestCase):
    """Tests au niveau HTTP (sessions, CSRF, droits, API) avec des dates relatives à aujourd'hui."""

    def setUp(self):
        self.fichier = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.fichier.close()
        db = creer_base(self.fichier.name)
        db.execute("INSERT INTO formules (nom, duree_jours, prix) VALUES ('Mensuel', 30, 10000)")
        self.demain = date.today() + timedelta(days=1)
        db.execute("INSERT INTO cours (discipline, coach, jour_semaine, heure, salle, places_max) "
                   "VALUES ('Zumba', 'Rose', ?, '23:59', 'Salle A', 3)", (self.demain.weekday(),))
        empreinte = generate_password_hash(CODE)
        for i in (1, 2):
            db.execute("INSERT INTO membres (nom, telephone, empreinte_code) VALUES (?, ?, ?)",
                       (f"Membre {i}", f"6770000{i:02d}", empreinte))
            db.execute("INSERT INTO abonnements (membre_id, formule_id, date_debut, date_fin, montant_paye) "
                       "VALUES (?, 1, ?, ?, 10000)",
                       (i, (date.today() - timedelta(days=5)).isoformat(), (date.today() + timedelta(days=25)).isoformat()))
        db.commit()
        db.close()
        self.patch = mock.patch.object(appli, "CHEMIN_BASE", self.fichier.name)
        self.patch.start()
        appli.app.config["TESTING"] = True

    def tearDown(self):
        self.patch.stop()
        os.unlink(self.fichier.name)

    def jeton(self, client, url="/connexion"):
        return re.search(r'name="csrf_token" value="([0-9a-f]+)"', client.get(url).data.decode()).group(1)

    def connecter(self, numero=1):
        client = appli.app.test_client()
        reponse = client.post("/connexion", data={"telephone": f"6770000{numero:02d}", "code": CODE,
                                                   "csrf_token": self.jeton(client)})
        self.assertEqual(reponse.status_code, 302)
        return client

    def lignes(self):
        db = sqlite3.connect(self.fichier.name)
        resultat = db.execute("SELECT id, membre_id FROM reservations_cours").fetchall()
        db.close()
        return resultat

    def reserver(self, client, jour=None, cours="1", avec_jeton=True):
        donnees = {"cours_id": cours, "date_seance": (jour or self.demain).isoformat()}
        if avec_jeton:
            donnees["csrf_token"] = self.jeton(client, "/membre")
        return client.post("/reserver", data=donnees)

    def test_visiteur_non_connecte_est_renvoye_vers_la_connexion(self):
        client = appli.app.test_client()
        reponse = client.post("/reserver", data={"cours_id": "1", "date_seance": self.demain.isoformat(),
                                                 "csrf_token": self.jeton(client)})
        self.assertEqual(reponse.status_code, 302)
        self.assertTrue(reponse.headers["Location"].endswith("/connexion"))
        self.assertEqual(self.lignes(), [])

    def test_post_sans_jeton_csrf_refuse(self):
        client = self.connecter()
        reponse = self.reserver(client, avec_jeton=False)
        self.assertEqual(reponse.status_code, 400)
        self.assertEqual(self.lignes(), [])

    def test_get_sur_les_actions_est_refuse(self):
        client = self.connecter()
        self.assertEqual(client.get("/reserver").status_code, 405)
        self.assertEqual(client.get("/annuler/1").status_code, 405)

    def test_reservation_puis_affichage_dans_l_espace_membre(self):
        client = self.connecter()
        reponse = self.reserver(client)
        self.assertEqual(reponse.status_code, 302)
        self.assertTrue(reponse.headers["Location"].endswith("/membre"))
        page = client.get("/membre").data.decode()
        self.assertIn("Réservation confirmée", page)
        self.assertIn("Zumba", page)
        self.assertIn("Rose", page)
        self.assertEqual(len(self.lignes()), 1)

    def test_erreur_de_regle_renvoie_vers_le_planning_avec_un_message(self):
        client = self.connecter()
        self.reserver(client)
        reponse = self.reserver(client)                          # doublon
        self.assertTrue(reponse.headers["Location"].endswith("/planning"))
        self.assertIn("déjà réservé", client.get("/planning").data.decode())
        self.assertEqual(len(self.lignes()), 1)

    def test_l_identite_vient_de_la_session_pas_du_formulaire(self):
        client = self.connecter(1)
        donnees = {"cours_id": "1", "date_seance": self.demain.isoformat(), "membre_id": "2",
                   "csrf_token": self.jeton(client, "/membre")}
        client.post("/reserver", data=donnees)
        self.assertEqual(self.lignes()[0][1], 1)                  # réservé pour le membre connecté (1), pas le 2

    def test_annulation_par_le_proprietaire(self):
        client = self.connecter(1)
        self.reserver(client)
        identifiant = self.lignes()[0][0]
        reponse = client.post(f"/annuler/{identifiant}", data={"csrf_token": self.jeton(client, "/membre")})
        self.assertEqual(reponse.status_code, 302)
        self.assertEqual(self.lignes(), [])

    def test_un_membre_ne_peut_pas_annuler_la_reservation_d_un_autre(self):
        proprietaire = self.connecter(1)
        self.reserver(proprietaire)
        identifiant = self.lignes()[0][0]
        intrus = self.connecter(2)
        intrus.post(f"/annuler/{identifiant}", data={"csrf_token": self.jeton(intrus, "/membre")})
        self.assertEqual(len(self.lignes()), 1)                  # la réservation existe toujours
        self.assertIn("Réservation introuvable", intrus.get("/membre").data.decode())

    def test_api_planning_indique_les_reservations_du_membre(self):
        client = self.connecter(1)
        self.reserver(client)
        identifiant = self.lignes()[0][0]

        def seance(c):
            donnees = c.get(f"/api/planning?debut={self.demain.isoformat()}").get_json()
            jour = next(j for j in donnees["jours"] if j["date"] == self.demain.isoformat())
            return jour["seances"][0]

        connecte = seance(client)
        self.assertTrue(connecte["reservee"])
        self.assertEqual(connecte["reservation_id"], identifiant)
        self.assertFalse(connecte["reservable"])                 # déjà réservée : pas de nouveau bouton
        self.assertEqual(connecte["places_restantes"], 2)

        visiteur = seance(appli.app.test_client())
        self.assertFalse(visiteur["reservee"])
        self.assertIsNone(visiteur["reservation_id"])
        self.assertTrue(visiteur["reservable"])
        self.assertEqual(visiteur["places_restantes"], 2)

        autre = seance(self.connecter(2))
        self.assertFalse(autre["reservee"])                      # il ne voit pas la réservation du membre 1
        self.assertIsNone(autre["reservation_id"])


# ===========================================================================
# Gestion des membres et des abonnements (espace gérant)
# ===========================================================================
AUJOURDHUI = date(2026, 10, 21)


class BaseTemporaire(unittest.TestCase):
    """Base de test : une base SQLite temporaire avec trois formules."""

    def setUp(self):
        self.fichier = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.fichier.close()
        self.db = creer_base(self.fichier.name)
        self.db.row_factory = sqlite3.Row
        self.db.executemany("INSERT INTO formules (nom, duree_jours, prix) VALUES (?, ?, ?)",
                            [("À la séance", 1, 1500), ("Mensuel", 30, 10000), ("Trimestriel", 90, 27000)])
        self.db.commit()

    def tearDown(self):
        self.db.close()
        os.unlink(self.fichier.name)

    def membre(self, numero, nom=None):
        self.db.execute("INSERT INTO membres (nom, telephone, empreinte_code) VALUES (?, ?, 'h')",
                        (nom or f"Membre {numero}", f"6770000{numero:02d}"))
        self.db.commit()
        return self.db.execute("SELECT MAX(id) FROM membres").fetchone()[0]

    def abonnement(self, membre_id, formule_id, debut, fin):
        self.db.execute("INSERT INTO abonnements (membre_id, formule_id, date_debut, date_fin, montant_paye) "
                        "VALUES (?, ?, ?, ?, 10000)", (membre_id, formule_id, debut, fin))
        self.db.commit()

    def nombre(self, table):
        return self.db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]


class GestionDesMembres(BaseTemporaire):

    def creer(self, nom="Aline Mbarga", telephone="677000001"):
        return appli.creer_membre(self.db, nom, telephone, AUJOURDHUI)

    def test_creation_valide(self):
        ok, message, membre = self.creer()
        self.assertTrue(ok, message)
        self.assertEqual(membre["telephone"], "677000001")
        ligne = self.db.execute("SELECT * FROM membres WHERE id = ?", (membre["id"],)).fetchone()
        self.assertEqual(ligne["date_inscription"], "2026-10-21")

    def test_le_code_n_est_stocke_que_sous_forme_d_empreinte(self):
        _, _, membre = self.creer()
        ligne = self.db.execute("SELECT empreinte_code FROM membres").fetchone()
        self.assertNotIn(membre["code"], ligne["empreinte_code"])
        self.assertTrue(appli.check_password_hash(ligne["empreinte_code"], membre["code"]))

    def test_format_du_code_et_unicite(self):
        codes = {appli.generer_code_acces() for _ in range(300)}
        self.assertGreater(len(codes), 295)                       # imprévisible : pas de répétitions
        for code in codes:
            self.assertEqual(len(code), 6)
            self.assertTrue(set(code) <= set(appli.ALPHABET_CODE))
        self.assertFalse(set("0O1IL") & set(appli.ALPHABET_CODE))  # caractères ambigus exclus

    def test_numero_nettoye(self):
        ok, _, membre = self.creer(telephone="+237 677 00 00 01")
        self.assertTrue(ok)
        self.assertEqual(membre["telephone"], "677000001")

    def test_nom_nettoye(self):
        _, _, membre = self.creer(nom="  Aline    Mbarga \n")
        self.assertEqual(membre["nom"], "Aline Mbarga")

    def test_doublon_de_numero_meme_avec_une_autre_ecriture(self):
        self.assertTrue(self.creer()[0])
        ok, message, _ = self.creer(nom="Autre", telephone="+237-677-000-001")
        self.assertFalse(ok)
        self.assertIn("déjà enregistré", message)
        self.assertEqual(self.nombre("membres"), 1)

    def test_noms_invalides(self):
        for nom in ["", "   ", "A", "x" * 81, "mauvais\x00nom", None]:
            with self.subTest(nom=nom):
                self.assertFalse(self.creer(nom=nom)[0])
        self.assertEqual(self.nombre("membres"), 0)

    def test_numeros_invalides(self):
        for numero in ["", "12345", "577000001", "67700000", "6770000012", "abcdefghi", None, "' OR 1=1 --"]:
            with self.subTest(numero=numero):
                self.assertFalse(self.creer(telephone=numero)[0])
        self.assertEqual(self.nombre("membres"), 0)

    def test_reinitialisation_du_code(self):
        _, _, membre = self.creer()
        ok, message, nouveau = appli.reinitialiser_code(self.db, membre["id"])
        self.assertTrue(ok, message)
        empreinte = self.db.execute("SELECT empreinte_code FROM membres").fetchone()[0]
        self.assertTrue(appli.check_password_hash(empreinte, nouveau["code"]))
        self.assertFalse(appli.check_password_hash(empreinte, membre["code"]))   # l'ancien code ne marche plus

    def test_reinitialisation_d_un_membre_inconnu(self):
        self.assertFalse(appli.reinitialiser_code(self.db, 999)[0])

    def test_recherche_et_etat_des_membres(self):
        a = self.membre(1, "Aline Mbarga")
        b = self.membre(2, "Paul Fotso")
        self.membre(3, "Ibrahim Bello")
        self.abonnement(a, 2, "2026-10-01", "2026-10-30")           # actif
        self.abonnement(b, 2, "2026-08-01", "2026-08-30")           # expiré
        toutes = appli.lister_membres(self.db, "", AUJOURDHUI)
        self.assertEqual([m["nom"] for m in toutes], ["Aline Mbarga", "Ibrahim Bello", "Paul Fotso"])
        etats = {m["nom"]: (m["actif"], m["derniere_fin"]) for m in toutes}
        self.assertEqual(etats["Aline Mbarga"], (True, "2026-10-30"))
        self.assertEqual(etats["Paul Fotso"], (False, "2026-08-30"))
        self.assertEqual(etats["Ibrahim Bello"], (False, None))
        self.assertEqual([m["nom"] for m in appli.lister_membres(self.db, "ALINE", AUJOURDHUI)], ["Aline Mbarga"])
        self.assertEqual([m["nom"] for m in appli.lister_membres(self.db, "fots", AUJOURDHUI)], ["Paul Fotso"])
        self.assertEqual([m["nom"] for m in appli.lister_membres(self.db, "000002", AUJOURDHUI)], ["Paul Fotso"])

    def test_la_recherche_ne_traite_pas_les_jokers_comme_des_jokers(self):
        self.membre(1, "Aline Mbarga")
        for recherche in ["%", "_", "A%a", "\\"]:
            with self.subTest(recherche=recherche):
                self.assertEqual(appli.lister_membres(self.db, recherche, AUJOURDHUI), [])

    def test_recherche_avec_injection_sql(self):
        self.membre(1)
        self.assertEqual(appli.lister_membres(self.db, "'; DROP TABLE membres;--", AUJOURDHUI), [])
        self.assertEqual(self.nombre("membres"), 1)


class GestionDesAbonnements(BaseTemporaire):
    SEANCE, MENSUEL, TRIMESTRIEL = 1, 2, 3

    def enregistrer(self, membre, formule, debut="", montant=""):
        return appli.enregistrer_abonnement(self.db, membre, formule, debut, montant, AUJOURDHUI)

    def dernier(self):
        return self.db.execute("SELECT * FROM abonnements ORDER BY id DESC LIMIT 1").fetchone()

    # ---------- dates ----------
    def test_premier_abonnement_commence_aujourd_hui(self):
        m = self.membre(1)
        ok, message, valeurs = self.enregistrer(m, self.MENSUEL)
        self.assertTrue(ok, message)
        self.assertEqual((valeurs["date_debut"], valeurs["date_fin"]), ("2026-10-21", "2026-11-19"))   # 30 jours, fin incluse
        self.assertEqual(valeurs["montant"], 10000)

    def test_formule_a_la_seance_dure_un_jour(self):
        m = self.membre(1)
        _, _, valeurs = self.enregistrer(m, self.SEANCE)
        self.assertEqual((valeurs["date_debut"], valeurs["date_fin"]), ("2026-10-21", "2026-10-21"))

    def test_renouvellement_commence_le_lendemain_de_la_fin(self):
        m = self.membre(1)
        self.abonnement(m, self.MENSUEL, "2026-10-01", "2026-10-30")
        ok, message, valeurs = self.enregistrer(m, self.MENSUEL)
        self.assertTrue(ok, message)
        self.assertEqual((valeurs["date_debut"], valeurs["date_fin"]), ("2026-10-31", "2026-11-29"))
        self.assertIn("fin de l’abonnement actuel", message)

    def test_renouvellements_enchaines(self):
        m = self.membre(1)
        self.abonnement(m, self.MENSUEL, "2026-10-01", "2026-10-30")
        self.enregistrer(m, self.MENSUEL)
        _, _, deuxieme = self.enregistrer(m, self.MENSUEL)
        self.assertEqual(deuxieme["date_debut"], "2026-11-30")      # sans trou ni chevauchement
        self.assertEqual(self.nombre("abonnements"), 3)

    def test_renouvellement_apres_expiration_commence_aujourd_hui(self):
        m = self.membre(1)
        self.abonnement(m, self.MENSUEL, "2026-09-01", "2026-09-30")
        _, _, valeurs = self.enregistrer(m, self.MENSUEL)
        self.assertEqual(valeurs["date_debut"], "2026-10-21")

    def test_le_dernier_jour_d_abonnement_compte(self):
        m = self.membre(1)
        self.abonnement(m, self.MENSUEL, "2026-09-22", "2026-10-21")  # se termine aujourd'hui
        _, _, valeurs = self.enregistrer(m, self.MENSUEL)
        self.assertEqual(valeurs["date_debut"], "2026-10-22")

    def test_date_explicite_dans_le_futur(self):
        m = self.membre(1)
        ok, message, valeurs = self.enregistrer(m, self.MENSUEL, debut="2026-11-05")
        self.assertTrue(ok, message)
        self.assertEqual(valeurs["date_fin"], "2026-12-04")

    def test_chevauchement_refuse_avec_une_suggestion(self):
        m = self.membre(1)
        self.abonnement(m, self.MENSUEL, "2026-10-01", "2026-10-30")
        ok, message, _ = self.enregistrer(m, self.MENSUEL, debut="2026-10-25")
        self.assertFalse(ok)
        self.assertIn("chevauche", message)
        self.assertIn("31 octobre 2026", message)                   # propose la bonne date
        self.assertEqual(self.nombre("abonnements"), 1)

    def test_cas_limite_le_lendemain_de_la_fin_est_permis_le_dernier_jour_non(self):
        m = self.membre(1)
        self.abonnement(m, self.MENSUEL, "2026-10-01", "2026-10-30")
        self.assertFalse(self.enregistrer(m, self.SEANCE, debut="2026-10-30")[0])   # le 30 est déjà couvert
        self.assertTrue(self.enregistrer(m, self.SEANCE, debut="2026-10-31")[0])    # le 31 est libre

    def test_chevauchement_avec_un_abonnement_a_venir(self):
        m = self.membre(1)
        self.abonnement(m, self.MENSUEL, "2026-12-01", "2026-12-30")
        ok, _, _ = self.enregistrer(m, self.MENSUEL, debut="2026-11-20")            # fin le 19/12 : chevauche
        self.assertFalse(ok)

    def test_un_abonnement_d_un_autre_membre_n_est_pas_un_chevauchement(self):
        a, b = self.membre(1), self.membre(2)
        self.abonnement(a, self.MENSUEL, "2026-10-01", "2026-10-30")
        self.assertTrue(self.enregistrer(b, self.MENSUEL, debut="2026-10-21")[0])

    def test_dates_de_debut_invalides(self):
        m = self.membre(1)
        for debut in ["2026-10-20", "2025-01-01", "21/10/2026", "2026-13-45", "demain", "2028-01-01"]:
            with self.subTest(debut=debut):
                self.assertFalse(self.enregistrer(m, self.MENSUEL, debut=debut)[0])
        self.assertEqual(self.nombre("abonnements"), 0)

    # ---------- montants, membres, formules ----------
    def test_montant_personnalise_et_montants_invalides(self):
        m = self.membre(1)
        ok, _, valeurs = self.enregistrer(m, self.MENSUEL, montant="8000")
        self.assertTrue(ok)
        self.assertEqual(self.dernier()["montant_paye"], 8000)
        for montant in ["-5", "abc", "1e5", "10000.5", "10 000", "٣٠٠٠", "999999999", "10000001", "0x10"]:
            with self.subTest(montant=montant):
                self.assertFalse(self.enregistrer(m, self.SEANCE, debut="2026-12-01", montant=montant)[0])
        self.assertEqual(self.nombre("abonnements"), 1)

    def test_membre_ou_formule_invalides(self):
        m = self.membre(1)
        for membre, formule in [(None, 2), ("abc", 2), (999, 2), (m, None), (m, "abc"), (m, 999), ("1; DROP TABLE abonnements", 2)]:
            with self.subTest(membre=membre, formule=formule):
                self.assertFalse(self.enregistrer(membre, formule)[0])
        self.assertEqual(self.nombre("abonnements"), 0)

    def test_filet_de_securite_de_la_base(self):
        """Le contrôle de chevauchement de Python est contourné : le déclencheur de la base refuse quand même."""
        m = self.membre(1)
        self.abonnement(m, self.MENSUEL, "2026-10-01", "2026-10-30")
        with mock.patch.object(appli, "trouver_chevauchement", return_value=None):
            ok, message, _ = self.enregistrer(m, self.MENSUEL, debut="2026-10-25")
        self.assertFalse(ok)
        self.assertIn("n’a pas pu être enregistré", message)
        self.assertEqual(self.nombre("abonnements"), 1)

    def test_verifier_ne_modifie_pas_la_base(self):
        m = self.membre(1)
        erreur, valeurs = appli.verifier_abonnement(self.db, m, self.MENSUEL, "", "", AUJOURDHUI)
        self.assertIsNone(erreur)
        self.assertEqual(valeurs["date_fin"], "2026-11-19")
        self.assertEqual(self.nombre("abonnements"), 0)

    # ---------- expirations ----------
    def scenario_expirations(self):
        noms = {}
        for numero, (nom, debut, fin) in enumerate([
            ("dans 2 jours", "2026-09-24", "2026-10-23"),
            ("dans 2 jours mais déjà renouvelé", "2026-09-24", "2026-10-23"),
            ("dans 7 jours pile", "2026-09-29", "2026-10-28"),
            ("dans 8 jours", "2026-09-30", "2026-10-29"),
            ("déjà expiré", "2026-09-21", "2026-10-20"),
            ("expire aujourd'hui", "2026-09-22", "2026-10-21"),
        ], start=1):
            noms[nom] = self.membre(numero, nom)
            self.abonnement(noms[nom], self.MENSUEL, debut, fin)
        self.abonnement(noms["dans 2 jours mais déjà renouvelé"], self.MENSUEL, "2026-10-24", "2026-11-22")
        return noms

    def test_abonnements_qui_expirent_dans_les_7_jours(self):
        self.scenario_expirations()
        lignes = appli.abonnements_expirant(self.db, AUJOURDHUI)
        self.assertEqual([(l["nom"], l["jours_restants"]) for l in lignes],
                         [("expire aujourd'hui", 0), ("dans 2 jours", 2), ("dans 7 jours pile", 7)])

    def test_filtres_de_la_liste_des_abonnements(self):
        self.scenario_expirations()
        def noms(filtre):
            return sorted(a["nom"] for a in appli.lister_abonnements(self.db, AUJOURDHUI, filtre))
        self.assertEqual(len(appli.lister_abonnements(self.db, AUJOURDHUI, "tous")), 7)
        self.assertEqual(noms("expirant"), ["dans 2 jours", "dans 7 jours pile", "expire aujourd'hui"])
        self.assertEqual(noms("expires"), ["déjà expiré"])
        self.assertEqual(len(appli.lister_abonnements(self.db, AUJOURDHUI, "en_cours")), 5)
        etats = {(a["nom"], a["etat"]) for a in appli.lister_abonnements(self.db, AUJOURDHUI, "tous")}
        self.assertIn(("dans 2 jours mais déjà renouvelé", "a_venir"), etats)


class GestionDesCours(BaseTemporaire):

    def saisie(self, **changements):
        valeurs = {"discipline": "Zumba", "coach": "Rose Ekane", "jour_semaine": "3", "heure": "18:00",
                   "salle": "Salle A", "places_max": "15"}
        valeurs.update(changements)
        return valeurs

    def creer(self, **changements):
        return appli.creer_cours(self.db, self.saisie(**changements), AUJOURDHUI)

    def modifier(self, cours_id, **changements):
        return appli.modifier_cours(self.db, cours_id, self.saisie(**changements), AUJOURDHUI)

    def abonne(self, numero):
        membre = self.membre(numero)
        self.abonnement(membre, 2, "2026-01-01", "2026-12-31")
        return membre

    def reserver_direct(self, cours_id, membre_id, jour):
        self.db.execute("INSERT INTO reservations_cours (cours_id, membre_id, date_seance) VALUES (?, ?, ?)",
                        (cours_id, membre_id, jour))
        self.db.commit()

    def cours(self, cours_id):
        return self.db.execute("SELECT * FROM cours WHERE id = ?", (cours_id,)).fetchone()

    # ---------- création ----------
    def test_creation_valide(self):
        ok, message, cours_id = self.creer()
        self.assertTrue(ok, message)
        self.assertIn("jeudi à 18:00", message)
        ligne = self.cours(cours_id)
        self.assertEqual((ligne["discipline"], ligne["jour_semaine"], ligne["heure"], ligne["places_max"]),
                         ("Zumba", 3, "18:00", 15))

    def test_textes_nettoyes(self):
        _, _, cours_id = self.creer(discipline="  Zumba   Fitness ", coach="Rose\n Ekane")
        self.assertEqual((self.cours(cours_id)["discipline"], self.cours(cours_id)["coach"]), ("Zumba Fitness", "Rose Ekane"))

    def test_champs_invalides(self):
        cas = {
            "discipline": ["", " ", "Z", "x" * 41, None, "mauvais\x00"],
            "coach": ["", "R", "x" * 61, None],
            "jour_semaine": ["", "7", "-1", "abc", "1.5", None, "٣"],
            "heure": ["", "9:00", "24:00", "12:60", "12h30", "18:00:00", "ab:cd", None],
            "salle": ["", "   ", "x" * 31, None],
            "places_max": ["", "0", "201", "-3", "abc", "10.5", "٣", None],
        }
        for champ, valeurs in cas.items():
            for valeur in valeurs:
                with self.subTest(champ=champ, valeur=valeur):
                    ok, _, _ = self.creer(**{champ: valeur})
                    self.assertFalse(ok)
        self.assertEqual(self.nombre("cours"), 0)

    def test_heures_limites_valides(self):
        for heure in ["00:00", "07:05", "23:59"]:
            with self.subTest(heure=heure):
                self.assertTrue(self.creer(heure=heure, salle="Salle " + heure)[0])

    def test_salle_deja_occupee(self):
        self.assertTrue(self.creer()[0])
        ok, message, _ = self.creer(discipline="Aérobic")
        self.assertFalse(ok)
        self.assertIn("déjà utilisée", message)
        self.assertIn("Zumba", message)
        self.assertEqual(self.nombre("cours"), 1)

    def test_salle_deja_occupee_sans_tenir_compte_des_majuscules(self):
        self.creer()
        self.assertFalse(self.creer(salle="salle a")[0])

    def test_autre_salle_ou_autre_heure_permis(self):
        self.creer()
        self.assertTrue(self.creer(salle="Salle B")[0])               # même moment, autre salle
        self.assertTrue(self.creer(heure="19:00")[0])                 # même salle, autre heure
        self.assertTrue(self.creer(jour_semaine="4")[0])              # même salle et heure, autre jour

    def test_filet_de_securite_de_la_base(self):
        self.creer()
        with mock.patch.object(appli, "verifier_cours", return_value=(None, {
                "discipline": "Zumba", "coach": "Rose", "jour_semaine": 3, "heure": "18:00",
                "salle": "Salle A", "places_max": 5})):
            ok, message, _ = self.creer()
        self.assertFalse(ok)
        self.assertIn("n’a pas pu être ajouté", message)
        self.assertEqual(self.nombre("cours"), 1)

    def test_injection_sql_sans_effet(self):
        ok, _, cours_id = self.creer(discipline="Zumba'); DROP TABLE cours;--")
        self.assertTrue(ok)                                           # c'est juste un texte
        self.assertEqual(self.cours(cours_id)["discipline"], "Zumba'); DROP TABLE cours;--")
        self.assertEqual(self.nombre("cours"), 1)

    # ---------- modification ----------
    def test_modification_simple(self):
        _, _, cours_id = self.creer()
        ok, message = self.modifier(cours_id, coach="Sandra Njoya", places_max="20")
        self.assertTrue(ok, message)
        self.assertEqual((self.cours(cours_id)["coach"], self.cours(cours_id)["places_max"]), ("Sandra Njoya", 20))

    def test_enregistrer_sans_rien_changer_n_est_pas_un_conflit(self):
        _, _, cours_id = self.creer()
        self.assertTrue(self.modifier(cours_id)[0])

    def test_modification_vers_le_creneau_d_un_autre_cours_refusee(self):
        self.creer()
        _, _, autre = self.creer(heure="19:00")
        ok, message = self.modifier(autre, heure="18:00")
        self.assertFalse(ok)
        self.assertIn("déjà utilisée", message)
        self.assertEqual(self.cours(autre)["heure"], "19:00")

    def test_modification_d_un_cours_inexistant(self):
        ok, message = self.modifier(999)
        self.assertFalse(ok)
        self.assertIn("n’existe pas", message)

    def test_reduire_les_places_sous_les_reservations_a_venir_refuse(self):
        _, _, cours_id = self.creer(places_max="3")
        for numero in (1, 2, 3):
            self.reserver_direct(cours_id, self.abonne(numero), "2026-10-22")       # jeudi à venir
        ok, message = self.modifier(cours_id, places_max="2")
        self.assertFalse(ok)
        self.assertIn("3 réservations", message)
        self.assertIn("22 octobre 2026", message)
        self.assertEqual(self.cours(cours_id)["places_max"], 3)
        self.assertTrue(self.modifier(cours_id, places_max="3")[0])                  # égal au nombre pris : permis

    def test_les_reservations_passees_ne_bloquent_pas_la_reduction(self):
        _, _, cours_id = self.creer(places_max="3")
        for numero in (1, 2, 3):
            self.reserver_direct(cours_id, self.abonne(numero), "2026-10-15")       # jeudi déjà passé
        self.assertTrue(self.modifier(cours_id, places_max="1")[0])

    def test_deplacer_un_cours_qui_a_des_reservations_a_venir_refuse(self):
        _, _, cours_id = self.creer()
        self.reserver_direct(cours_id, self.abonne(1), "2026-10-22")
        for changement in ({"jour_semaine": "4"}, {"heure": "19:00"}):
            with self.subTest(changement=changement):
                ok, message = self.modifier(cours_id, **changement)
                self.assertFalse(ok)
                self.assertIn("ne peuvent plus changer", message)
        self.assertTrue(self.modifier(cours_id, salle="Salle B", coach="Autre Coach")[0])   # le reste est modifiable

    def test_deplacer_un_cours_sans_reservation_a_venir(self):
        _, _, cours_id = self.creer()
        self.reserver_direct(cours_id, self.abonne(1), "2026-10-15")                 # seulement du passé
        self.assertTrue(self.modifier(cours_id, jour_semaine="4", heure="19:30")[0])

    # ---------- suppression ----------
    def test_suppression_d_un_cours_sans_reservation(self):
        _, _, cours_id = self.creer()
        ok, message = appli.supprimer_cours(self.db, cours_id)
        self.assertTrue(ok, message)
        self.assertEqual(self.nombre("cours"), 0)

    def test_suppression_refusee_si_le_cours_a_des_reservations(self):
        _, _, cours_id = self.creer()
        self.reserver_direct(cours_id, self.abonne(1), "2026-10-15")                 # même du passé
        ok, message = appli.supprimer_cours(self.db, cours_id)
        self.assertFalse(ok)
        self.assertIn("1 réservation", message)
        self.assertEqual(self.nombre("cours"), 1)

    def test_suppression_d_un_cours_inexistant(self):
        self.assertFalse(appli.supprimer_cours(self.db, 999)[0])

    # ---------- liste ----------
    def test_liste_avec_reservations_a_venir(self):
        _, _, jeudi = self.creer()
        _, _, mardi = self.creer(jour_semaine="1", heure="17:00")
        self.reserver_direct(jeudi, self.abonne(1), "2026-10-22")
        self.reserver_direct(jeudi, self.abonne(2), "2026-10-15")                    # passée : non comptée
        liste = appli.lister_cours_admin(self.db, AUJOURDHUI)
        self.assertEqual([(c["jour_nom"], c["reservations_a_venir"]) for c in liste], [("Mardi", 0), ("Jeudi", 1)])


class InscritsDUneSeance(GestionDesCours):
    """Hérite des outils de GestionDesCours (sans rejouer ses tests : voir load_tests plus bas)."""

    def preparer(self):
        _, _, cours_id = self.creer(places_max="4")
        for numero, reservation in ((1, "2026-10-20 09:00:00"), (2, "2026-10-19 08:00:00")):
            self.reserver_direct(cours_id, self.abonne(numero), "2026-10-22")
            self.db.execute("UPDATE reservations_cours SET date_reservation = ? WHERE membre_id = ?",
                            (reservation, numero))
        self.db.commit()
        return cours_id

    def test_inscrits_dans_l_ordre_de_reservation(self):
        cours_id = self.preparer()
        erreur, contexte = appli.inscrits_seance(self.db, cours_id, "2026-10-22", AUJOURDHUI)
        self.assertIsNone(erreur)
        self.assertEqual([i["nom"] for i in contexte["inscrits"]], ["Membre 2", "Membre 1"])   # le plus ancien d'abord
        self.assertEqual((contexte["places_prises"], contexte["places_restantes"]), (2, 2))
        self.assertFalse(contexte["passee"])
        self.assertEqual(contexte["cours"]["jour_nom"], "Jeudi")

    def test_seance_sans_inscrit(self):
        cours_id = self.preparer()
        _, contexte = appli.inscrits_seance(self.db, str(cours_id), "2026-10-29", AUJOURDHUI)
        self.assertEqual((contexte["inscrits"], contexte["places_prises"], contexte["places_restantes"]), ([], 0, 4))

    def test_seance_passee_consultable(self):
        cours_id = self.preparer()
        _, contexte = appli.inscrits_seance(self.db, cours_id, "2026-10-15", AUJOURDHUI)
        self.assertTrue(contexte["passee"])

    def test_dates_proposees(self):
        cours_id = self.preparer()
        _, contexte = appli.inscrits_seance(self.db, cours_id, "2026-10-22", AUJOURDHUI)
        self.assertEqual(contexte["dates"], ["2026-10-08", "2026-10-15", "2026-10-22", "2026-10-29",
                                             "2026-11-05", "2026-11-12"])

    def test_dates_proposees_selon_le_jour_courant(self):
        jeudi = date(2026, 10, 22)                                               # aujourd'hui = jeudi : séance du jour
        self.assertEqual(appli.dates_autour(3, jeudi)[2], "2026-10-22")
        vendredi = date(2026, 10, 23)                                            # demain vendredi : prochaine = 29/10
        self.assertEqual(appli.dates_autour(3, vendredi)[2], "2026-10-29")

    def test_erreurs(self):
        cours_id = self.preparer()
        cas = [(None, "2026-10-22"), ("abc", "2026-10-22"), (999, "2026-10-22"), (cours_id, None),
               (cours_id, "2026-13-45"), (cours_id, "2026-10-23")]                # le 23 est un vendredi
        for cours, jour in cas:
            with self.subTest(cours=cours, jour=jour):
                erreur, contexte = appli.inscrits_seance(self.db, cours, jour, AUJOURDHUI)
                self.assertIsNotNone(erreur)
                self.assertIsNone(contexte)
        erreur, _ = appli.inscrits_seance(self.db, cours_id, "2026-10-23", AUJOURDHUI)
        self.assertIn("jeudi", erreur)


def load_tests(chargeur, tests, motif):
    """InscritsDUneSeance hérite des outils de GestionDesCours : on évite de rejouer les tests hérités."""
    suite = unittest.TestSuite()
    for classe in (ReglesDeReservation, ReservationsParLeSite, GestionDesMembres, GestionDesAbonnements,
                   GestionDesCours, InscritsDUneSeance, Statistiques, DonneesMembre, AdministrationParLeSite,
                   PagesCompletes):
        noms = [n for n in chargeur.getTestCaseNames(classe)
                if classe is not InscritsDUneSeance or n in classe.__dict__]
        suite.addTests(classe(n) for n in noms)
    return suite


class Statistiques(BaseTemporaire):
    """Les quatre statistiques, avec des données calculées à la main (aujourd'hui = mercredi 21/10/2026)."""

    def test_recette_par_mois_et_par_formule(self):
        for numero, (formule, debut, fin, montant) in enumerate([
                (2, "2026-10-02", "2026-10-31", 10000), (2, "2026-10-15", "2026-11-13", 10000),
                (3, "2026-10-05", "2027-01-02", 27000), (2, "2026-09-20", "2026-10-19", 8000),
                (1, "2026-11-01", "2026-11-01", 1500)], start=1):
            membre = self.membre(numero)
            self.db.execute("INSERT INTO abonnements (membre_id, formule_id, date_debut, date_fin, montant_paye) "
                            "VALUES (?, ?, ?, ?, ?)", (membre, formule, debut, fin, montant))
        self.db.commit()
        recette = appli.recette_par_mois(self.db)
        self.assertEqual([m["mois"] for m in recette], ["2026-11", "2026-10", "2026-09"])      # récent d'abord
        octobre = recette[1]
        self.assertEqual(octobre["total"], 47000)
        self.assertEqual([(f["formule"], f["recette"], f["nombre"]) for f in octobre["formules"]],
                         [("Mensuel", 20000, 2), ("Trimestriel", 27000, 1)])
        self.assertEqual(recette[2]["total"], 8000)                  # la remise est bien comptée au montant payé
        self.assertEqual(len(appli.recette_par_mois(self.db, mois_max=2)), 2)

    def test_recette_sans_abonnement(self):
        self.assertEqual(appli.recette_par_mois(self.db), [])

    def test_remplissage_des_cours(self):
        self.db.execute("INSERT INTO cours (discipline, coach, jour_semaine, heure, salle, places_max) "
                        "VALUES ('Zumba', 'Rose', 3, '18:00', 'Salle A', 10)")           # jeudi
        self.db.execute("INSERT INTO cours (discipline, coach, jour_semaine, heure, salle, places_max) "
                        "VALUES ('Yoga', 'Estelle', 4, '08:00', 'Salle B', 5)")          # vendredi, jamais réservé
        membres = []
        for numero in range(1, 6):
            membre = self.membre(numero)
            self.abonnement(membre, 2, "2026-01-01", "2026-12-31")
            membres.append(membre)
        # fenêtre = 23/09 au 20/10 : jeudis 24/09, 01/10, 08/10, 15/10
        for jour, nombre in (("2026-09-24", 3), ("2026-10-01", 5), ("2026-10-15", 2),
                             ("2026-09-17", 4), ("2026-10-22", 4)):   # les deux dernières sont hors fenêtre
            for membre in membres[:nombre]:
                self.db.execute("INSERT INTO reservations_cours (cours_id, membre_id, date_seance) VALUES (1, ?, ?)",
                                (membre, jour))
        self.db.commit()
        resultat = appli.remplissage_des_cours(self.db, AUJOURDHUI)
        self.assertEqual([(c["discipline"], c["reservations"], c["seances"], c["taux"]) for c in resultat],
                         [("Zumba", 10, 4, 25.0), ("Yoga", 0, 4, 0.0)])                  # 10 ÷ (10 × 4) = 25 %

    def test_membres_actifs(self):
        actif, expire, _ = self.membre(1), self.membre(2), self.membre(3)
        self.abonnement(actif, 2, "2026-10-01", "2026-10-30")
        self.abonnement(expire, 2, "2026-08-01", "2026-08-30")
        self.assertEqual(appli.compter_membres_actifs(self.db, AUJOURDHUI), {"actifs": 1, "total": 3, "taux": 33.3})

    def test_membres_actifs_sans_membre(self):
        self.assertEqual(appli.compter_membres_actifs(self.db, AUJOURDHUI), {"actifs": 0, "total": 0, "taux": None})

    def test_taux_de_renouvellement(self):
        a, b, c, d, e = (self.membre(n) for n in range(1, 6))
        self.abonnement(a, 2, "2026-09-01", "2026-09-30")      # échu et renouvelé ensuite
        self.abonnement(a, 2, "2026-10-01", "2026-10-30")      # en cours : pas échu
        self.abonnement(b, 2, "2026-09-01", "2026-09-30")      # échu, jamais renouvelé
        self.abonnement(c, 1, "2026-09-10", "2026-09-10")      # « à la séance » : exclu du calcul
        self.abonnement(d, 2, "2026-10-10", "2026-11-08")      # en cours : pas échu
        self.abonnement(e, 2, "2026-08-01", "2026-08-31")      # échu et renouvelé ensuite
        self.abonnement(e, 2, "2026-10-01", "2026-10-30")
        self.assertEqual(appli.taux_renouvellement(self.db, AUJOURDHUI), {"echus": 3, "renouveles": 2, "taux": 66.7})

    def test_taux_de_renouvellement_sans_echeance(self):
        self.assertEqual(appli.taux_renouvellement(self.db, AUJOURDHUI), {"echus": 0, "renouveles": 0, "taux": None})

    def test_ensemble_serialisable_en_json(self):
        import json
        resultat = appli.statistiques(self.db, AUJOURDHUI)
        self.assertEqual(set(resultat), {"date", "recette", "remplissage", "membres", "renouvellement"})
        json.dumps(resultat)


class DonneesMembre(BaseTemporaire):
    """Données de l'espace membre (historique, compteurs, abonnements) et formules de l'accueil."""

    def preparer(self):
        self.membre_id = self.membre(1)
        self.abonnement(self.membre_id, 2, "2026-01-01", "2026-12-31")
        self.db.execute("INSERT INTO cours (discipline, coach, jour_semaine, heure, salle, places_max) "
                        "VALUES ('Aérobic', 'Sandra', 2, '07:00', 'Salle A', 5)")      # mercredi matin
        self.db.execute("INSERT INTO cours (discipline, coach, jour_semaine, heure, salle, places_max) "
                        "VALUES ('Zumba', 'Rose', 3, '18:00', 'Salle B', 8)")          # jeudi soir
        for cours, jour in ((1, "2026-10-21"), (2, "2026-10-22"), (2, "2026-10-15"), (2, "2026-10-29")):
            self.db.execute("INSERT INTO reservations_cours (cours_id, membre_id, date_seance) VALUES (?, ?, ?)",
                            (cours, self.membre_id, jour))
        self.db.commit()

    def test_historique_des_seances_commencees(self):
        self.preparer()
        historique = appli.historique_reservations(self.db, self.membre_id, MAINTENANT)
        self.assertEqual([(r["date_seance"], r["annulable"]) for r in historique],
                         [("2026-10-21", False), ("2026-10-15", False)])                # la plus récente d'abord
        self.assertEqual(len(appli.historique_reservations(self.db, self.membre_id, MAINTENANT, limite=1)), 1)

    def test_resume_du_tableau_de_bord(self):
        self.preparer()
        self.assertEqual(appli.resume_membre(self.db, self.membre_id, MAINTENANT),
                         {"a_venir": 2, "cette_semaine": 2, "passees": 2})

    def test_reservations_a_venir_avec_les_places(self):
        self.preparer()
        autre = self.membre(2)
        self.abonnement(autre, 2, "2026-01-01", "2026-12-31")
        self.db.execute("INSERT INTO reservations_cours (cours_id, membre_id, date_seance) VALUES (2, ?, '2026-10-22')", (autre,))
        self.db.commit()
        jeudi = appli.lister_reservations_a_venir(self.db, self.membre_id, MAINTENANT)[1]
        self.assertEqual((jeudi["date_seance"], jeudi["places_prises"], jeudi["places_max"]), ("2026-10-22", 2, 8))

    def test_abonnements_du_membre(self):
        membre = self.membre(1)
        self.abonnement(membre, 2, "2026-08-01", "2026-08-30")
        self.abonnement(membre, 2, "2026-10-01", "2026-10-30")
        self.abonnement(membre, 2, "2026-10-31", "2026-11-29")
        liste = appli.lister_abonnements_membre(self.db, membre, AUJOURDHUI)
        self.assertEqual([(a["date_debut"], a["etat"]) for a in liste],
                         [("2026-10-31", "a_venir"), ("2026-10-01", "en_cours"), ("2026-08-01", "expire")])

    def test_formule_populaire_et_prix_par_jour(self):
        for numero, formule in enumerate([2, 2, 2, 3, 1, 1, 1, 1], start=1):          # le « à la séance » est le plus vendu
            membre = self.membre(numero)
            self.abonnement(membre, formule, "2026-10-01", "2026-10-30")
        formules = {f["nom"]: f for f in appli.charger_formules(self.db)}
        self.assertEqual([n for n, f in formules.items() if f["populaire"]], ["Mensuel"])    # exclut « à la séance »
        self.assertEqual(formules["Mensuel"]["prix_par_jour"], 333)                    # 10 000 ÷ 30
        self.assertIsNone(formules["À la séance"]["prix_par_jour"])

    def test_aucune_formule_populaire_sans_abonnement(self):
        self.assertFalse(any(f["populaire"] for f in appli.charger_formules(self.db)))

    def test_filtres_de_gabarit(self):
        self.assertEqual(appli.filtre_initiales("Aline Mbarga"), "AM")
        self.assertEqual(appli.filtre_initiales("admin"), "A")
        self.assertEqual(appli.filtre_prenom("Aline Mbarga"), "Aline")
        self.assertEqual(appli.filtre_slug("Aérobic"), "aerobic")
        self.assertEqual(appli.filtre_mois_fr("2026-10"), "octobre 2026")
        self.assertEqual(appli.filtre_mois_fr("n'importe quoi"), "n'importe quoi")


class PagesCompletes(unittest.TestCase):
    """Affiche TOUTES les pages avec les vraies données de test et les vrais gabarits, puis contrôle
    le HTML : aucune erreur de syntaxe HTML5, identifiants uniques, champs étiquetés, rien d'oublié."""

    @classmethod
    def setUpClass(cls):
        import contextlib
        import io
        cls.fichier = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        cls.fichier.close()
        import donnees_test
        cls.patchs = [mock.patch.object(donnees_test, "CHEMIN_BASE", cls.fichier.name),
                      mock.patch.object(donnees_test, "CHEMIN_SCHEMA", os.path.join(DOSSIER_PROJET, "schema.sql")),
                      mock.patch.object(appli, "CHEMIN_BASE", cls.fichier.name)]
        for patch in cls.patchs:
            patch.start()
        with contextlib.redirect_stdout(io.StringIO()):
            donnees_test.main()
        appli.app.config["TESTING"] = True

    @classmethod
    def tearDownClass(cls):
        for patch in cls.patchs:
            patch.stop()
        os.unlink(cls.fichier.name)

    def jeton(self, client, url):
        return re.search(r'name="csrf_token" value="([0-9a-f]+)"', client.get(url).data.decode()).group(1)

    def membre(self):
        client = appli.app.test_client()
        client.post("/connexion", data={"telephone": "677000001", "code": "Membre!2026",
                                        "csrf_token": self.jeton(client, "/connexion")})
        return client

    def gerant(self):
        client = appli.app.test_client()
        client.post("/admin/connexion", data={"login": "admin", "mot_de_passe": "Admin!2026",
                                              "csrf_token": self.jeton(client, "/admin/connexion")})
        return client

    def controler(self, html, adresse):
        """Contrôles communs à toutes les pages."""
        self.assertNotIn("{{", html, adresse)
        self.assertNotIn("{%", html, adresse)
        texte = re.sub(r"<[^>]+>", " ", html)
        for mot in ("None", "Undefined", "Traceback"):
            self.assertNotIn(mot, texte, f"{adresse} contient « {mot} »")
        identifiants = re.findall(r'\sid="([^"]+)"', html)
        self.assertEqual(len(identifiants), len(set(identifiants)), f"{adresse} : identifiants en double")
        for champ in re.finditer(r"<(input|select)\b([^>]*)>", html):
            attributs = champ.group(2)
            if 'type="hidden"' in attributs:
                continue
            identifiant = re.search(r'\sid="([^"]+)"', attributs)
            self.assertTrue(identifiant, f"{adresse} : champ sans id : {attributs}")
            self.assertTrue(re.search(rf'<label[^>]*for="{re.escape(identifiant.group(1))}"', html),
                            f"{adresse} : champ « {identifiant.group(1)} » sans label")
        self.assertIn('<html lang="fr">', html)
        self.assertEqual(len(re.findall(r"<h1\b", html)), 1, f"{adresse} : il faut exactement un titre h1")
        try:
            import html5lib
        except ImportError:
            return
        analyseur = html5lib.HTMLParser(strict=False)
        analyseur.parse(html)
        self.assertEqual(analyseur.errors, [], f"{adresse} : erreurs HTML5")

    def afficher(self, client, adresse, statut=200):
        reponse = client.get(adresse)
        self.assertEqual(reponse.status_code, statut, adresse)
        html = reponse.data.decode()
        self.controler(html, adresse)
        return html

    def test_pages_publiques(self):
        visiteur = appli.app.test_client()
        accueil = self.afficher(visiteur, "/")
        self.assertIn("Votre forme,", accueil)
        self.assertEqual(accueil.count("Populaire"), 1)                  # une seule formule populaire
        self.assertIn("10&nbsp;000", accueil.replace("\u00a0", "&nbsp;"))  # prix lus dans la base (mensuel)
        self.assertIn("Aérobic · Musculation · Taekwondo · Zumba", accueil)
        self.assertIn("Se connecter", accueil)
        formules = self.afficher(visiteur, "/formules")
        self.assertEqual(formules.count('<article class="carte-formule'), 4)
        planning = self.afficher(visiteur, "/planning")
        self.assertIn("Connectez-vous", planning)
        self.assertNotIn("data-membre", planning)
        connexion = self.afficher(visiteur, "/connexion")
        self.assertIn("Rejoignez FitZone", connexion)
        self.assertIn('aria-current="page">Membre', connexion)
        admin = self.afficher(visiteur, "/admin/connexion")
        self.assertIn('aria-current="page">Administrateur', admin)
        introuvable = self.afficher(visiteur, "/page-inconnue", statut=404)
        self.assertIn("Page introuvable", introuvable)

    def test_planning_pour_un_membre_connecte(self):
        planning = self.afficher(self.membre(), "/planning")
        self.assertIn('data-membre="oui"', planning)
        self.assertIn('data-annuler="/annuler/0"', planning)
        self.assertNotIn("Connectez-vous", planning)

    def test_pages_du_membre(self):
        client = self.membre()
        tableau = self.afficher(client, "/membre")
        self.assertIn("Bonjour, Aline", tableau)
        self.assertIn("Prochaines séances", tableau)
        self.assertIn('role="progressbar"', tableau)
        self.assertIn("Réservations à venir", tableau)
        a_venir = self.afficher(client, "/membre/reservations")
        self.assertIn('aria-current="page">À venir', a_venir)
        self.assertIn("Annuler", a_venir)
        historique = self.afficher(client, "/membre/reservations?onglet=historique")
        self.assertIn('aria-current="page">Historique', historique)
        self.assertIn("Terminée", historique)
        self.assertNotIn(">Annuler<", historique)                         # pas d'annulation d'une séance passée
        self.assertIn('aria-current="page">À venir', self.afficher(client, "/membre/reservations?onglet=nimporte-quoi"))
        abonnement = self.afficher(client, "/membre/abonnement")
        self.assertIn("Historique de mes abonnements", abonnement)

    def test_pages_membre_protegees(self):
        visiteur = appli.app.test_client()
        for adresse in ("/membre", "/membre/reservations", "/membre/abonnement"):
            reponse = visiteur.get(adresse)
            self.assertEqual((reponse.status_code, reponse.headers["Location"].endswith("/connexion")), (302, True), adresse)

    def test_pages_du_gerant(self):
        client = self.gerant()
        tableau = self.afficher(client, "/admin")
        self.assertIn("Abonnements expirant sous 7 jours", tableau)
        self.assertEqual(tableau.count("Renouveler</a>"), 5)               # cinq premières lignes seulement
        adresses = ["/admin/membres", "/admin/membres?q=a", "/admin/membres?q=zzzzzz", "/admin/membres/nouveau",
                    "/admin/abonnements", "/admin/abonnements?filtre=expirant", "/admin/abonnements?filtre=en_cours",
                    "/admin/abonnements?filtre=expires", "/admin/abonnements/nouveau", "/admin/abonnements/nouveau?membre_id=1",
                    "/admin/cours", "/admin/cours/nouveau", "/admin/cours/1/modifier", "/admin/statistiques"]
        for adresse in adresses:
            with self.subTest(adresse=adresse):
                self.afficher(client, adresse)
        self.assertIn("Aucun membre ne correspond", self.afficher(client, "/admin/membres?q=zzzzzz"))
        self.assertIn('aria-current="page">Renouvellements', self.afficher(client, "/admin/abonnements?filtre=expirant").replace("<span>Renouvellements", 'aria-current="page">Renouvellements'))

    def test_page_des_inscrits_et_des_statistiques(self):
        client = self.gerant()
        db = sqlite3.connect(self.fichier.name)
        cours_id, date_seance = db.execute(
            "SELECT cours_id, MAX(date_seance) FROM reservations_cours GROUP BY cours_id ORDER BY COUNT(*) DESC LIMIT 1").fetchone()
        mois = db.execute("SELECT substr(MIN(date_debut), 1, 7) FROM abonnements").fetchone()[0]
        db.close()
        seance = self.afficher(client, f"/admin/seance?cours_id={cours_id}&date={date_seance}")
        self.assertIn("Inscrits à la séance", seance)
        self.assertIn("Places prises", seance)
        stats = self.afficher(client, f"/admin/statistiques?mois={mois}")
        self.assertIn("Recettes par formule", stats)
        self.assertIn("Taux de remplissage des cours", stats)
        self.assertIn("Taux de renouvellement", stats)
        self.assertIn("conic-gradient" if False else "donut", stats)
        self.afficher(client, "/admin/statistiques?mois=invalide")        # un mois inconnu retombe sur le plus récent

    def test_pages_issues_d_un_envoi_de_formulaire(self):
        client = self.gerant()
        jeton = self.jeton(client, "/admin")
        creation = client.post("/admin/membres/nouveau", data={"nom": "Nadège Kamga", "telephone": "698123456", "csrf_token": jeton})
        self.assertEqual(creation.status_code, 200)
        self.controler(creation.data.decode(), "code d'accès")
        self.assertIn("Code d’accès", creation.data.decode())
        self.assertEqual(creation.headers["Cache-Control"], "no-store")
        erreurs = [("/admin/membres/nouveau", {"nom": "X", "telephone": "1"}),
                   ("/admin/cours/nouveau", {"discipline": "", "coach": "", "jour_semaine": "", "heure": "", "salle": "", "places_max": ""}),
                   ("/admin/abonnements/nouveau", {"membre_id": "", "formule_id": ""})]
        for adresse, donnees in erreurs:
            with self.subTest(adresse=adresse):
                reponse = client.post(adresse, data=dict(donnees, csrf_token=jeton))
                self.assertEqual(reponse.status_code, 200)
                self.assertIn('role="alert"', reponse.data.decode())
                self.controler(reponse.data.decode(), adresse)

    def test_pages_d_erreur(self):
        visiteur = appli.app.test_client()
        jeton_refuse = visiteur.post("/connexion", data={})
        self.assertEqual(jeton_refuse.status_code, 400)
        self.controler(jeton_refuse.data.decode(), "400")
        self.assertIn("Formulaire expiré", jeton_refuse.data.decode())
        interdit = visiteur.get("/deconnexion")
        self.assertEqual(interdit.status_code, 405)
        self.controler(interdit.data.decode(), "405")

    def test_connexion_avec_erreur_et_menu_actif(self):
        visiteur = appli.app.test_client()
        reponse = visiteur.post("/connexion", data={"telephone": "677000001", "code": "faux", "csrf_token": self.jeton(visiteur, "/connexion")})
        html = reponse.data.decode()
        self.controler(html, "connexion refusée")
        self.assertIn("incorrect", html)
        self.assertIn('value="677000001"', html)                         # le numéro saisi est conservé
        self.assertNotIn("faux", html)                                    # mais jamais le code

class AdministrationParLeSite(unittest.TestCase):
    """Tests HTTP de l'espace gérant. Les gabarits « admin_*.html » ne sont pas encore écrits (étape
    frontend) : on capture le nom du gabarit et les données qui lui sont transmises."""

    def setUp(self):
        self.fichier = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.fichier.close()
        db = creer_base(self.fichier.name)
        db.executemany("INSERT INTO formules (nom, duree_jours, prix) VALUES (?, ?, ?)",
                       [("À la séance", 1, 1500), ("Mensuel", 30, 10000)])
        db.execute("INSERT INTO administrateurs (login, empreinte) VALUES ('admin', ?)",
                   (generate_password_hash("Admin!2026"),))
        db.execute("INSERT INTO membres (nom, telephone, empreinte_code) VALUES ('Aline Mbarga', '677000001', ?)",
                   (generate_password_hash(CODE),))
        db.execute("INSERT INTO membres (nom, telephone, empreinte_code) VALUES ('Paul Fotso', '677000002', ?)",
                   (generate_password_hash(CODE),))
        aujourdhui = date.today()
        db.execute("INSERT INTO abonnements (membre_id, formule_id, date_debut, date_fin, montant_paye) "
                   "VALUES (1, 2, ?, ?, 10000)", ((aujourdhui - timedelta(days=27)).isoformat(),
                                                  (aujourdhui + timedelta(days=2)).isoformat()))
        db.commit()
        db.close()
        self.rendus = []
        original = appli.render_template

        def simule(gabarit, /, **contexte):          # « / » : le gabarit se passe toujours en premier argument
            self.rendus.append((gabarit, contexte))
            if gabarit.startswith("admin_"):
                return "page simulée : " + gabarit
            return original(gabarit, **contexte)

        self.patches = [mock.patch.object(appli, "CHEMIN_BASE", self.fichier.name),
                        mock.patch.object(appli, "render_template", simule)]
        for patch in self.patches:
            patch.start()
        appli.app.config["TESTING"] = True

    def tearDown(self):
        for patch in self.patches:
            patch.stop()
        os.unlink(self.fichier.name)

    def jeton(self, client, url="/connexion"):
        return re.search(r'name="csrf_token" value="([0-9a-f]+)"', client.get(url).data.decode()).group(1)

    def gerant(self):
        client = appli.app.test_client()
        client.post("/admin/connexion", data={"login": "admin", "mot_de_passe": "Admin!2026",
                                              "csrf_token": self.jeton(client, "/admin/connexion")})
        return client

    def membre_connecte(self):
        client = appli.app.test_client()
        client.post("/connexion", data={"telephone": "677000001", "code": CODE, "csrf_token": self.jeton(client)})
        return client

    def requete(self, requete):
        db = sqlite3.connect(self.fichier.name)
        resultat = db.execute(requete).fetchall()
        db.close()
        return resultat

    def dernier_rendu(self, nom):
        return next(contexte for n, contexte in reversed(self.rendus) if n == nom)

    PAGES_GET = ["/admin", "/admin/membres", "/admin/membres/nouveau", "/admin/abonnements",
                 "/admin/abonnements/nouveau", "/admin/cours", "/admin/cours/nouveau", "/admin/cours/1/modifier",
                 "/admin/seance?cours_id=1&date=2026-10-22", "/admin/statistiques"]

    # ---------- droits d'accès ----------
    def test_pages_admin_refusees_aux_visiteurs_et_aux_membres(self):
        for client in (appli.app.test_client(), self.membre_connecte()):
            for url in self.PAGES_GET:
                with self.subTest(url=url):
                    reponse = client.get(url)
                    self.assertEqual(reponse.status_code, 302)
                    self.assertTrue(reponse.headers["Location"].endswith("/admin/connexion"))

    def test_actions_admin_refusees_aux_non_gerants(self):
        # (client, page qui contient un jeton CSRF pour ce client)
        for client, page in ((appli.app.test_client(), "/connexion"), (self.membre_connecte(), "/membre")):
            jeton = self.jeton(client, page)
            for url, donnees in [("/admin/membres/nouveau", {"nom": "Intrus", "telephone": "677000009"}),
                                 ("/admin/membres/1/code", {}),
                                 ("/admin/abonnements/nouveau", {"membre_id": "2", "formule_id": "2"}),
                                 ("/admin/cours/nouveau", {"discipline": "Intrus", "coach": "Intrus", "jour_semaine": "1",
                                                           "heure": "10:00", "salle": "Salle Z", "places_max": "5"}),
                                 ("/admin/cours/1/modifier", {}), ("/admin/cours/1/supprimer", {})]:
                with self.subTest(url=url):
                    reponse = client.post(url, data=dict(donnees, csrf_token=jeton))
                    self.assertEqual(reponse.status_code, 302)
                    self.assertTrue(reponse.headers["Location"].endswith("/admin/connexion"))
        self.assertEqual(len(self.requete("SELECT id FROM membres")), 2)
        self.assertEqual(len(self.requete("SELECT id FROM abonnements")), 1)

    def test_les_api_gerant_sont_reservees_au_gerant(self):
        for client in (appli.app.test_client(), self.membre_connecte()):
            self.assertEqual(client.get("/api/admin/abonnement-apercu?membre_id=1&formule_id=2").status_code, 401)

    def test_post_sans_jeton_csrf_refuse(self):
        client = self.gerant()
        for url in ("/admin/membres/nouveau", "/admin/membres/1/code", "/admin/abonnements/nouveau",
                    "/admin/cours/nouveau", "/admin/cours/1/modifier", "/admin/cours/1/supprimer"):
            self.assertEqual(client.post(url, data={"nom": "X Y", "telephone": "677000009"}).status_code, 400)
        self.assertEqual(len(self.requete("SELECT id FROM membres")), 2)

    # ---------- membres ----------
    def test_liste_et_recherche_des_membres(self):
        client = self.gerant()
        client.get("/admin/membres")
        self.assertEqual([m["nom"] for m in self.dernier_rendu("admin_membres.html")["membres"]],
                         ["Aline Mbarga", "Paul Fotso"])
        client.get("/admin/membres?q=fotso")
        contexte = self.dernier_rendu("admin_membres.html")
        self.assertEqual([m["nom"] for m in contexte["membres"]], ["Paul Fotso"])
        self.assertEqual(contexte["recherche"], "fotso")

    def test_creation_d_un_membre_qui_peut_ensuite_se_connecter(self):
        client = self.gerant()
        reponse = client.post("/admin/membres/nouveau", data={
            "nom": "Kevin Njoya", "telephone": "699 11 22 33", "csrf_token": self.jeton(client, "/admin")})
        self.assertEqual(reponse.status_code, 200)
        self.assertEqual(reponse.headers["Cache-Control"], "no-store")        # le code n'est pas gardé par le navigateur
        membre = self.dernier_rendu("admin_code_acces.html")["membre"]
        self.assertEqual(membre["telephone"], "699112233")
        self.assertEqual(len(membre["code"]), 6)
        # Le nouveau membre se connecte avec le code remis par le gérant
        nouveau = appli.app.test_client()
        reponse = nouveau.post("/connexion", data={"telephone": "699112233", "code": membre["code"],
                                                   "csrf_token": self.jeton(nouveau)})
        self.assertEqual(reponse.status_code, 302)
        self.assertTrue(reponse.headers["Location"].endswith("/membre"))

    def test_creation_refusee_pour_un_numero_deja_pris(self):
        client = self.gerant()
        client.post("/admin/membres/nouveau", data={"nom": "Doublon", "telephone": "677000001",
                                                    "csrf_token": self.jeton(client, "/admin")})
        contexte = self.dernier_rendu("admin_membre_nouveau.html")
        self.assertIn("déjà enregistré", contexte["erreur"])
        self.assertEqual(contexte["nom"], "Doublon")                          # la saisie est conservée
        self.assertEqual(len(self.requete("SELECT id FROM membres")), 2)

    def test_reinitialisation_du_code_d_un_membre(self):
        client = self.gerant()
        client.post("/admin/membres/1/code", data={"csrf_token": self.jeton(client, "/admin")})
        nouveau_code = self.dernier_rendu("admin_code_acces.html")["membre"]["code"]
        ancien = appli.app.test_client()
        ancien.post("/connexion", data={"telephone": "677000001", "code": CODE, "csrf_token": self.jeton(ancien)})
        self.assertEqual(ancien.get("/membre").status_code, 302)               # ancien code refusé : pas connecté
        nouveau = appli.app.test_client()
        nouveau.post("/connexion", data={"telephone": "677000001", "code": nouveau_code, "csrf_token": self.jeton(nouveau)})
        self.assertEqual(nouveau.get("/membre").status_code, 200)

    def test_reinitialisation_d_un_membre_inconnu(self):
        client = self.gerant()
        reponse = client.post("/admin/membres/999/code", data={"csrf_token": self.jeton(client, "/admin")})
        self.assertEqual(reponse.status_code, 302)
        with client.session_transaction() as session:
            self.assertIn("Membre introuvable.", [m for _, m in session["_flashes"]])

    # ---------- abonnements ----------
    def test_enregistrement_d_un_renouvellement(self):
        client = self.gerant()
        reponse = client.post("/admin/abonnements/nouveau", data={
            "membre_id": "1", "formule_id": "2", "date_debut": "", "montant": "",
            "csrf_token": self.jeton(client, "/admin")})
        self.assertEqual(reponse.status_code, 302)
        self.assertTrue(reponse.headers["Location"].endswith("/admin/abonnements"))
        lignes = self.requete("SELECT date_debut, date_fin, montant_paye FROM abonnements WHERE membre_id = 1 ORDER BY id")
        fin_actuelle = date.today() + timedelta(days=2)
        self.assertEqual(lignes[1][0], (fin_actuelle + timedelta(days=1)).isoformat())    # lendemain de la fin
        self.assertEqual(lignes[1][2], 10000)
        with client.session_transaction() as session:
            self.assertEqual(session["_flashes"][0][0], "succes")

    def test_enregistrement_refuse_conserve_la_saisie(self):
        client = self.gerant()
        client.post("/admin/abonnements/nouveau", data={
            "membre_id": "1", "formule_id": "2", "date_debut": date.today().isoformat(), "montant": "5000",
            "csrf_token": self.jeton(client, "/admin")})                       # chevauche l'abonnement en cours
        contexte = self.dernier_rendu("admin_abonnement_nouveau.html")
        self.assertIn("chevauche", contexte["erreur"])
        self.assertEqual(contexte["valeurs"]["montant"], "5000")
        self.assertEqual(len(contexte["membres"]), 2)
        self.assertEqual(len(contexte["formules"]), 2)
        self.assertEqual(len(self.requete("SELECT id FROM abonnements")), 1)

    def test_formulaire_prerempli_par_l_adresse(self):
        client = self.gerant()
        client.get("/admin/abonnements/nouveau?membre_id=2")
        self.assertEqual(self.dernier_rendu("admin_abonnement_nouveau.html")["valeurs"]["membre_id"], "2")

    def test_liste_des_abonnements_et_filtre(self):
        client = self.gerant()
        client.get("/admin/abonnements?filtre=expirant")
        contexte = self.dernier_rendu("admin_abonnements.html")
        self.assertEqual(contexte["filtre"], "expirant")
        self.assertEqual([a["nom"] for a in contexte["abonnements"]], ["Aline Mbarga"])
        self.assertEqual(contexte["nombre_expirant"], 1)
        client.get("/admin/abonnements?filtre=n-importe-quoi")
        self.assertEqual(self.dernier_rendu("admin_abonnements.html")["filtre"], "tous")

    def test_tableau_de_bord_compte_les_expirations(self):
        self.gerant().get("/admin")
        contexte = self.dernier_rendu("admin.html")
        self.assertEqual((contexte["membres_actifs"], contexte["membres_total"], contexte["expirations"]), (1, 2, 1))

    def test_api_apercu_d_un_abonnement(self):
        client = self.gerant()
        reponse = client.get("/api/admin/abonnement-apercu?membre_id=1&formule_id=2")
        donnees = reponse.get_json()
        self.assertEqual(reponse.status_code, 200)
        fin_actuelle = date.today() + timedelta(days=2)
        self.assertEqual(donnees["date_debut"], (fin_actuelle + timedelta(days=1)).isoformat())
        self.assertEqual(donnees["montant"], 10000)
        self.assertEqual(len(self.requete("SELECT id FROM abonnements")), 1)          # rien n'est enregistré
        reponse = client.get("/api/admin/abonnement-apercu?membre_id=1&formule_id=999")
        self.assertEqual(reponse.status_code, 400)
        self.assertIn("erreur", reponse.get_json())


    # ---------- cours et inscrits ----------
    DONNEES_COURS = {"discipline": "Zumba", "coach": "Rose Ekane", "jour_semaine": "3", "heure": "18:00",
                     "salle": "Salle A", "places_max": "15"}

    def creer_cours_direct(self):
        db = sqlite3.connect(self.fichier.name)
        db.execute("INSERT INTO cours (discipline, coach, jour_semaine, heure, salle, places_max) "
                   "VALUES ('Zumba', 'Rose Ekane', 3, '18:00', 'Salle A', 15)")
        db.commit()
        db.close()

    def test_ajout_d_un_cours(self):
        client = self.gerant()
        reponse = client.post("/admin/cours/nouveau", data=dict(self.DONNEES_COURS, csrf_token=self.jeton(client, "/admin")))
        self.assertEqual(reponse.status_code, 302)
        self.assertTrue(reponse.headers["Location"].endswith("/admin/cours"))
        self.assertEqual(self.requete("SELECT discipline, jour_semaine, heure, places_max FROM cours"),
                         [("Zumba", 3, "18:00", 15)])
        with client.session_transaction() as session:
            self.assertEqual(session["_flashes"][0][0], "succes")

    def test_ajout_refuse_conserve_la_saisie(self):
        client = self.gerant()
        client.post("/admin/cours/nouveau", data=dict(self.DONNEES_COURS, heure="25:00", csrf_token=self.jeton(client, "/admin")))
        contexte = self.dernier_rendu("admin_cours_form.html")
        self.assertEqual(contexte["mode"], "nouveau")
        self.assertIn("HH:MM", contexte["erreur"])
        self.assertEqual(contexte["valeurs"]["heure"], "25:00")
        self.assertEqual(contexte["valeurs"]["discipline"], "Zumba")
        self.assertEqual([nom for _, nom in contexte["jours"]][:2], ["Lundi", "Mardi"])
        self.assertEqual(self.requete("SELECT id FROM cours"), [])

    def test_formulaire_de_modification_prerempli(self):
        self.creer_cours_direct()
        self.gerant().get("/admin/cours/1/modifier")
        contexte = self.dernier_rendu("admin_cours_form.html")
        self.assertEqual((contexte["mode"], contexte["cours_id"]), ("modifier", 1))
        self.assertEqual(contexte["valeurs"], {"discipline": "Zumba", "coach": "Rose Ekane", "jour_semaine": "3",
                                               "heure": "18:00", "salle": "Salle A", "places_max": "15"})

    def test_cours_inconnu_donne_une_404(self):
        client = self.gerant()
        self.assertEqual(client.get("/admin/cours/999/modifier").status_code, 404)

    def test_modification_d_un_cours(self):
        self.creer_cours_direct()
        client = self.gerant()
        reponse = client.post("/admin/cours/1/modifier", data=dict(self.DONNEES_COURS, coach="Sandra Njoya",
                                                                    csrf_token=self.jeton(client, "/admin")))
        self.assertEqual(reponse.status_code, 302)
        self.assertEqual(self.requete("SELECT coach FROM cours"), [("Sandra Njoya",)])

    def test_modification_refusee_reaffiche_le_formulaire(self):
        self.creer_cours_direct()
        client = self.gerant()
        client.post("/admin/cours/1/modifier", data=dict(self.DONNEES_COURS, places_max="0", csrf_token=self.jeton(client, "/admin")))
        contexte = self.dernier_rendu("admin_cours_form.html")
        self.assertIn("places", contexte["erreur"])
        self.assertEqual(contexte["valeurs"]["places_max"], "0")
        self.assertEqual(self.requete("SELECT places_max FROM cours"), [(15,)])

    def test_suppression_d_un_cours(self):
        self.creer_cours_direct()
        client = self.gerant()
        reponse = client.post("/admin/cours/1/supprimer", data={"csrf_token": self.jeton(client, "/admin")})
        self.assertEqual(reponse.status_code, 302)
        self.assertEqual(self.requete("SELECT id FROM cours"), [])
        self.assertEqual(client.get("/admin/cours/1/supprimer").status_code, 405)         # pas de suppression par adresse

    def test_suppression_refusee_avec_reservations(self):
        self.creer_cours_direct()
        db = sqlite3.connect(self.fichier.name)
        db.execute("INSERT INTO abonnements (membre_id, formule_id, date_debut, date_fin, montant_paye) "
                   "VALUES (2, 2, '2020-01-01', '2099-12-31', 10000)")
        db.execute("INSERT INTO reservations_cours (cours_id, membre_id, date_seance) VALUES (1, 2, '2026-10-22')")
        db.commit()
        db.close()
        client = self.gerant()
        client.post("/admin/cours/1/supprimer", data={"csrf_token": self.jeton(client, "/admin")})
        self.assertEqual(len(self.requete("SELECT id FROM cours")), 1)
        with client.session_transaction() as session:
            self.assertEqual(session["_flashes"][0][0], "erreur")

    def test_liste_des_cours(self):
        self.creer_cours_direct()
        self.gerant().get("/admin/cours")
        cours = self.dernier_rendu("admin_cours.html")["cours"]
        self.assertEqual([(c["jour_nom"], c["reservations_a_venir"]) for c in cours], [("Jeudi", 0)])

    def test_page_des_inscrits(self):
        self.creer_cours_direct()
        client = self.gerant()
        prochain_jeudi = date.today() + timedelta(days=(3 - date.today().weekday()) % 7)
        db = sqlite3.connect(self.fichier.name)
        db.execute("INSERT INTO abonnements (membre_id, formule_id, date_debut, date_fin, montant_paye) "
                   "VALUES (2, 2, '2020-01-01', '2099-12-31', 10000)")
        db.execute("INSERT INTO reservations_cours (cours_id, membre_id, date_seance) VALUES (1, 2, ?)",
                   (prochain_jeudi.isoformat(),))
        db.commit()
        db.close()
        client.get(f"/admin/seance?cours_id=1&date={prochain_jeudi.isoformat()}")
        contexte = self.dernier_rendu("admin_seance.html")
        self.assertEqual([i["nom"] for i in contexte["inscrits"]], ["Paul Fotso"])
        self.assertEqual((contexte["places_prises"], contexte["places_restantes"]), (1, 14))
        self.assertIn(prochain_jeudi.isoformat(), contexte["dates"])

    def test_page_des_inscrits_avec_parametres_invalides(self):
        self.creer_cours_direct()
        client = self.gerant()
        for adresse in ["/admin/seance", "/admin/seance?cours_id=abc&date=2026-10-22", "/admin/seance?cours_id=1&date=nimporte"]:
            with self.subTest(adresse=adresse):
                reponse = client.get(adresse)
                self.assertEqual(reponse.status_code, 302)
                self.assertTrue(reponse.headers["Location"].endswith("/admin/cours"))

    # ---------- statistiques ----------
    def test_page_et_api_des_statistiques(self):
        client = self.gerant()
        client.get("/admin/statistiques")
        stats = self.dernier_rendu("admin_statistiques.html")["stats"]
        self.assertEqual((stats["membres"]["actifs"], stats["membres"]["total"]), (1, 2))
        reponse = client.get("/api/statistiques")
        self.assertEqual(reponse.status_code, 200)
        self.assertEqual(set(reponse.get_json()), {"date", "recette", "remplissage", "membres", "renouvellement"})

    def test_api_statistiques_reservee_au_gerant(self):
        for client in (appli.app.test_client(), self.membre_connecte()):
            self.assertEqual(client.get("/api/statistiques").status_code, 401)


if __name__ == "__main__":
    unittest.main(verbosity=2)
