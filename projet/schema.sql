-- schema.sql : création des tables du Projet 15 (salle de sport : abonnements et séances)
-- Ce script est rejouable : il supprime d'abord les anciennes tables (dans l'ordre inverse des liens).
-- Les dates sont stockées en texte ISO « AAAA-MM-JJ ». Les jours de la semaine vont de 0 (lundi) à 6 (dimanche).
-- ATTENTION : dans app.py et donnees_test.py, exécuter « PRAGMA foreign_keys = ON » à CHAQUE connexion,
-- sinon SQLite n'applique pas les clés étrangères.

PRAGMA foreign_keys = ON;

-- Suppression des anciens objets (les tables qui en référencent d'autres d'abord)
DROP TABLE IF EXISTS reservations_cours;
DROP TABLE IF EXISTS abonnements;
DROP TABLE IF EXISTS cours;
DROP TABLE IF EXISTS membres;
DROP TABLE IF EXISTS formules;
DROP TABLE IF EXISTS administrateurs;

-- ---------------------------------------------------------------------------
-- 1. formules : les offres vendues (mensuel, trimestriel, annuel, à la séance)
-- ---------------------------------------------------------------------------
CREATE TABLE formules (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    nom         TEXT    NOT NULL UNIQUE,                       -- deux formules ne portent pas le même nom
    duree_jours INTEGER NOT NULL CHECK (duree_jours > 0),      -- 1 pour « à la séance »
    prix        INTEGER NOT NULL CHECK (prix >= 0)             -- en FCFA, sans décimales
);

-- ---------------------------------------------------------------------------
-- 2. membres : les adhérents de la salle (créés par le gérant)
-- ---------------------------------------------------------------------------
CREATE TABLE membres (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    nom              TEXT NOT NULL,
    -- téléphone camerounais mobile : 9 chiffres commençant par 6, unique (sert d'identifiant de connexion)
    telephone        TEXT NOT NULL UNIQUE
                     CHECK (telephone GLOB '6[0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9]'),
    date_inscription TEXT NOT NULL DEFAULT (date('now'))
                     CHECK (date(date_inscription) IS NOT NULL AND date(date_inscription) = date_inscription),
    empreinte_code   TEXT NOT NULL                              -- code d'accès haché (generate_password_hash), jamais en clair
);

-- ---------------------------------------------------------------------------
-- 3. abonnements : un membre achète une formule pour une période (fin incluse)
-- ---------------------------------------------------------------------------
CREATE TABLE abonnements (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    membre_id    INTEGER NOT NULL REFERENCES membres(id),
    formule_id   INTEGER NOT NULL REFERENCES formules(id),
    date_debut   TEXT    NOT NULL
                 CHECK (date(date_debut) IS NOT NULL AND date(date_debut) = date_debut),
    date_fin     TEXT    NOT NULL
                 CHECK (date(date_fin) IS NOT NULL AND date(date_fin) = date_fin),
    montant_paye INTEGER NOT NULL CHECK (montant_paye >= 0),
    CHECK (date_fin >= date_debut)                              -- la fin ne précède jamais le début
);

CREATE INDEX idx_abonnements_membre ON abonnements (membre_id, date_debut, date_fin);

-- Un membre n'a jamais deux abonnements qui se chevauchent (règle RG9 : un renouvellement
-- commence le lendemain de la fin de l'abonnement actuel). Deux périodes se chevauchent si
-- chacune commence avant (ou le jour de) la fin de l'autre.
CREATE TRIGGER trg_abonnements_chevauchement
BEFORE INSERT ON abonnements
WHEN EXISTS (
    SELECT 1 FROM abonnements
    WHERE membre_id = NEW.membre_id
      AND NEW.date_debut <= date_fin
      AND NEW.date_fin   >= date_debut
)
BEGIN
    SELECT RAISE(ABORT, 'Cet abonnement chevauche un abonnement existant du membre.');
END;

-- ---------------------------------------------------------------------------
-- 4. cours : le planning hebdomadaire (un cours se répète chaque semaine)
-- ---------------------------------------------------------------------------
CREATE TABLE cours (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    discipline   TEXT    NOT NULL,                              -- musculation, aérobic, taekwondo, zumba...
    coach        TEXT    NOT NULL,
    jour_semaine INTEGER NOT NULL CHECK (jour_semaine BETWEEN 0 AND 6),   -- 0 = lundi ... 6 = dimanche
    heure        TEXT    NOT NULL CHECK (heure GLOB '[0-2][0-9]:[0-5][0-9]'),  -- « HH:MM »
    salle        TEXT    NOT NULL,
    places_max   INTEGER NOT NULL CHECK (places_max > 0),
    UNIQUE (salle, jour_semaine, heure)                         -- une salle ne peut pas accueillir deux cours en même temps
);

-- ---------------------------------------------------------------------------
-- 5. reservations_cours : un membre réserve une place à UNE séance datée d'un cours
-- ---------------------------------------------------------------------------
CREATE TABLE reservations_cours (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    cours_id         INTEGER NOT NULL REFERENCES cours(id),
    membre_id        INTEGER NOT NULL REFERENCES membres(id),
    date_seance      TEXT    NOT NULL
                     CHECK (date(date_seance) IS NOT NULL AND date(date_seance) = date_seance),
    date_reservation TEXT    NOT NULL DEFAULT (datetime('now')),
    UNIQUE (cours_id, membre_id, date_seance)                   -- RG5 : une seule réservation par membre, cours et date
);

CREATE INDEX idx_reservations_seance ON reservations_cours (cours_id, date_seance);
CREATE INDEX idx_reservations_membre ON reservations_cours (membre_id, date_seance);

-- RG7 : la date de séance doit tomber le bon jour de la semaine pour ce cours.
-- strftime('%w') donne 0 pour dimanche : on convertit pour obtenir 0 = lundi.
CREATE TRIGGER trg_reservations_jour
BEFORE INSERT ON reservations_cours
WHEN ((CAST(strftime('%w', NEW.date_seance) AS INTEGER) + 6) % 7)
     <> (SELECT jour_semaine FROM cours WHERE id = NEW.cours_id)
BEGIN
    SELECT RAISE(ABORT, 'La date choisie ne correspond pas au jour de ce cours.');
END;

-- RG3 et RG4 : le membre doit avoir un abonnement valide à la date de la SÉANCE
-- (et non à la date de la réservation).
CREATE TRIGGER trg_reservations_abonnement
BEFORE INSERT ON reservations_cours
WHEN NOT EXISTS (
    SELECT 1 FROM abonnements
    WHERE membre_id = NEW.membre_id
      AND date_debut <= NEW.date_seance
      AND date_fin   >= NEW.date_seance
)
BEGIN
    SELECT RAISE(ABORT, 'Aucun abonnement valide à la date de cette séance.');
END;

-- RG6 : jamais plus de réservations que de places. Ce contrôle est exécuté DANS l'instruction
-- INSERT, sous le verrou d'écriture de SQLite : deux réservations simultanées ne peuvent pas
-- prendre la dernière place toutes les deux.
CREATE TRIGGER trg_reservations_capacite
BEFORE INSERT ON reservations_cours
WHEN (SELECT COUNT(*) FROM reservations_cours
      WHERE cours_id = NEW.cours_id AND date_seance = NEW.date_seance)
     >= (SELECT places_max FROM cours WHERE id = NEW.cours_id)
BEGIN
    SELECT RAISE(ABORT, 'Ce cours est complet pour cette séance.');
END;

-- ---------------------------------------------------------------------------
-- 6. administrateurs : comptes du gérant (espace d'administration)
-- ---------------------------------------------------------------------------
CREATE TABLE administrateurs (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    login     TEXT NOT NULL UNIQUE,
    empreinte TEXT NOT NULL                                     -- mot de passe haché (generate_password_hash)
);
