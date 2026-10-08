# Naya Marina — gestion hôtelière (prototype)

Application indépendante en Python/Django avec SQLite ou MariaDB. Le projet reprend des besoins courants de gestion hôtelière avec un code et une identité visuelle propres. Il ne contient pas le code PHP de QloApps.

## Fonctionnalités disponibles

- Assistant local pour saisir l’identité de l’hôtel et du développeur et créer le compte administrateur.
- Page d’accueil de démonstration et catalogue de chambres fictives.
- Blocs intérieurs, équipements, avis et carte restaurant administrables.
- Formulaire de demande de réservation : dates, chambre, nombre de voyageurs, coordonnées et montant indicatif.
- Recherche des catégories disponibles par dates et capacité.
- Tarifs saisonniers administrables par chambre et période, appliqués nuit par nuit, avec jours tarifés, jours d’arrivée/de départ autorisés, minimum/maximum de nuits et réduction configurables.
- Frais d’hôtel configurables (montant fixe par séjour/par nuit ou pourcentage), figés sur la réservation et détaillés sur la facture ; taxe paramétrée en sus.
- Unités de chambre physiques codées, affectées automatiquement aux réservations et administrables avec la catégorie.
- Planning équipe des chambres sur 14 jours, états de ménage et blocages de maintenance qui retirent une unité des résultats de disponibilité.
- Cycle de séjour avec confirmation, arrivée et départ ; le départ rend l’unité à nettoyer. L’arrivée exige une chambre propre et un paiement ou une garantie enregistrée.
- Comptes clients, connexion, historique, modification des demandes non payées et annulation. Une réservation confirmée est annulable jusqu’à la date limite paramétrée ; si elle est déjà payée, un remboursement à traiter est signalé sans prétendre rembourser le fournisseur.
- Vérification de l’adresse e-mail par lien signé ou code alphanumérique à usage unique valable 30 minutes ; le code est conservé haché et le renvoi remplace l’ancien.
- Vérification serveur des dates, de la capacité et de l’inventaire avant l’enregistrement et la modification ; les écritures d’inventaire sont sérialisées par verrou de ligne sous MariaDB et verrou d’écriture SQLite.
- Courriels transactionnels avec journal d’envoi, contenu chiffré temporairement après échec SMTP, reprise progressive par un worker Docker et effacement du contenu après succès ou abandon.
- Parcours FedaPay côté serveur pour les séjours et commandes restaurant : transaction en XOF, lien hébergé et lecture du statut API ; webhook signé HMAC-SHA256, fenêtre anti-rejeu de 5 minutes, contrôle de référence/montant et journal idempotent (relecture API si l’événement ne contient pas les détails de transaction).
- Reçu de paiement imprimable, émis après une confirmation FedaPay et accessible depuis l’espace client.
- Facture PDF téléchargeable avec ventilation du montant hors taxe et de la taxe incluse, figée à l’émission ; le taux se règle par `HOTEL_TAX_RATE` et vaut `0` par défaut.
- Carte administrable avec options et suppléments, suivi de stock facultatif, choix de table disponible, commandes à table/en chambre/à emporter et transitions contrôlées par l’équipe. Une annulation remet le stock et libère la table ; le service terminé libère aussi la table. L’assistant peut créer des tables, suppléments et stocks d’exemple.
- File cuisine du personnel `/equipe/restaurant/cuisine/` avec filtres d’état, pagination, fiche de commande et ticket imprimable ; accès réservé aux profils autorisés du restaurant/gestion.
- Corrections manuelles de stock dans l’espace restaurant avec motif obligatoire, contrôle anti-stock négatif et inscription dans le journal ; la quantité n’est plus modifiable directement dans la fiche d’administration.
- Administration des chambres, clients et réservations dans `/admin/`.
- Gestion des comptes employés et rôles depuis `/equipe/personnel/` pour le gestionnaire ; rôles : gestionnaire hôtelier, réceptionniste et équipe restaurant. Les droits sont appliqués par groupes Django.
- Réinitialisation du mot de passe par lien e-mail pour les comptes clients et personnel : le lien est disponible sur la connexion du site et celle de l’administration. Le changement n’est effectif qu’après ouverture du lien reçu par e-mail.
- Depuis le même espace, le Gestionnaire peut changer le rôle et activer/désactiver les employés ; il ne peut pas modifier son compte depuis cet écran, ni désactiver ou rétrograder le dernier Gestionnaire actif.
- Rapport de gestion privé `/equipe/rapports/` avec filtre de dates, recettes encaissées, comparaison à la période précédente et export CSV des séjours/commandes ; seuls le gestionnaire et l’administrateur peuvent l’ouvrir.
- Page d’informations dédiée et liens de menu/pied de page qui aboutissent à des sections existantes ; le menu mobile est actionnable.
- Journal métier en lecture seule dans l’administration : créations/modifications/annulations de séjour, mouvements des commandes, paiements FedaPay et création d’employés. Visible au Gestionnaire et à l’administrateur.
- Commande de sauvegarde SQLite/MariaDB : `python manage.py backup_database` (fichiers dans `backups/`).

La ventilation de taxe ne valide pas la conformité fiscale d’un pays : configure le taux et les mentions exigées après vérification locale avant tout usage commercial. Les tarifs et chambres de démonstration sont fictifs. Le remboursement fournisseur n’est pas automatisé : après traitement réel dans le tableau FedaPay, le remboursement doit être rapproché manuellement. Pour le webhook, configure `FEDAPAY_WEBHOOK_SECRET` avec le secret propre au point de terminaison dans FedaPay et enregistre `https://<domaine>/paiements/fedapay/webhook/` pour les événements de transaction. Ce secret est distinct de la clé API FedaPay. La fenêtre d’annulation des réservations confirmées se règle en jours avec `HOTEL_CANCELLATION_NOTICE_DAYS` (2 par défaut). Voir [ROADMAP.md](ROADMAP.md).

## Vérifications automatisées

Lancer les tests dans le conteneur avec une base SQLite temporaire afin de ne pas demander au compte MariaDB applicatif de créer une base de test :

```powershell
docker compose exec -T -e DATABASE_ENGINE=sqlite -e SQLITE_PATH=/tmp/naya-tests.sqlite3 application python manage.py test apps.core
```

Les 25 tests couvrent les rôles, les transitions de séjour, les restrictions de tarif, les frais figés, le parcours commande/options/tables/stock/transitions/corrections restaurant, le rapport/CSV avec contrôle d’accès et protection contre les formules, les entrées du journal métier, le webhook signé/idempotent et son rejet si signature invalide, les courriels journalisés et la reprise d’un échec SMTP simulé, les paiements restant en attente, les factures et les vérifications de compte. Ils ne certifient pas un encaissement ou remboursement réel, un serveur SMTP de production, ni la conformité fiscale.

## Choisir la base de données

Les deux modes utilisent le même code Django. Choisis un seul mode à la fois, car chacun utilise le port local `8091`.

### MariaDB avec Docker

Mode recommandé pour la démonstration actuelle. Docker Desktop est configuré pour démarrer à l’ouverture de session Windows et les conteneurs sont marqués `unless-stopped` pour repartir avec le moteur Docker. Pour démarrer ou recréer les services manuellement :

```powershell
docker compose up --build -d
```

MariaDB conserve les données dans un volume Docker. C’est le mode par défaut (`DATABASE_ENGINE=mariadb`).

### Déploiement Vercel

Le projet inclut une configuration Django Vercel (`vercel.json`) et ajoute automatiquement le domaine Vercel aux hôtes et origines CSRF autorisés. Avant le premier déploiement, lie le dossier au projet Vercel et configure dans ses variables d’environnement **Production et Preview** : `DJANGO_SECRET_KEY`, `DJANGO_DEBUG=0`, `DATABASE_ENGINE=mysql`, `MYSQL_HOST`, `MYSQL_PORT`, `MYSQL_DATABASE`, `MYSQL_USER`, `MYSQL_PASSWORD`, `FEDAPAY_ENVIRONMENT=sandbox`, `FEDAPAY_SECRET_KEY` (clé `sk_sandbox_…`) et `FEDAPAY_WEBHOOK_SECRET`. La base doit être une instance MySQL/MariaDB managée joignable depuis Vercel ; la base MariaDB de Docker sur le poste local n’est pas accessible au déploiement. Ne déploie pas avec SQLite, car son fichier ne constitue pas un stockage durable partagé entre les fonctions Vercel.

Une fois l’authentification Vercel et la base distante configurées, depuis ce dossier :

```powershell
vercel link
vercel deploy --prod
```

Les migrations Vercel se lancent depuis un environnement autorisé à joindre la base distante avec `python manage.py migrate`. Le worker de reprise des courriels Docker est séparé et n’est pas démarré comme fonction Web Vercel ; pour l’exploitation durable des reprises, déploie ce worker en service conteneurisé distinct.

### SQLite avec Docker

Pour le lancement léger, sans serveur MariaDB :

```powershell
docker compose -f compose.sqlite.yaml up --build -d
```

SQLite enregistre la base dans `database/db.sqlite3` sur le dossier du projet. Ce fichier est local et exclu de Git.

Pour passer d’un mode à l’autre, arrête d’abord celui qui tourne : `docker compose down` pour MariaDB, ou `docker compose -f compose.sqlite.yaml down` pour SQLite, puis démarre l’autre mode. Chaque base conserve ses propres données ; aucune synchronisation automatique n’est faite entre elles.

Après le démarrage, ouvrir <http://localhost:8091/installation/> et suivre l’assistant. L’installation crée le compte administrateur uniquement lorsque le formulaire est soumis. Les mots de passe se choisissent dans cet assistant et ne sont pas prédéfinis.

Pour afficher les journaux MariaDB : `docker compose logs -f application`. Pour SQLite, ajouter `-f compose.sqlite.yaml`. Les données restent dans leur stockage respectif après l’arrêt.

## Configuration des services externes

Ne pas mettre de secrets dans le dépôt, les pages HTML ou le navigateur. Les champs se configurent dans le fichier local `.env`, jamais dans Git.

- **FedaPay** : l’application impose `FEDAPAY_ENVIRONMENT=sandbox`, y compris avec `compose.production.yaml`. Configure `FEDAPAY_SECRET_KEY` avec une clé `sk_sandbox_…` active et `FEDAPAY_WEBHOOK_SECRET` avec le secret distinct du point de terminaison. Le retour du navigateur ne suffit pas à confirmer le paiement : le statut est vérifié par l’API authentifiée et les webhooks signés sont dédupliqués. Le remboursement fournisseur reste manuel tant que son API et son rapprochement ne sont pas implémentés.
- **Courriels de production** : renseigner `EMAIL_BACKEND=django.core.mail.backends.smtp.EmailBackend`, `EMAIL_HOST`, `EMAIL_PORT`, `EMAIL_USE_TLS`, `EMAIL_HOST_USER`, `EMAIL_HOST_PASSWORD` et `DEFAULT_FROM_EMAIL` avec des identifiants valides. `EMAIL_OUTBOX_ENCRYPTION_KEY` doit être une clé Fernet secrète et persistante, identique dans l’application et le worker. Générer une clé avec `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"`; ne pas la changer tant qu’une file contient des messages chiffrés. Le backend console local ne valide pas la livraison SMTP.

Pour demander un lien de réinitialisation, ouvrir `/compte/mot-de-passe/oubli/` et saisir l’adresse e-mail du compte. Sur Vercel, la livraison dépend des paramètres SMTP de production ci-dessus. Vérifier également les courriers indésirables. Les comptes employés peuvent aussi être créés avec un nouveau mot de passe depuis `/equipe/personnel/` par un gestionnaire autorisé.

Les secrets locaux se configurent dans `.env` et ne doivent pas être copiés dans le dépôt. Pour une installation de déploiement, utilise `.env.production` dérivé du modèle fourni ; FedaPay y reste aussi en mode sandbox.

## Sauvegardes

Crée une sauvegarde ponctuelle :

```powershell
docker compose exec application python manage.py backup_database
```

La commande écrit une copie SQLite cohérente ou un export MariaDB transactionnel dans `backups/`. Programme-la avec le planificateur du serveur, copie une seconde version hors de la machine, limite l’accès au dossier et essaie régulièrement une restauration. Une sauvegarde qui n’a jamais été restaurée n’est pas validée.

## Déploiement de production

Le compose de démonstration utilise `runserver` et n’est pas destiné à Internet. Pour la configuration de production, copie `.env.production.example` en `.env.production`, remplace chaque valeur d’exemple, génère une clé Django aléatoire, renseigne le domaine HTTPS et configure le taux de taxe selon les règles applicables. Ne partage pas ce fichier et garde-le hors de Git.

```powershell
docker compose --env-file .env.production -f compose.production.yaml up --build -d
```

Ce profil désactive `DEBUG`, utilise Gunicorn et WhiteNoise, impose les cookies sécurisés et la redirection HTTPS, et garde MariaDB non publiée sur le réseau hôte. Place un proxy HTTPS (par exemple Nginx ou Caddy) devant le port local `8092`, active le renouvellement TLS et configure les en-têtes transmis. `DJANGO_SECURE_PROXY_SSL_HEADER=1` n’est à activer que si le proxy de confiance remplace correctement `X-Forwarded-Proto`. Vérifie les sauvegardes, la restauration, les e-mails, les paiements, les journaux et les en-têtes de sécurité avant une ouverture publique.
