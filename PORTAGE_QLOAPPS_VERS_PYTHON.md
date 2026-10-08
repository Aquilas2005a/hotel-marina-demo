# Portage fonctionnel de QloApps vers Python

Ce document suit le portage demandé module par module. Le dépôt PHP QloApps est une référence fonctionnelle ; le résultat Python est une réimplémentation Django, pas une conversion automatique de chaque ligne PHP.

## Règles de portage

- Relever le comportement, les écrans, les rôles, les validations et les opérations de données de chaque module source.
- Recréer ces comportements avec les modèles, vues, formulaires, URL et gabarits Django correspondants.
- Garder les données métier indépendantes des valeurs de démonstration et configurer la langue française, le Bénin, le fuseau Africa/Porto-Novo et les montants en FCFA/XOF.
- Garder deux moteurs sélectionnables : SQLite pour démarrer simplement et MariaDB pour la configuration Docker habituelle ; chaque moteur conserve ses données séparément.
- Recueillir les informations réelles du développeur dans l’assistant d’installation ; ne pas les inventer ni intégrer d’identifiants secrets au code.
- Conserver un jeu de démonstration explicitement fictif et ne pas suggérer d’affiliation avec un hôtel réel.
- Garder dans le dépôt source PHP ses fichiers, historique, licences et crédits. Le dépôt Python est distinct ; si du code ou des ressources sous licence sont repris, respecter la licence correspondante et documenter la provenance.

## Correspondance des modules

| Ordre | Référence PHP | Port Python | État actuel |
|---|---|---|---|
| 1. Installation et identité | `install-dev/`, contrôleur de paramètres hôteliers | `apps/core/views.py`, `forms.py`, `HotelProfile`, `templates/core/installation.html` | Assistant initial et compte administrateur actifs sur l’instance locale ; contrôle des pages vérifié. Reprise/réinitialisation avancée, diagnostics système et assistant de migration restent à faire. |
| 2. Chambres, catégories et tarifs | classes et contrôleurs de réservation hôtelière, modules de chambres | `Room`, `RoomUnit`, `RoomBlock`, `RoomRate`, `RoomFee`, `PromotionCode`, administration Django et services tarifaires | Unités, affectation automatique, suivi du ménage, blocages et verrou d’inventaire par moteur présents. Prix saisonniers, séjour minimum/maximum, restrictions jours d’arrivée/départ, frais fixes/proportionnels, réductions saisonnières et codes à usage limité sont calculés ; inventaire multi-hôtel, règles commerciales avancées et validation fiscale locale restent à faire. |
| 3. Recherche et disponibilité | contrôleurs et classes de recherche de chambres | `AvailabilitySearchForm`, `get_available_room_units`, `rechercher_chambres`, `planning_chambres` | Recherche par dates et capacité, vérification d’inventaire et planning sur 14 jours ; parcours couvert par contrôles HTTP locaux. Règles commerciales avancées restent à faire. |
| 4. Réservations et clients | classes de réservation, contrôleurs client et panier | modèles `Reservation`, `CustomerProfile`, `ReservationPaymentRecord`, vues de compte/réception et services | Inscription/vérification e-mail par lien ou code, demandes, historique, modification client/réception, annulation après confirmation, changement d’unité, arrivée/départ, garantie, état ménage et encaissements partiels/manuels sont présents. Restent l’essai de charge concurrent et les services additionnels. |
| 5. Paiement FedaPay | module FedaPay et ses vues | `apps/core/fedapay.py`, `FedaPayWebhookEvent`, `RefundRecord`, rapprochement `/equipe/paiements/rapprochement/` | Création de transaction, webhook HMAC/idempotent et relecture API pour rapprocher montant/référence/statut présents. Suivi des remboursements fractionnés, déduplication et limites au montant réellement dû présents. L’exécution du remboursement reste côté fournisseur ; la recette sandbox nécessite un compte sandbox actif et son secret webhook. L’environnement live est bloqué par la configuration. |
| 6. Courriels et notifications | modèles de courriels et paramètres back-office | SMTP Django, gabarits `templates/emails/`, activation, récupération, journal `EmailOutbox` et worker de reprise | Modèles français pour activation, réservation, commande restaurant et reçu présents ; échecs SMTP journalisés, contenu chiffré en attente et reprises progressives implémentés/testés. La recette SMTP réelle reste à faire. |
| 7. Restaurant et services | modules hôteliers de commande et services | `MenuItem`, `MenuOption`, `RestaurantTable`, `RestaurantOrder`, `RestaurantStockMovement`, file cuisine et inventaire | Carte, options, table, stock décrémenté/restauré et journalisé, seuils d’alerte, corrections motivées/auditées, services table/chambre/à emporter, transitions, file tactile filtrée/paginée et ticket imprimable présents. La recette opérationnelle cuisine et l’inventaire fournisseur/multi-site restent à faire. |
| 8. Accueil, équipements et avis | blocs hôtel, équipements, témoignages et thème | `HotelInterior`, `HotelAmenity`, `Testimonial`, `templates/core/accueil.html` | Sections administrables et contenu explicitement fictif présents. Gestion média locale, recherche, langues et thèmes avancés restent à faire. |
| 9. Administration, factures et rapports | contrôleurs de gestion, facturation et rapports | tableaux par rôle, rapport `/equipe/rapports/`, export CSV, `BusinessAuditLog`, audit Django Admin, gestion des groupes/états du personnel, `Invoice` et génération PDF | Rapport filtré/CSV, audit métier et des changements Admin principaux, gestion des rôles et création de comptes employés présents ; dernier Gestionnaire actif et compte connecté protégés. Les vrais comptes dépendent du roster fourni par l’hôtel ; les permissions par champ, l’exhaustivité des données et la conformité fiscale locale restent à valider. |
| 10. Exploitation | déploiement, sécurité et sauvegarde | `compose.production.yaml`, `.env.production.example`, commande `backup_database`, projet Vercel | Dernier déploiement de production Vercel à l’état Ready ; les pages publiques `/`, `/informations/` et `/restaurant/` répondent en HTTP 200 sur le domaine principal. Les URL techniques de déploiement exigent une connexion Vercel. Sauvegarde/restauration MariaDB, supervision et exercice d’exploitation restent à faire. |

## Audit détaillé des 61 modules QloApps

La liste ci-dessous a été relevée dans `work/qloapps/modules/`. « Partiel » signifie que certains parcours voisins existent, mais que le module et ses comportements ne sont pas équivalents. « Absent » signifie qu’aucun module Python correspondant n’a été trouvé. Les modules de statistiques composent les tableaux de bord QloApps : ils ne sont pas couverts par les seuls compteurs de cette maquette.

| Module source | État côté Django | Écart vérifié / adaptation à réaliser |
|---|---|---|
| `hotelreservationsystem` | Partiel | Catalogue, disponibilités, réservation, chambre physique, blocage et tarif saisonnier existent. Restent notamment restrictions de séjour, services optionnels, documents de réservation, remboursements et réglages hôteliers complets. |
| `wkhotelroom` | Partiel | Catégories et chambres existent ; variantes, médias, équipements détaillés et règles QloApps restent à reprendre. |
| `wkroomsearchblock` | Partiel | Recherche avec dates, capacité, catégorie, budget moyen/nuit et tri maintenant présents ; carte et options commerciales avancées manquent. |
| `wkhotelfilterblock` | Partiel | Filtres de catégorie et de budget moyen/nuit et tris par prix/capacité présents dans la recherche ; filtres par équipements, carte et facettes dynamiques absents. |
| `wkhotelfeaturesblock` | Partiel | Équipements administrables basiques, sans la configuration de caractéristiques et de prix du module. |
| `wkabouthotelblock` | Partiel | Présentation d’hôtel simplifiée dans les contenus de la page d’accueil. |
| `wktestimonialblock` | Partiel | Témoignages de démonstration affichés ; collecte, vérification, réponse et modération d’avis absentes. |
| `qlohotelreview` | Partiel | Avis relié à un séjour terminé, client authentifié avec courriel vérifié, envoi unique depuis l’espace client, statut de modération, réponse publique de l’hôtel et carrousel défilant sur l’accueil maintenant disponibles. Les catégories de notation, images, votes d’utilité, signalements, pagination et notification de réponse du module source restent absents. |
| `qlofedapay` | Partiel | Création de transaction et lecture de statut ; webhook signé/idempotent maintenant présent. Remboursement, rapprochement et recette avec secrets actifs manquent. |
| `qloduitkupayment` | Absent | Aucun connecteur Duitku. |
| `qlopaypalcommerce` | Absent | Aucun connecteur PayPal. |
| `bankwire` | Absent | Aucun paiement par virement bancaire. |
| `cheque` | Absent | Aucun paiement par chèque. |
| `qlochannelmanagerconnector` | Absent | Pas de synchronisation avec channel manager, canaux ou réservations externes. |
| `qlocrontaskmanager` | Absent | Pas d’ordonnanceur d’exploitation pour tâches récurrentes et relances. |
| `qlocleaner` | Partiel | Statut de ménage sur les unités disponible ; affectation de tâches, suivi et historique des opérations absents. |
| `qlohotelreports` | Partiel | Rapport par période, CSV, comparaison à une période précédente, taux d’occupation sur les unités assignées, recettes quotidiennes/hebdomadaires, catégories, meilleurs clients et plats maintenant présents ; canaux, prévisions et exports statistiques complets manquent. |
| `qlostatsserviceproducts` | Partiel | Classement restaurant par quantité et recettes encaissées ; statistiques des services hôteliers annexes absentes. |
| `dashactivity` | Partiel | Le tableau de gestion montre quelques compteurs récents, sans flux d’activité QloApps. |
| `dashavailability` | Partiel | Planning local sur 14 jours, sans bloc de disponibilité paramétrable équivalent. |
| `dashgoals` | Absent | Pas d’objectifs commerciaux. |
| `dashguestcycle` | Partiel | Classement des clients et nombre de séjours dans la période ; étapes de cycle, rétention et segmentation absentes. |
| `dashinsights` | Partiel | Vue agrégée avec occupation, recettes, catégories et meilleurs produits ; conseils automatiques absents. |
| `dashoccupancy` | Partiel | Occupation historique calculée par nuitée disponible, hors blocages ; prévision et graphique dédié absents. |
| `dashperformance` | Partiel | Indicateurs de recettes et occupation disponibles ; comparaison à une période précédente de même durée ajoutée aux rapports. Objectifs et ratios avancés restent absents. |
| `dashproducts` | Partiel | Classement des plats encaissés par quantité et recettes ; analyse complète des services hôteliers absente. |
| `dashtrends` | Partiel | Tendance de recettes par jour ou semaine disponible ; graphiques interactifs et analyses saisonnières absents. |
| `graphnvd3` | Absent | Pas de bibliothèque/présentation graphique correspondante. |
| `gridhtml` | Partiel | Listes Django Admin, sans grille QloApps ni personnalisation équivalente. |
| `statsbestcategories` | Partiel | Catégories de chambres et restaurant classées par recettes encaissées ; comparaison détaillée des produits absente. |
| `statsbestcustomers` | Partiel | Classement des clients par recettes et nombre de séjours ; segmentation et export dédiés absents. |
| `statsbestproducts` | Partiel | Classement restaurant par recettes et quantités ; autres services non couverts. |
| `statsbestvouchers` | Absent | Pas de statistiques de bons ou réductions. |
| `statscatalog` | Partiel | Recettes des catégories de chambres et restaurant affichées ; disponibilités, coûts et marges du catalogue non analysés. |
| `statscheckup` | Absent | Pas de diagnostic statistique du catalogue et de la boutique. |
| `statsdata` | Absent | Pas de couche de collecte/agrégation de statistiques équivalente. |
| `statsequipment` | Absent | Pas de statistiques sur les équipements. |
| `statsforecast` | Absent | Pas de prévisions. |
| `statslive` | Absent | Pas de suivi en direct. |
| `statsnewsletter` | Absent | Pas de statistiques de newsletter. |
| `statsorigin` | Absent | Pas de statistiques de provenance du trafic. |
| `statspersonalinfos` | Absent | Pas de rapport démographique/statistique client. |
| `statsproduct` | Partiel | Ventes restaurant agrégées par article ; détail temporel et services hôteliers annexes absents. |
| `statsregistrations` | Absent | Pas de statistiques d’inscription. |
| `statssales` | Partiel | Recettes encaissées regroupées par période et par source, dans le rapport de gestion ; exports et rapprochement comptable absents. |
| `statsvisits` | Absent | Pas de statistiques de visites. |
| `sekeywords` | Absent | Pas de rapport des mots-clés de recherche. |
| `pagesnotfound` | Absent | Pas de rapport administrable des pages introuvables. |
| `qlogoogletranslate` | Absent | Pas de connecteur de traduction Google ; l’interface Python est principalement en français. |
| `blockcart` | Partiel | La commande restaurant est un panier simplifié ; pas de panier hôtel QloApps et de ses règles de commande. |
| `blockcurrencies` | Partiel | Montants fixés en FCFA/XOF ; sélection multidevise absente. |
| `blocklanguages` | Partiel | Interface française ; changement de langue absent. |
| `blockmyaccount` | Partiel | Compte client, réservations et factures existent ; les autres fonctions du compte source manquent. |
| `blocknavigationmenu` | Partiel | Liens de navigation éditables, ordonnables, activables et limités aux chemins locaux ou à HTTPS ; menus imbriqués, ciblage par langue et règles d’affichage restent absents. |
| `blocknewsletter` | Partiel | Formulaire réel, double confirmation par courriel, état de consentement, désinscription avec jeton personnel et liste de gestion maintenant présents. Campagnes, préférences, modèles personnalisables, export et statistiques d’envoi restent absents. |
| `blocksocial` | Partiel | Liens sociaux configurables, activables et triables dans l’administration ; statistiques, flux de publication et configuration avancée absents. |
| `blockuserinfo` | Partiel | Liens de compte affichés, sans le bloc configurable et ses états complets. |
| `wkfooterlangcurrencyblock` | Absent | Pas de choix de langue et de devise dans le pied de page. |
| `wkfooternotificationblock` | Partiel | Notifications de site avec date de début/fin, type, ordre et lien configurables dans l’administration ; ciblage utilisateur et fermeture individuelle absents. |
| `wkfooterpaymentblock` | Partiel | Moyens de paiement affichés ; les logos et règles d’activation du module ne sont pas repris. |
| `wkfooterpaymentinfoblockcontainer` | Partiel | Informations statiques de démonstration uniquement. |

### Contrôle des espaces utilisateur Python

| Espace | Fonction livrée dans ce correctif | Limites restantes |
|---|---|---|
| Gestionnaire hôtelier | Tableau avec réservations, occupation, ménage, blocages, paiements/rapprochement/remboursements, factures, personnel, rapports/CSV et journal métier ; audit des objets gérés depuis Django Admin. | Rôles prédéfinis ; droits par champ et validation fiscale locale à réaliser. |
| Réceptionniste | Accueil et dossier de séjour pour modifier/annuler, changer d’unité, encaisser en plusieurs fois et imprimer la facture ; transitions arrivée/départ avec garantie et ménage. | Le remboursement effectif par le fournisseur reste à faire dans son tableau de bord. |
| Équipe restaurant | Accueil, file cuisine tactile filtrable/paginée, tickets imprimables, transitions, options/tables, inventaire à seuil et journal de stock. | Recette opérationnelle et règles fournisseurs/multi-site à réaliser. |
| Client | Compte, vérification, récupération, réservations, commandes et factures de son compte. | Pas de modification libre des coordonnées, fidélité, messagerie ou annulation avec remboursement. |

Les espaces employés possèdent maintenant des URL explicites : `/equipe/gestion/`, `/equipe/reception/`, `/equipe/restaurant/` et `/equipe/restaurant/cuisine/`. L’URL `/equipe/` choisit l’espace depuis les permissions attribuées. Une URL d’un autre rôle renvoie une interdiction, elle ne donne pas accès à son tableau.

### Liste de travail restante par ordre fonctionnel

1. **Réception** : édition/annulation, déplacement d’unité, garantie et encaissements partiels/manuels livrés ; un essai de deux réservations concurrentes passe avec SQLite et MariaDB.
2. **Restaurant** : file cuisine tactile, seuils de stock et historique des mouvements livrés ; restent recette opérationnelle, inventaire fournisseur et gestion multi-site.
3. **Administration et rapports** : rapport CSV, taux d’occupation, comparaison avec la période précédente, recettes par jour/semaine, catégories, meilleurs clients et plats, audit métier/Admin et gestion des rôles/statuts livrés ; restent prévisions, statistiques de canaux, couverture exhaustive et exports PDF.
4. **Réservations** : services additionnels et journal exhaustif de toutes les transitions.
5. **Paiements** : recette sandbox avec des identifiants sandbox actifs et remboursement effectué dans le tableau de bord fournisseur.
6. **Avis et contenu** : dépôt depuis l’espace client et carrousel défilant en page d’accueil livrés ; les avis restent soumis à modération avant affichage public. Restent les catégories de notation, images, votes d’utilité, signalements, pagination, notification au client, gestion des médias et contenus multilingues.
7. **Connectivité et exploitation** : channel manager, tâches récurrentes, sauvegarde restaurée pour MariaDB, supervision, TLS et recette de production.
8. **Communication et navigation** : formulaire newsletter double opt-in, liens sociaux et navigation administrables livrés ; les liens d’accueil et du pied de page ont maintenant des destinations valides. Restent campagnes, statistiques d’envoi, multilingue/multidevise, menus imbriqués et règles d’affichage avancées.

Sur les **61 modules source audités**, aucun n’est encore déclaré équivalent complet : **38 sont partiels et 23 absents**. Le tableau détaille les écarts ; la liste de travail ci-dessus regroupe les principales fonctions restantes.

Cet audit constate **une parité partielle** et ne présente pas le Django comme une copie complète ou une conversion fichier par fichier. Les fonctionnalités détaillées ci-dessus sont la base de recette pour poursuivre le portage et garder sa provenance/licence documentée.

## Tranche livrée — Avis clients vérifiés

- Un client connecté dont l’adresse est vérifiée peut déposer un avis uniquement pour sa propre réservation avec statut « départ enregistré ».
- Une contrainte d’unicité limite chaque réservation à un seul avis ; note de 1 à 5 et commentaire d’au moins 15 caractères.
- Les nouveaux avis restent cachés au public jusqu’à leur publication par un administrateur ; l’hôtel peut ajouter une réponse publique et l’avis peut être refusé.
- Le parcours est accessible depuis « Mon compte » ; les avis publiés apparaissent sur l’accueil avec la mention « séjour vérifié ».
- La migration `0025_hotelreview` doit être appliquée à la base locale lorsque Docker/MariaDB est disponible. La session Docker n’était pas démarrée lors de cette tranche ; aucune vérification d’exécution n’est revendiquée.

## Tranche livrée — Inscription newsletter

- Le formulaire du pied de page enregistre une adresse normalisée en état « confirmation en attente » et envoie un lien de confirmation à durée limitée via la file d’e-mails existante.
- La visite du lien ne change pas l’état à elle seule ; le client confirme explicitement par formulaire protégé CSRF.
- Après confirmation, un lien personnel permet la désinscription avec confirmation POST ; le jeton est renouvelé lors d’une nouvelle inscription.
- Les adresses et leur état sont consultables dans l’administration. Les campagnes et les statistiques d’envoi ne sont pas implémentées.
- La migration `0026_newslettersubscription` suit la migration `0025_hotelreview` et sera appliquée au redémarrage de l’application quand Docker sera disponible.

## Tranche livrée — Réseaux sociaux

- Les liens Facebook, Instagram, YouTube, TikTok, LinkedIn, WhatsApp et autres sont configurables et activables dans l’administration.
- Le pied de page n’affiche que les liens actifs, dans l’ordre configuré ; les liens ouvrent une nouvelle fenêtre avec les attributs de sécurité appropriés.
- La migration `0027_sociallink` complète les migrations précédentes et sera appliquée au redémarrage de l’application quand Docker sera disponible.

## Tranche livrée — Notifications du site

- L’administration permet de programmer des notifications d’information, de succès ou d’avertissement, avec ordre, dates de visibilité et lien facultatif.
- Le site n’affiche que les notifications actives dont la période inclut la date locale ; les avertissements sont annoncés comme alertes accessibles.
- La migration `0028_sitenotice` complète les précédentes et sera appliquée au redémarrage de l’application quand Docker sera disponible.

## Tranche livrée — Navigation configurable

- L’administration permet de définir le libellé, le chemin local ou l’URL HTTPS, l’ordre et l’état actif des liens de navigation.
- Les URL externes HTTP et les chemins protocol-relative sont rejetés par la validation du modèle.
- La navigation par défaut reste affichée tant qu’aucun lien administré n’est actif ; la migration `0029_navigationlink` est à appliquer après le retour de Docker.

## Prochaine tranche de travail

Poursuivre les fonctions de `qlohotelreview` (notes par catégorie, images, votes utiles, signalements et pagination), puis les modules de statistiques/dashboards absents listés plus haut. Les validations externes (recette FedaPay sandbox, SMTP réel, remboursement effectivement traité, données du personnel) restent nécessaires à part. La compatibilité ne sera déclarée que lorsque chaque parcours aura une implémentation réelle et une vérification documentée.

## Tranche livrée — Avis depuis l’espace client et défilement

- Depuis « Mon compte », le client peut ouvrir le formulaire d’avis pour chaque réservation terminée dont il est propriétaire. Le serveur exige un compte connecté et une adresse e-mail vérifiée, et limite le dépôt à un avis par séjour.
- Le client voit l’état de son avis et peut le consulter après l’envoi. L’avis est envoyé pour modération ; il apparaît dans le carrousel public uniquement après publication par l’équipe.
- Les avis publiés et les témoignages de démonstration défilent dans un carrousel avec boutons précédent/suivant. Le défilement automatique s’arrête au survol, au focus et lorsque l’onglet est masqué ; il est désactivé si le navigateur demande une réduction des animations.
- Les témoignages marqués comme fictifs restent identifiés comme tels.

## Tranche livrée — Liens du site et comparaison des rapports

- Les liens du pied de page (confidentialité, mentions, conditions, présentation et paiements) conduisent maintenant vers des sections présentes sur `/informations/` ; leur contenu précise les limites de la maquette et ne prétend pas remplacer les mentions de l’exploitant réel.
- Les liens d’ancre du menu principal sont rattachés à l’accueil pour fonctionner depuis les pages intérieures. Le menu mobile peut être ouvert/fermé au bouton, à l’échap, au clic extérieur et au choix d’un lien.
- Le rapport de gestion compare maintenant recettes encaissées, réservations et commandes à une période précédente de même durée, affichée explicitement.
- Les statistiques de prévision, les objectifs commerciaux et les autres modules absents restent à réaliser ; ce correctif ne change pas le décompte de parité globale (38 partiels, 23 absents).

## Provenance

Le dépôt de référence est QloApps, dont le noyau est identifié dans le dépôt source comme OSL-3.0 ; plusieurs modules ont leurs propres fichiers de licence. Les mentions et obligations applicables doivent accompagner toute redistribution d’éléments dérivés. Les données de configuration et de démonstration du projet Python sont séparées des fichiers PHP sources.
