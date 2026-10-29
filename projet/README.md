# Projet 15 : salle de sport, abonnements et séances (Groupe 15)

Application Web **FitZone** pour une salle de sport : les membres suivent leur abonnement et réservent leurs places aux cours collectifs, le gérant enregistre les abonnements, gère le planning et consulte les statistiques.

Technologies : Python 3, Flask, SQLite (module `sqlite3`), Jinja2, HTML5, CSS, JavaScript. Aucune bibliothèque externe côté navigateur : le site fonctionne sans connexion Internet.

## Prérequis

- Python 3.9 ou plus récent (testé avec Python 3.12), avec `pip`.
- Un navigateur récent.

## Installation

Ouvrir un terminal **dans le dossier `projet`** (celui qui contient ce fichier), puis :

```text
python -m venv venv
venv\Scripts\activate          (Windows, invite de commandes)
source venv/bin/activate       (Linux ou macOS)
pip install -r requirements.txt
```

Sous Windows PowerShell, utiliser `venv\Scripts\Activate.ps1`. Si `python` n'est pas reconnu, essayer `py` (Windows) ou `python3` (Linux, macOS).

## Lancement

La base `projet.db` est déjà remplie de données de test.

```text
python app.py
```

Puis ouvrir http://127.0.0.1:5000 dans le navigateur. Pour arrêter le serveur : `Ctrl+C`.

## Comptes de test

| Espace | Adresse | Identifiant | Mot de passe ou code |
|---|---|---|---|
| Membre | http://127.0.0.1:5000/connexion | téléphone `677000001` | `Membre!2026` |
| Gérant | http://127.0.0.1:5000/admin/connexion | `admin` | `Admin!2026` |

Le membre `677000001` (Aline Mbarga) a un abonnement en cours et des réservations à venir. Tous les membres de test ont le même code `Membre!2026` ; leurs numéros se trouvent dans l'espace gérant, page « Membres ».

## Recréer les données de test

```text
python donnees_test.py
```

Ce script **efface et recrée** `projet.db` à partir de `schema.sql` : 4 formules, 70 membres, 100 abonnements (en cours et expirés), 12 cours hebdomadaires, 300 réservations et le compte gérant. Les choix aléatoires sont toujours les mêmes (`random.seed(42)`), mais les **dates sont calculées à partir du jour du lancement** : il y a donc toujours des abonnements en cours, des abonnements qui expirent bientôt et des séances à venir.

La base peut aussi être recréée depuis l'export SQL :

```text
python -c "import sqlite3; db = sqlite3.connect('copie.db'); db.executescript(open('export_projet.sql', encoding='utf-8').read())"
```

## Fonctionnalités

- **Visiteur** : accueil, formules et prix, planning de la semaine avec les places restantes en direct.
- **Membre** : tableau de bord (abonnement, jours restants, barre de progression), réservation et annulation d'une place, séances à venir et historique, détail de l'abonnement.
- **Gérant** : création des membres (code d'accès affiché une seule fois), enregistrement des abonnements et renouvellements avec aperçu des dates, liste des abonnements qui expirent sous 7 jours, gestion des cours, inscrits d'une séance, quatre statistiques.

Règles principales : un abonnement est valide à la date de la **séance** ; un renouvellement commence le lendemain de la fin de l'abonnement actuel ; un cours ne dépasse jamais son nombre de places ; on réserve au plus 7 jours à l'avance ; l'annulation est possible jusqu'au début de la séance.

## API JSON

| Route | Accès | Contenu |
|---|---|---|
| `/api/planning` | public | séances de la semaine et places restantes |
| `/api/abonnement` | membre | jours restants et progression |
| `/api/admin/abonnement-apercu` | gérant | dates et montant d'un abonnement, sans l'enregistrer |
| `/api/statistiques` | gérant | les quatre statistiques |

## Sécurité

Mots de passe et codes d'accès stockés sous forme d'empreinte (`generate_password_hash`) ; requêtes SQL toujours paramétrées ; modifications et suppressions uniquement en POST avec un jeton CSRF ; toutes les saisies revérifiées sur le serveur, puis par les contraintes et déclencheurs de la base ; identité prise dans la session ; `debug=False`.

## Structure du dossier

```text
projet/
├── app.py               application Flask (routes, règles, API)
├── schema.sql           création des tables, contraintes et déclencheurs
├── donnees_test.py      remplit la base de données de test
├── projet.db            base SQLite déjà remplie
├── export_projet.sql    export complet de la base
├── requirements.txt     dépendances (Flask)
├── README.md
├── static/
│   ├── css/style.css
│   ├── js/              planning.js, verif.js, abonnement.js, apercu.js, admin.js
│   ├── fonts/           police Poppins (licence SIL OFL)
│   └── images/          photos facultatives : hero, connexion, cours (.jpg ou .png)
└── templates/           gabarits Jinja2 (base.html dont héritent toutes les pages)
```

## Dépannage

- **« projet.db est introuvable »** : lancer `python donnees_test.py`, puis `python app.py`.
- **Le port 5000 est déjà utilisé** : fermer l'autre programme qui l'occupe (ou une ancienne fenêtre du serveur), puis relancer.
- **`ModuleNotFoundError: flask`** : l'environnement virtuel n'est pas activé, ou `pip install -r requirements.txt` n'a pas été lancé.
- **Les séances à venir ont disparu** : les dates de test vieillissent ; relancer `python donnees_test.py`.


