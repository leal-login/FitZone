// admin.js : petits comportements de l'espace membre et de l'administration.
// - data-confirmer : demande une confirmation avant une action (annulation, suppression...)
// - data-auto-envoi : envoie le formulaire dès que la liste déroulante change

(function () {
    "use strict";

    document.querySelectorAll("[data-confirmer]").forEach(function (bouton) {
        bouton.addEventListener("click", function (evenement) {
            if (!window.confirm(bouton.dataset.confirmer)) {
                evenement.preventDefault();
            }
        });
    });

    document.querySelectorAll("select[data-auto-envoi]").forEach(function (liste) {
        liste.addEventListener("change", function () {
            if (liste.form) {
                liste.form.submit();
            }
        });
    });
})();
