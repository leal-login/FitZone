// verif.js : vérification des formulaires de connexion AVANT l'envoi + bouton « afficher le mot de passe ».
// Cette vérification est un confort pour l'utilisateur : le serveur revérifie toujours tout
// (un utilisateur peut désactiver JavaScript ou envoyer une requête à la main).

(function () {
    "use strict";

    /** Nettoie un numéro (espaces, points, tirets, indicatif +237) ; renvoie null s'il est invalide. */
    function normaliserTelephone(texte) {
        let numero = texte.replace(/[\s.\-]/g, "");
        if (numero.startsWith("+237")) {
            numero = numero.slice(4);
        } else if (numero.startsWith("237") && numero.length === 12) {
            numero = numero.slice(3);
        }
        return /^6[0-9]{8}$/.test(numero) ? numero : null;
    }

    /** Affiche (ou efface) le message d'erreur d'un champ. */
    function afficherErreur(champ, message) {
        const zone = document.getElementById("erreur-" + champ.name);
        if (message) {
            champ.setAttribute("aria-invalid", "true");
            if (zone) {
                zone.textContent = message;
                zone.hidden = false;
            }
        } else {
            champ.removeAttribute("aria-invalid");
            if (zone) {
                zone.textContent = "";
                zone.hidden = true;
            }
        }
    }

    function verifierMembre(formulaire) {
        const telephone = formulaire.elements["telephone"];
        const code = formulaire.elements["code"];
        let correct = true;

        const numero = normaliserTelephone(telephone.value.trim());
        if (numero === null) {
            afficherErreur(telephone, "Saisissez 9 chiffres commençant par 6.");
            correct = false;
        } else {
            telephone.value = numero;          // on envoie le numéro nettoyé
            afficherErreur(telephone, "");
        }

        if (code.value === "") {
            afficherErreur(code, "Saisissez votre code d’accès.");
            correct = false;
        } else {
            afficherErreur(code, "");
        }
        return correct;
    }

    function verifierAdmin(formulaire) {
        const login = formulaire.elements["login"];
        const motDePasse = formulaire.elements["mot_de_passe"];
        let correct = true;

        if (login.value.trim() === "") {
            afficherErreur(login, "Saisissez votre identifiant.");
            correct = false;
        } else {
            afficherErreur(login, "");
        }
        if (motDePasse.value === "") {
            afficherErreur(motDePasse, "Saisissez votre mot de passe.");
            correct = false;
        } else {
            afficherErreur(motDePasse, "");
        }
        return correct;
    }

    const verifications = { membre: verifierMembre, admin: verifierAdmin };

    document.querySelectorAll("form[data-verification]").forEach(function (formulaire) {
        const verifier = verifications[formulaire.dataset.verification];
        if (!verifier) {
            return;
        }
        // Les messages sont gérés ici : on coupe la bulle d'erreur du navigateur.
        // Sans JavaScript, la validation native (attribut required) reste active.
        formulaire.noValidate = true;
        formulaire.addEventListener("submit", function (evenement) {
            if (!verifier(formulaire)) {
                evenement.preventDefault();
                const premier = formulaire.querySelector("[aria-invalid='true']");
                if (premier) {
                    premier.focus();
                }
            }
        });
    });

    // Bouton « afficher / masquer » : caché sans JavaScript, puis révélé ici
    document.querySelectorAll("[data-bascule]").forEach(function (bouton) {
        const champ = document.getElementById(bouton.dataset.bascule);
        if (!champ) {
            return;
        }
        bouton.hidden = false;
        bouton.addEventListener("click", function () {
            const visible = champ.type === "text";
            champ.type = visible ? "password" : "text";
            bouton.setAttribute("aria-pressed", String(!visible));
            bouton.setAttribute("aria-label", (visible ? "Afficher " : "Masquer ") +
                (champ.name === "code" ? "le code" : "le mot de passe"));
        });
    });
})();
