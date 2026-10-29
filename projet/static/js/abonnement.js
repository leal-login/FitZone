// abonnement.js : met à jour la barre de progression de l'abonnement avec les données de l'API
// (fetch sur /api/abonnement). La page est déjà complète côté serveur : le script ne fait
// que rafraîchir la barre, avec une petite animation.

(function () {
    "use strict";

    const script = document.querySelector("script[data-api]");
    const barre = document.querySelector("[data-progression]");
    if (!script || !barre) {
        return;
    }
    const remplissage = barre.querySelector(".progression-barre");

    fetch(script.dataset.api)
        .then(function (reponse) {
            if (!reponse.ok) {
                throw new Error("Réponse " + reponse.status);
            }
            return reponse.json();
        })
        .then(function (donnees) {
            if (donnees.etat !== "en_cours") {
                return;
            }
            remplissage.style.width = "0%";
            window.requestAnimationFrame(function () {
                remplissage.style.width = donnees.progression + "%";
                barre.setAttribute("aria-valuenow", String(donnees.progression));
            });
        })
        .catch(function () {
            // En cas d'erreur, la barre calculée par le serveur reste affichée
        });
})();
