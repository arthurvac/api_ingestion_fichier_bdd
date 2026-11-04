# Choix techniques et évolutions

[Sommaire](README.md) · [Fonctionnement](fonctionnement.md) · [Exploitation et échelle](exploitation.md)

## Technologies retenues

| Outil | Pourquoi il est utilisé | Compromis |
|---|---|---|
| Python 3.13 | Code commun Windows/Linux, annotations et bibliothèques de fichiers | Version déclarée dans `pyproject.toml`, venv propre par système |
| FastAPI + Uvicorn | HTTP, multipart, OpenAPI et routes synchrones | Le traitement occupe un thread pendant l’import ; pas de queue asynchrone |
| Pydantic 2 + email-validator | Contrat central, types et validations auprès des champs | Ne remplace pas les contraintes SQL ni les décisions sémantiques |
| `csv` + openpyxl + defusedxml | Chaînes CSV et types Excel conservés, lecture itérative et XML protégé | Les métadonnées/chaînes partagées XLSX peuvent consommer de la mémoire |
| charset-normalizer | Aide contrôlée aux encodages occidentaux, après BOM/UTF-8 strict | L’intention d’un encodage sans marque n’est jamais garantie pour tous les octets |
| PostgreSQL 17 | Relations, JSONB, `DATE`, unicité avec NULL et verrous transactionnels | La base est une dépendance obligatoire, avec sauvegardes à exploiter |
| SQLAlchemy 2 + psycopg 3 | Requêtes lisibles, session par requête, transactions et pool | Les limites de connexions doivent être dimensionnées entre processus/instances |
| Alembic | Historique reproductible du **schéma**, à partir de `0001_v1` | Une migration doit être relue ; ce n’est pas l’historique des valeurs métier |
| Docker + Compose | Images officielles sur Debian 13 Trixie, environnement commun Windows/Linux, réseau, volumes et quotas | Les budgets sont par conteneur ; le volume ne constitue pas une sauvegarde |
| pip + setuptools et fichiers `.lock` | Paquet installable et versions Python exactes | Les tags système Docker ne sont pas figés par digest |
| Pytest + httpx / TestClient | Tests unitaires et intégration sur PostgreSQL réel | Les fichiers de référence restent locaux, sans données personnelles publiées |
| Black | Convention de formatage commune | Ni vérification de typage ni analyse de sécurité exhaustive |

Les versions exactes sont dans les deux `.lock`. `tzdata` est une dépendance de psycopg sous Windows uniquement. Aucun DataFrame, Redis, SQLite de travail, framework d’audit ou système de plugins n’est nécessaire au parcours actuel.

Les images sont `python:3.13-slim-trixie` et `postgres:17-trixie`, également pour les tests. Trixie fixe la génération Debian, tandis que `slim` limite les paquets préinstallés de l’API. Les tags reçoivent des correctifs et ne garantissent pas une image identique dans le temps ; un digest est nécessaire pour cela. Le changement de distribution conserve Python 3.13 et PostgreSQL 17.

## Maintenance des dépendances

`pyproject.toml` est la source à modifier : `[project.dependencies]` pour l’API, `[project.optional-dependencies].dev` pour les outils de développement. Les deux `.lock` sont des fichiers de requirements lisibles par pip ; ils fixent aussi les dépendances indirectes. `requirements-dev.lock` contient l’API et les outils de test, avec les mêmes versions communes que `requirements.lock`.

```mermaid
flowchart LR
    Besoin["Modifier pyproject.toml"] --> Production["Générer requirements.lock"]
    Production --> Developpement["Générer requirements-dev.lock avec contrainte runtime"]
    Developpement --> Verification["Relire le diff et tester Windows / Linux"]
    Verification --> Commit["Versionner ensemble les fichiers concernés"]
```

### Préparer l’outil de génération

Depuis la racine du dépôt, dans un venv Python 3.13 activé, installer **uv 0.13.7**, version utilisée pour vérifier cette procédure. Les commandes ci-dessous fonctionnent dans PowerShell et Bash ; les étapes de création du venv figurent dans le [README](../README.md#développement-python-local--facultatif).

```sh
python -m pip install "uv==0.13.7"
python -m uv --version
```

uv sert uniquement à cette maintenance : pip reste utilisé pour installer les dépendances et Docker n’a pas besoin d’uv. La génération lit `pyproject.toml`, pas les paquets du venv. Ne pas remplacer ce processus par `pip freeze > requirements.lock` : cela inclurait les outils locaux et perdrait les conditions propres aux autres systèmes.

### Ajouter ou supprimer une dépendance

Modifier d’abord la liste ou la contrainte correspondante dans `pyproject.toml`, puis générer **dans cet ordre** :

```sh
python -m uv pip compile pyproject.toml --universal --python-version 3.13 --output-file requirements.lock
python -m uv pip compile pyproject.toml --extra dev --universal --python-version 3.13 --constraint requirements.lock --output-file requirements-dev.lock
```

Conserver les fichiers existants avant génération : uv privilégie leurs versions tant qu’elles restent compatibles. Une suppression retire les paquets devenus inutiles, sauf s’ils sont encore requis indirectement. La contrainte du second appel empêche le fichier de développement de choisir d’autres versions pour les dépendances de l’API.

`--universal` conserve les variantes de plateforme, notamment `tzdata` et `colorama` conditionnés à Windows ; ne pas recopier ces marqueurs à la main. `--python-version 3.13` correspond à la version du projet. Une résolution universelle ne remplace pas les tests sur les systèmes cibles. Une première régénération peut ajouter des commentaires et préciser les marqueurs sans changer les versions.

### Mettre à jour les versions

Pour cibler une bibliothèque de l’API, par exemple openpyxl, ajouter `--upgrade-package` au premier appel, puis régénérer le fichier de développement :

```sh
python -m uv pip compile pyproject.toml --universal --python-version 3.13 --upgrade-package openpyxl --output-file requirements.lock
python -m uv pip compile pyproject.toml --extra dev --universal --python-version 3.13 --constraint requirements.lock --output-file requirements-dev.lock
```

Pour cibler un outil comme Black, conserver le premier appel normal et ajouter `--upgrade-package black` au second. Les dépendances indirectes nécessaires peuvent également évoluer. Pour une mise à jour globale, utiliser :

```sh
python -m uv pip compile pyproject.toml --universal --python-version 3.13 --upgrade --output-file requirements.lock
python -m uv pip compile pyproject.toml --extra dev --universal --python-version 3.13 --constraint requirements.lock --upgrade --output-file requirements-dev.lock
```

Ces mises à jour respectent toujours les bornes de `pyproject.toml` ; élargir une borne majeure demande une décision et des tests adaptés. La version de setuptools dans `[build-system].requires` se maintient séparément : elle concerne la construction du paquet, pas les dépendances d’exécution figées ici. Les images Docker se mettent également à jour séparément.

### Vérifier et valider la mise à jour

Relire `git diff -- pyproject.toml requirements.lock requirements-dev.lock`, les notes de version des bibliothèques modifiées et les éventuels changements de dépendances indirectes. Tester l’installation dans un **venv Python 3.13 propre** sur Windows, selon le README :

```sh
python -m pip install -r requirements-dev.lock
python -m pip install --no-deps -e .
python -m pip check
python -m pytest tests/unit -q
python -m black --check --workers 1 app tests migrations
```

Puis reconstruire les images et tester Linux avec PostgreSQL isolé :

```sh
docker compose build api
docker compose -p ingestion-tests -f compose.test.yaml build tests
docker compose -p ingestion-tests -f compose.test.yaml run --rm tests python -m pip check
docker compose -p ingestion-tests -f compose.test.yaml run --rm tests python -m pytest -q --tb=short
docker compose -p ingestion-tests -f compose.test.yaml down -v
```

La dernière commande supprime exclusivement les données de test. Les quatre fichiers locaux de référence sont nécessaires pour valider tout le scénario métier ; un test ignoré faute de source ne constitue pas une réussite. La construction seule ne redéploie pas l’API en cours d’exécution.

Après validation, committer ensemble les modifications du `pyproject.toml` et des `.lock` concernées, en précisant le motif et les contrôles réellement exécutés. Si la validation échoue, corriger la contrainte et régénérer, ou revenir à l’ensemble précédent ; ne pas livrer un fichier de développement désaligné. Aucun schéma SQL n’évolue du seul fait de la génération des `.lock`.

Références : [génération, contraintes et mises à jour uv](https://docs.astral.sh/uv/pip/compile/), [options de résolution](https://docs.astral.sh/uv/reference/cli/#uv-pip-compile).

## Décisions de conception

```mermaid
flowchart LR
    Contrat["Modèles Pydantic et métadonnées"] --> Mapping["Mapping commun"]
    CSV["Lecteur CSV"] --> Mapping
    XLSX["Lecteur XLSX"] --> Mapping
    Mapping --> Metier["Fonctions de réconciliation"]
    Metier --> SQL["Modèles et contraintes PostgreSQL"]
    SQL --> Atomicite["Données et historique atomiques"]
```

Un module par responsabilité utile suffit au monolithe. Le lecteur ne connaît pas SQL ; les métadonnées de champs évitent de recopier les listes de contacts dans plusieurs couches. Les modèles canoniques et ORM restent distincts : règles d’import et stockage ne sont pas le même contrat.

La personne est recherchée puis comparée explicitement pour maîtriser non-effacement et historique. Un upsert opaque risquerait de masquer ces décisions. Pour le poste, l’upsert limité à « insérer si absent » correspond exactement au besoin. Le verrou PostgreSQL protège la réconciliation entre instances ; [ses limites de débit](exploitation.md#passage-à-léchelle) sont assumées.

L’atomicité par fichier évite les créations ou historiques partiels. Les traitements restent synchrones et bornés ; l’ajout d’une queue, d’un worker ou de commits par blocs demanderait de redéfinir reprise et idempotence, pas seulement de déplacer une fonction.

## Fonctionnalités possibles

Ce tableau décrit des **évolutions**, pas des fonctionnalités déjà livrées.

| Priorité / déclencheur | Évolution | Bénéfice et travail nécessaire |
|---|---|---|
| Avant exposition publique | Authentification et droits, TLS à l’entrée, quotas par client | Protéger les imports ; passerelle possible aujourd’hui, intégration applicative à concevoir |
| Avant exploitation durable | Métriques, alertes, sauvegardes et restauration testée, CI Windows/Linux | Mesurer délais/erreurs/verrous, sécuriser les livraisons et la reprise |
| Besoin de révocation immédiate | Annulation d’import et progression observables | Protocole d’annulation et contrôle du travail réellement interrompu |
| Volumes ou durées incompatibles avec HTTP synchrone | Jobs asynchrones avec file bornée et workers | Reprise et isolement ; nouveau contrat HTTP/statuts et politique de rétention nécessaires |
| Verrou global réellement limitant | Partitionnement des imports ou verrous par identité | Écritures concurrentes ; ordre de verrouillage, conflits d’organisation et deadlocks à traiter |
| Mesures SQL montrant trop d’allers-retours | Opérations par lots ou staging | Réduire les échanges tout en conservant compteurs, snapshots et rollback global |
| Identités plus fiables disponibles | Identifiants externes et référentiel d’organisations | Réduire les limites des homonymes ; règles de fusion à valider métier |
| Précision annuelle importante métier | Stocker année et précision séparément | Éviter le jour technique du 1er janvier ; migration et contrat à faire évoluer |
| Demande métier nouvelle | Nouveaux champs, effacement explicite ou audit des postes | Distinguer absence et demande d’effacement ; étendre schéma, historique et tests |

```mermaid
flowchart LR
    V1["V1 synchrone et atomique"] --> Mesures["Observer et mesurer"]
    Mesures --> Securite["Renforcer exploitation et sécurité"]
    Mesures --> Besoin{"Quel goulot mesuré ?"}
    Besoin -->|Calcul ou mémoire| Vertical["Dimensionnement vertical"]
    Besoin -->|Disponibilité ou réception| Horizontal["Plusieurs instances"]
    Besoin -->|Écritures SQL| Concurrence["Revoir le verrou métier"]
    Besoin -->|Durée HTTP| Jobs["Étudier les jobs asynchrones"]
```

## Sources primaires

[FastAPI — fichiers](https://fastapi.tiangolo.com/tutorial/request-files/), [Pydantic — champs](https://docs.pydantic.dev/latest/concepts/fields/), [Python — csv](https://docs.python.org/3.13/library/csv.html), [openpyxl — lecture optimisée](https://openpyxl.readthedocs.io/en/stable/optimized.html), [charset-normalizer](https://charset-normalizer.readthedocs.io/en/latest/user/advanced_search.html), [SQLAlchemy — transactions](https://docs.sqlalchemy.org/en/20/orm/session_transaction.html), [PostgreSQL — contraintes](https://www.postgresql.org/docs/17/ddl-constraints.html), [Alembic](https://alembic.sqlalchemy.org/en/latest/tutorial.html), [Docker Compose](https://docs.docker.com/reference/compose-file/services/).

Ces documentations expliquent les mécanismes ; l’arborescence et les arbitrages ci-dessus sont propres au projet.
