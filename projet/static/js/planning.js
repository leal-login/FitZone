// planning.js : planning hebdomadaire (jours cliquables, tableau des séances, réservation et annulation).
// Les données viennent de l'API (fetch sur /api/planning). Tout texte est inséré avec textContent
// (jamais innerHTML) : même si une donnée contenait du code HTML, il ne serait pas exécuté.

(function () {
    "use strict";

    const racine = document.getElementById("planning");
    if (!racine) {
        return;
    }
    const estMembre = racine.dataset.membre === "oui";
    const zoneJours = document.getElementById("jours");
    const zoneSeances = document.getElementById("seances");
    const libelle = document.getElementById("libelle-semaine");
    const precedente = document.getElementById("semaine-precedente");
    const suivante = document.getElementById("semaine-suivante");

    let semaine = 0;            // 0 = semaine en cours, 1 = semaine suivante
    let lundiCourant = null;    // lundi de la semaine en cours (donné par le serveur)
    let donnees = null;         // dernière réponse de l'API
    let jourChoisi = null;      // date « AAAA-MM-JJ » affichée dans le tableau

    // Formateurs de dates en français ; fuseau UTC pour éviter tout décalage d'un jour
    const formatMois = new Intl.DateTimeFormat("fr-FR", { month: "long", timeZone: "UTC" });
    const formatLong = new Intl.DateTimeFormat("fr-FR", { weekday: "long", day: "numeric", month: "long", timeZone: "UTC" });

    function lireDate(texte) {
        const [annee, mois, jour] = texte.split("-").map(Number);
        return new Date(Date.UTC(annee, mois - 1, jour));
    }

    function ajouterJours(texte, jours) {
        const date = lireDate(texte);
        date.setUTCDate(date.getUTCDate() + jours);
        return date.toISOString().slice(0, 10);
    }

    /** « 1er novembre » ou « 25 octobre 2026 » (avec l'année si demandé). */
    function dateEnLettres(date, avecAnnee) {
        const jour = date.getUTCDate() === 1 ? "1er" : String(date.getUTCDate());
        const texte = jour + " " + formatMois.format(date);
        return avecAnnee ? texte + " " + date.getUTCFullYear() : texte;
    }

    /** « Aérobic » devient « aerobic » : sert à colorer chaque discipline. */
    function slug(texte) {
        return texte.normalize("NFD").replace(/[\u0300-\u036f]/g, "").toLowerCase().replace(/[^a-z0-9]+/g, "-");
    }

    function creer(balise, classe, texte) {
        const element = document.createElement(balise);
        if (classe) {
            element.className = classe;
        }
        if (texte !== undefined) {
            element.textContent = texte;
        }
        return element;
    }

    function badge(texte, type) {
        return creer("span", "badge badge-" + type, texte);
    }

    /** Statut d'une séance d'après ses places. */
    function statut(seance) {
        if (seance.passee) {
            return badge("Terminé", "neutre");
        }
        if (seance.complet) {
            return badge("Complet", "erreur");
        }
        if (seance.places_restantes <= 3) {
            return badge("Dernières places", "alerte");
        }
        return badge("Disponible", "succes");
    }

    /** Formulaire POST (avec jeton CSRF) pour réserver ou annuler une place. */
    function creerFormulaire(action, champs, libelleBouton, classeBouton, confirmation) {
        const formulaire = document.createElement("form");
        formulaire.method = "post";
        formulaire.action = action;
        formulaire.className = "formulaire-en-ligne";
        const toutes = Object.assign({ csrf_token: racine.dataset.csrf }, champs);
        Object.keys(toutes).forEach(function (nom) {
            const champ = document.createElement("input");
            champ.type = "hidden";
            champ.name = nom;
            champ.value = toutes[nom];
            formulaire.append(champ);
        });
        const bouton = creer("button", "bouton bouton-petit " + classeBouton, libelleBouton);
        bouton.type = "submit";
        formulaire.append(bouton);
        if (confirmation) {
            formulaire.addEventListener("submit", function (evenement) {
                if (!window.confirm(confirmation)) {
                    evenement.preventDefault();
                }
            });
        }
        return formulaire;
    }

    /** Cellule d'action d'une séance (uniquement pour un membre connecté). */
    function cellule_action(seance, jour) {
        const cellule = creer("td");
        cellule.dataset.label = "Action";
        if (seance.reservee && !seance.passee) {
            cellule.append(creerFormulaire(racine.dataset.annuler.replace(/0$/, String(seance.reservation_id)), {},
                "Annuler", "bouton-danger", "Annuler cette réservation ?"));
        } else if (seance.reservable) {
            cellule.append(creerFormulaire(racine.dataset.reserver,
                { cours_id: seance.cours_id, date_seance: jour.date }, "Réserver", "bouton-primaire"));
        } else {
            cellule.append(creer("span", "sous-titre", "—"));
        }
        return cellule;
    }

    function creerLigne(seance, jour) {
        const ligne = creer("tr");

        const heure = creer("th", "", seance.heure);
        heure.scope = "row";
        heure.dataset.label = "Heure";

        const cours = creer("td");
        cours.dataset.label = "Cours";
        const nom = creer("span", "cours-nom");
        nom.append(creer("span", "point disc-" + slug(seance.discipline)), seance.discipline);
        cours.append(nom);

        const coach = creer("td", "", seance.coach);
        coach.dataset.label = "Coach";
        const salle = creer("td", "", seance.salle);
        salle.dataset.label = "Salle";
        const places = creer("td", "colonne-nombre", (seance.places_max - seance.places_restantes) + "/" + seance.places_max);
        places.dataset.label = "Places";

        const etat = creer("td");
        etat.dataset.label = "Statut";
        etat.append(statut(seance));
        if (seance.reservee) {
            etat.append(" ", badge("Réservé", "primaire"));
        }

        ligne.append(heure, cours, coach, salle, places, etat);
        if (estMembre) {
            ligne.append(cellule_action(seance, jour));
        }
        return ligne;
    }

    /** Affiche les séances du jour choisi. */
    function afficherSeances() {
        zoneSeances.replaceChildren();
        const jour = donnees.jours.find(function (j) { return j.date === jourChoisi; });
        if (!jour || jour.seances.length === 0) {
            zoneSeances.append(creer("p", "etat-vide", "Aucun cours ce jour-là."));
            zoneSeances.setAttribute("aria-busy", "false");
            return;
        }
        const table = creer("table", "tableau tableau-empile");
        const legende = creer("caption", "visuellement-cache", "Cours du " + formatLong.format(lireDate(jour.date)));
        const entetes = ["Heure", "Cours", "Coach", "Salle", "Places", "Statut"];
        if (estMembre) {
            entetes.push("Action");
        }
        const tete = creer("thead");
        const ligneTete = creer("tr");
        entetes.forEach(function (texte) {
            const th = creer("th", texte === "Places" ? "colonne-nombre" : "", texte);
            th.scope = "col";
            ligneTete.append(th);
        });
        tete.append(ligneTete);
        const corps = creer("tbody");
        jour.seances.forEach(function (seance) {
            corps.append(creerLigne(seance, jour));
        });
        table.append(legende, tete, corps);
        zoneSeances.append(table);
        zoneSeances.setAttribute("aria-busy", "false");
    }

    /** Affiche les sept boutons de jours. */
    function afficherJours() {
        zoneJours.replaceChildren();
        donnees.jours.forEach(function (jour) {
            const bouton = creer("button", "pilule" + (jour.aujourdhui ? " pilule-aujourdhui" : ""));
            bouton.type = "button";
            bouton.setAttribute("aria-pressed", String(jour.date === jourChoisi));
            bouton.setAttribute("aria-label", formatLong.format(lireDate(jour.date)));
            bouton.append(creer("span", "pilule-jour", jour.nom.slice(0, 3)),
                creer("span", "pilule-numero", String(lireDate(jour.date).getUTCDate())));
            bouton.addEventListener("click", function () {
                jourChoisi = jour.date;
                afficherJours();
                afficherSeances();
            });
            zoneJours.append(bouton);
        });
    }

    function afficher() {
        const aujourdhui = donnees.jours.find(function (j) { return j.aujourdhui; });
        jourChoisi = aujourdhui ? aujourdhui.date : donnees.jours[0].date;
        libelle.textContent = "Semaine du " + dateEnLettres(lireDate(donnees.debut), false) +
            " au " + dateEnLettres(lireDate(donnees.fin), true);
        precedente.disabled = semaine === 0;
        suivante.disabled = semaine === 1;
        afficherJours();
        afficherSeances();
    }

    function afficherErreur() {
        zoneJours.replaceChildren();
        zoneSeances.replaceChildren();
        const message = creer("div", "etat-vide");
        message.append(creer("p", "", "Impossible de charger le planning. Vérifiez votre connexion puis réessayez."));
        const reessayer = creer("button", "bouton bouton-contour bouton-petit", "Réessayer");
        reessayer.type = "button";
        reessayer.addEventListener("click", function () {
            charger(semaine);
        });
        message.append(reessayer);
        zoneSeances.append(message);
        libelle.textContent = "";
        zoneSeances.setAttribute("aria-busy", "false");
    }

    /** Charge la semaine demandée : 0 = semaine en cours, 1 = semaine suivante. */
    function charger(decalage) {
        zoneSeances.setAttribute("aria-busy", "true");
        let adresse = racine.dataset.api;
        if (decalage > 0 && lundiCourant) {
            adresse += "?debut=" + ajouterJours(lundiCourant, 7 * decalage);
        }
        fetch(adresse)
            .then(function (reponse) {
                if (!reponse.ok) {
                    throw new Error("Réponse " + reponse.status);
                }
                return reponse.json();
            })
            .then(function (resultat) {
                semaine = decalage;
                if (decalage === 0) {
                    lundiCourant = resultat.debut;
                }
                donnees = resultat;
                afficher();
            })
            .catch(afficherErreur);
    }

    precedente.addEventListener("click", function () { charger(0); });
    suivante.addEventListener("click", function () {
        if (lundiCourant) {
            charger(1);
        }
    });

    charger(0);
})();
