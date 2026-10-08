# État et feuille de route — Naya Marina

Application scolaire indépendante en Python/Django, avec SQLite ou MariaDB. Elle reprend certains parcours hôteliers courants et n’est pas un portage complet de QloApps.

## Fonctionnalités présentes et contrôlées

- Assistant d’installation initial et compte administrateur sur l’instance locale.
- Catalogue, recherche de disponibilité, réservations, affectation d’unités et planning sur 14 jours ; tarifs avec jours applicables, durée minimale/maximale, réductions saisonnières, codes promotionnels à usage limité, jours d’arrivée/départ, frais et taxe configurable.
- Recherche filtrable par catégorie, voyageurs et budget moyen par nuit, avec tri par prix ou capacité.
- Comptes clients, activation par lien ou code à usage unique, récupération, modification des demandes non payées, annulation avec suivi de remboursement et historique.
- Newsletter avec double opt-in, jetons de confirmation/désinscription, limitation des renvois, suivi du consentement et liste de gestion ; campagnes toujours à réaliser.
- Liens sociaux du pied de page administrables, ordonnables et désactivables.
- Notifications de site programmables et administrables avec niveaux d’alerte, période de visibilité et lien.
- Liens de navigation administrables, ordonnables et validés pour chemins locaux ou HTTPS.
- Réception avec modification/annulation des séjours confirmés, changement d’unité, encaissements partiels/manuels et édition d’un reçu au paiement complet ; rapprochement FedaPay sur relecture API et suivi de remboursements fractionnés.
- Commandes restaurant avec options tarifées, stock facultatif, seuils d’alerte, journal des mouvements, attribution d’une table disponible, statuts de préparation, file cuisine adaptée au tactile et ticket imprimable.
- Espaces d’accueil différenciés Gestionnaire hôtelier, Réceptionniste et Équipe restaurant (`/equipe/gestion/`, `/equipe/reception/`, `/equipe/restaurant/`) ; raccourcis métier et accès croisés refusés selon le rôle.
- Rapport de gestion protégé par rôle avec période personnalisée, activité/encaissements et export CSV des réservations et commandes.
- Journal métier en lecture seule pour les opérations clés et les changements Django Admin audités, accessible au Gestionnaire et à l’administrateur ; le gestionnaire peut créer des employés, changer leurs rôles et les activer/désactiver avec protection du dernier Gestionnaire actif.
- Facture PDF, montant TTC et ventilation paramétrée ; taux par défaut de démonstration égal à zéro.
- Réglages de production Docker/Gunicorn, commande de sauvegarde SQLite/MariaDB.
- Webhook FedaPay signé/idempotent et vérifié avec tests simulés ; la recette avec secrets fournisseur actifs reste à faire.
- Avis clients liés à une réservation terminée, accessibles au client propriétaire après vérification de courriel, envoi unique, modération et réponse publique de l’hôtel. Les catégories, images, votes et signalements restent à réaliser.
- `manage.py check` et `makemigrations --check` réussis ; 37 tests automatisés passent en SQLite temporaire. Un essai concurrent sur deux requêtes accepte une seule réservation de la dernière unité sous SQLite et MariaDB.
- Génération d’une sauvegarde SQLite puis copie/restauration isolée vérifiée avec `PRAGMA integrity_check`.

Ces vérifications ne remplacent pas une recette métier exhaustive. L’essai de restauration MariaDB en environnement de production et l’exercice de paiement avec un compte FedaPay actif restent à réaliser.

## Travaux restants

1. **Réservations et inventaire** : arrivées/départs, changement d’unité, modifications réception/client, annulation après confirmation, garantie, encaissements partiels/manuels, historique de paiement et facture au solde complet sont livrés. Un test de deux réservations simultanées de la dernière chambre passe sur SQLite et MariaDB : une seule est acceptée.
2. **Tarification** : tarifs par jours, durées min/max, restrictions d’arrivée/départ, remises saisonnières, codes promo à durée et nombre d’utilisations, frais fixes/proportionnels et ventilation fiscale configurable sont livrés. Le taux et les mentions fiscales doivent encore être validés pour le contexte légal d’utilisation ; le projet ne prétend pas certifier la conformité fiscale.
3. **Paiements** : rapprochement manuel d’une transaction par relecture API, suivi des remboursements, prévention des doublons/surremboursements livrés. Le code et les exemples de déploiement imposent désormais FedaPay sandbox ; restent la recette fournisseur avec un compte sandbox actif et l’exécution effective d’un remboursement.
4. **Courriels** : modèles français d’activation, réservation, commande et reçu ; journal des tentatives et reprise progressive via worker Docker livrés. Le contenu en attente est chiffré et effacé après succès/abandon. Reste un essai SMTP réel avec un compte actif.
5. **Restaurant** : options tarifées, tables, stock facultatif, seuils, mouvements automatiques/manuels, transitions contrôlées, file cuisine tactile, ticket imprimable, remise en stock/libération de table à l’annulation et libération après service livrés ; restent une recette opérationnelle en cuisine et les règles de stock multi-site/fournisseurs.
6. **Administration** : rapport/CSV, journal des opérations métier, audit des ajouts/modifications/suppressions de la plupart des objets Django Admin, permissions par rôle et gestion des comptes employés livrés. Les véritables comptes du personnel nécessitent la liste et les identités fournies par l’hôtel ; les permissions par champ et une recette de séparation des données restent à faire.
7. **Exploitation** : restaurer un dump MariaDB dans un environnement isolé, programmer et externaliser les sauvegardes, configurer supervision et TLS.
8. **Déploiement distant** : le déploiement de production est prêt et les pages d’accueil, d’informations et de restaurant répondent sur le domaine public Vercel. Les URL techniques des déploiements restent protégées par une connexion Vercel ; la MariaDB du poste local n’est pas la base de production.
9. **Portage comparatif** : audit des 61 modules QloApps documenté dans `PORTAGE_QLOAPPS_VERS_PYTHON.md` : 38 modules partiels et 23 absents, aucun déclaré équivalent complet. Les filtres de recherche, le rapport analytique, les avis vérifiés, la newsletter, les liens sociaux, les notifications et la navigation configurable viennent d’être ajoutés ; appliquer les migrations 0025–0029 au redémarrage de Docker puis suivre les écarts listés avant toute annonce de portage complet.

## Limites connues

- Les contenus, chambres et avis de démonstration sont fictifs et les médias peuvent être distants.
- L’adresse SMTP et le paiement doivent être configurés avec des identifiants actuels et valides ; leur présence dans `.env` ne prouve pas le succès d’un envoi ou d’un paiement. La file d’e-mails dépend de `EMAIL_OUTBOX_ENCRYPTION_KEY`, à conserver stable pendant que des messages sont en attente.
- Le contrôle API FedaPay a confirmé l’approbation sandbox de la réservation de démonstration et la correspondance du montant. La commande restaurant de démonstration reste en attente ; aucun essai de paiement en production n’a été réalisé.
- La facture PDF n’atteste pas de la conformité fiscale. Le taux et les mentions doivent être validés avec les règles locales.
- Le compose local utilise `runserver`; seul le profil production démarre Gunicorn, derrière un proxy HTTPS configuré séparément.
- La configuration de paiement est volontairement limitée au sandbox, y compris dans le profil de déploiement.
