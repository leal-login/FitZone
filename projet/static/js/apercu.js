// apercu.js : aperçu en direct d'un abonnement dans le formulaire du gérant.
// Interroge l'API (fetch sur /api/admin/abonnement-apercu) sans rien enregistrer.
// Le contenu reçu est inséré avec textContent (jamais innerHTML).

(function () {
    "use strict";

    const formulaire = document.getElementById("form-abonnement");
    const zone = document.getElementById("apercu");
    if (!formulaire || !zone) {
        return;
    }
    const membre = formulaire.elements["membre_id"];
    const formule = formulaire.elements["formule_id"];
    const debut = formulaire.elements["date_debut"];
    const montant = formulaire.elements["montant"];
    let minuterie = null;
    let numeroRequete = 0;       // ignore les réponses devenues obsolètes

    const format = new Intl.DateTimeFormat("fr-FR", { day: "numeric", month: "long", year: "numeric", timeZone: "UTC" });

    function enLettres(texte) {
        const [annee, mois, jour] = texte.split("-").map(Number);
        const date = new Date(Date.UTC(annee, mois - 1, jour));
        return jour === 1 ? format.format(date).replace(/^1 /, "1er ") : format.format(date);
    }

    function montantFcfa(valeur) {
        return new Intl.NumberFormat("fr-FR").format(valeur).replace(/\u202f/g, "\u00a0") + "\u00a0FCFA";
    }

    function message(classe, texte) {
        const p = document.createElement("p");
        p.className = classe;
        p.textContent = texte;
        zone.replaceChildren(p);
    }

    function ligne(liste, nom, valeur) {
        const bloc = document.createElement("div");
        const dt = document.createElement("dt");
        dt.textContent = nom;
        const dd = document.createElement("dd");
        dd.textContent = valeur;
        bloc.append(dt, dd);
        liste.append(bloc);
    }

    function actualiser() {
        if (!membre.value || !formule.value) {
            message("etat-vide", "Choisissez un membre et une formule pour voir les dates calculées.");
            return;
        }
        const requete = ++numeroRequete;
        const parametres = new URLSearchParams({
            membre_id: membre.value, formule_id: formule.value, date_debut: debut.value, montant: montant.value
        });
        fetch(zone.dataset.api + "?" + parametres.toString())
            .then(function (reponse) {
                return reponse.json().then(function (donnees) {
                    return { ok: reponse.ok, donnees: donnees };
                });
            })
            .then(function (resultat) {
                if (requete !== numeroRequete) {
                    return;
                }
                if (!resultat.ok) {
                    message("avis avis-alerte", resultat.donnees.erreur || "Saisie incorrecte.");
                    return;
                }
                const d = resultat.donnees;
                const liste = document.createElement("dl");
                liste.className = "apercu-liste";
                ligne(liste, "Membre", d.nom);
                ligne(liste, "Formule", d.formule + " (" + d.duree_jours + (d.duree_jours > 1 ? " jours" : " jour") + ")");
                ligne(liste, "Début", enLettres(d.date_debut));
                ligne(liste, "Fin", enLettres(d.date_fin));
                ligne(liste, "Montant", montantFcfa(d.montant));
                zone.replaceChildren(liste);
                if (d.debut_automatique && d.date_debut > new Date().toISOString().slice(0, 10)) {
                    const note = document.createElement("p");
                    note.className = "aide";
                    note.textContent = "Renouvellement : l’abonnement commence à la fin de l’abonnement actuel.";
                    zone.append(note);
                }
            })
            .catch(function () {
                message("avis avis-alerte", "Aperçu indisponible. Vous pouvez tout de même enregistrer.");
            });
    }

    function planifier() {
        window.clearTimeout(minuterie);
        minuterie = window.setTimeout(actualiser, 250);       // attend la fin de la saisie
    }

    [membre, formule, debut, montant].forEach(function (champ) {
        champ.addEventListener("input", planifier);
        champ.addEventListener("change", planifier);
    });
    actualiser();
})();
