# API d’import CSV/XLSX

Une seule route métier, **`POST /upload`**, importe personnes, organisations et postes dans PostgreSQL. Elle crée les personnes absentes, met à jour les coordonnées renseignées des personnes existantes et historise les changements réels. Un fichier est **atomique** : toute erreur annule ses écritures, historique compris. Les réponses HTTP sont en anglais.

[Documentation](docs/README.md) · [Fonctionnement](docs/fonctionnement.md) · [Règles de données](docs/donnees.md) · [Exploitation](docs/exploitation.md)

## Installer et démarrer avec Docker

Le même dépôt et les mêmes images Linux s’utilisent sur **Windows et Linux**. Avec Docker, Python et PostgreSQL sont installés dans les conteneurs : aucune installation native de ces deux composants n’est nécessaire.

Les commandes suivantes partent du dossier contenant `compose.yaml`. Remplacer `VOTRE_COMPTE` et, si nécessaire, le nom du dépôt par ceux du dépôt GitHub publié.

### Windows — PowerShell

Installer Git et [Docker Desktop pour Windows](https://docs.docker.com/desktop/setup/install/windows-install/). Configurer le backend WSL 2 selon ce guide, utiliser les **conteneurs Linux**, puis démarrer Docker Desktop. Ouvrir un nouveau terminal après installation si `git` ou `docker` n’est pas reconnu.

```powershell
git clone "https://github.com/VOTRE_COMPTE/api_ingestion_fichier_bdd.git"
cd api_ingestion_fichier_bdd
docker version
docker compose version
docker compose up --build -d --wait
docker compose ps
```

### Linux — Bash

Installer Git, [Docker Engine pour votre distribution](https://docs.docker.com/engine/install/) et le [plugin Docker Compose](https://docs.docker.com/compose/install/linux/), puis démarrer le moteur Docker. Les commandes ci-dessous supposent que l’utilisateur peut accéder à Docker ; sinon, préfixer les commandes `docker` par `sudo` ou configurer cet accès selon le [guide officiel](https://docs.docker.com/engine/install/linux-postinstall/).

```bash
git clone "https://github.com/VOTRE_COMPTE/api_ingestion_fichier_bdd.git"
cd api_ingestion_fichier_bdd
docker version
docker compose version
docker compose up --build -d --wait
docker compose ps
```

### Accès, configuration et arrêt — les deux systèmes

Après le démarrage, les services doivent être `healthy`. Swagger : http://localhost:8000/docs ; contrôle de santé : http://localhost:8000/health. PostgreSQL est créé localement, puis Alembic applique les migrations avant Uvicorn. `/health` et les pages OpenAPI sont techniques ; aucun endpoint de profils, d’analyse ou de consultation métier n’est nécessaire.

Les valeurs de développement de `compose.yaml` suffisent **sans `.env`**. Pour personnaliser la configuration, copier [`.env.example`](.env.example) :

| PowerShell | Bash |
|---|---|
| `Copy-Item .env.example .env` | `cp .env.example .env` |

Adapter `POSTGRES_*` **et** `DATABASE_URL` si les identifiants changent. Entre conteneurs, l’hôte SQL est `db` ; depuis Python exécuté directement sur Windows ou Linux, c’est `127.0.0.1`. Les ports 8000/5432 sont liés à localhost ; changer `API_PORT`/`POSTGRES_PORT` s’ils sont déjà occupés et adapter les URL utilisées. Les secrets fournis sont destinés au développement. Modifier `POSTGRES_*` ne reconfigure pas une base dont le volume a déjà été initialisé.

Commandes communes à PowerShell et Bash :

```sh
# Consulter les logs, puis Ctrl+C pour quitter leur affichage
docker compose logs -f api
# Arrêter les services en conservant la base
docker compose down
# Les relancer
docker compose up -d --wait
```

Le volume `postgres_data` conserve les données lors d’un redémarrage, d’une reconstruction ou de la fermeture de VS Code. Ne pas utiliser `down -v` sur l’environnement normal pour redémarrer. Le plafond API par défaut est de 2048 Mio et 1 CPU ; PostgreSQL et l’hôte ont besoin de ressources supplémentaires.

## Développement Python local — facultatif

Cette variante exécute l’API sur l’hôte et garde PostgreSQL dans Docker. Installer **Python 3.13** sur le système concerné, avec le module `venv` (les paquets nécessaires dépendent de la distribution Linux). Chaque système doit créer son propre `.venv` ; ne pas recopier un environnement Windows vers Linux.

Si l’API Docker tourne déjà, l’arrêter pour libérer le port 8000. Les exemples ci-dessous utilisent les identifiants et le port SQL de développement par défaut ; les adapter si `.env` a été modifié.

### Windows — PowerShell

```powershell
docker compose stop api
docker compose up -d --wait db
py -3.13 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements-dev.lock
python -m pip install --no-deps -e .
$env:DATABASE_URL = "postgresql+psycopg://ingestion_user:ingestion_dev_password@127.0.0.1:5432/ingestion_db"
python -m alembic upgrade head
python -m uvicorn app.main:app --reload
```

Si PowerShell bloque l’activation, utiliser directement `.\.venv\Scripts\python.exe` à la place de `python` dans les commandes suivantes ; l’activation n’est pas indispensable.

### Linux — Bash

```bash
docker compose stop api
docker compose up -d --wait db
python3.13 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements-dev.lock
python -m pip install --no-deps -e .
export DATABASE_URL="postgresql+psycopg://ingestion_user:ingestion_dev_password@127.0.0.1:5432/ingestion_db"
python -m alembic upgrade head
python -m uvicorn app.main:app --reload
```

L’API locale est également accessible sur http://localhost:8000/docs ; Ctrl+C l’arrête. Pour revenir au lancement entièrement Docker dans le même terminal, retirer la surcharge de connexion avant de relancer `docker compose up -d --wait` : `Remove-Item Env:DATABASE_URL` sous PowerShell ou `unset DATABASE_URL` sous Bash. Compose retrouve alors son hôte SQL `db`. L’environnement virtuel peut être quitté avec `deactivate` sur les deux systèmes.

## Appeler `/upload`

Dans Swagger, choisir `file` et laisser `options` vide pour commencer. Aucun identifiant `format` ni profil à créer.

### Premier essai après clonage

Les fichiers réels de `data/` ne sont **pas versionnés dans GitHub**. Créer un petit CSV synthétique pour tester immédiatement l’installation.

Sous Windows, dans PowerShell :

```powershell
@("first_name;last_name;email", "Alice;Exemple;alice@example.org") | Set-Content -Encoding utf8 contacts.csv
curl.exe -F "file=@contacts.csv" http://localhost:8000/upload
```

Sous Linux, dans Bash :

```bash
printf '%s\n' 'first_name;last_name;email' 'Alice;Exemple;alice@example.org' > contacts.csv
curl -F "file=@contacts.csv" http://localhost:8000/upload
```

`curl.exe` évite l’alias PowerShell `curl` ; sous Linux, utiliser `curl` (à installer si nécessaire). Répéter l’appel doit renvoyer zéro personne/poste créé et zéro personne mise à jour.

### Fichiers de référence disponibles localement

Pour reproduire le scénario complet, créer un dossier `data/` dans le dépôt et y placer les quatre originaux sans les modifier. Ils sont directement reconnus par leurs **colonnes**, indépendamment du nom du fichier ou de la feuille :

```powershell
curl.exe -F "file=@data/data_1.csv" http://localhost:8000/upload
curl.exe -F "file=@data/data_2.xlsx" http://localhost:8000/upload
curl.exe -F "file=@data/data_3.xlsx" http://localhost:8000/upload
curl.exe -F "file=@data/data_4.xlsx" http://localhost:8000/upload
```

Sous Linux, les mêmes appels utilisent `curl` à la place de `curl.exe`. Les exemples et les tests de référence ont besoin de ces fichiers locaux ; ils ne sont pas nécessaires au démarrage de l’API.

Une structure nouvelle comme `first_name;last_name;email` ou `Prénom;Nom;courriel` fonctionne aussi, sans organisation ni date obligatoire. Les colonnes sont rapprochées par nom normalisé et alias ; l’ordre ne compte pas. Les valeurs textuelles sont nettoyées sans changer casse ou accents.

Pour d’autres noms, fournir un objet JSON dans le champ multipart `options`. L’exemple suivant vise un fichier `fournisseur.csv` dont la ligne 2 contient `Given;Family;Comment`. Exemple de fichier local `options.json` :

```json
{
  "mapping": {
    "person.first_name": "Given",
    "person.last_name": "Family"
  },
  "ignored_columns": ["Comment"],
  "header_row": 2
}
```

```powershell
curl.exe -F "file=@fournisseur.csv" -F "options=<options.json" http://localhost:8000/upload
```

Sous Linux :

```bash
curl -F "file=@fournisseur.csv" -F "options=<options.json" http://localhost:8000/upload
```

Options supplémentaires : `encoding`, `delimiter` pour CSV, `sheets: ["Contacts"]` pour XLSX, et `year_policy: "january_first"` pour accepter une année seule. Plusieurs feuilles sont sélectionnées explicitement ; leurs lignes sont concaténées sans jointure. `header_row: 0` permet un fichier sans en-tête avec sources `@c1`, `@c2`, etc. Les en-têtes dupliqués doivent être renommés.

Les colonnes additionnelles sans cible sont ignorées **avec avertissement**. `name`, `title`, `date` et `uri` nécessitent un mapping ou une ignorance explicite. Une correspondance ambiguë n’est jamais choisie arbitrairement.

`dataURI` et `raw_uri` sont des alias de `organization.org_vivo_uri` et sont conservés. Une URI VIVO peut décrire une catégorie commune à plusieurs organisations : elle n’est donc pas unique à elle seule. Le rapprochement utilise le couple nom exact + URI ; sans URI, le nom doit correspondre à une seule organisation. Voir les [règles d’organisation](docs/donnees.md#4-rapprocher-mettre-à-jour-et-historiser).

## Comprendre les traitements

```mermaid
flowchart LR
    Fichier["CSV ou XLSX"] --> Mapping["Colonnes vers schéma cible"]
    Mapping --> Validation["Validation des données"]
    Validation --> SQL["Personnes, postes et historique"]
    SQL --> Commit["Transaction atomique et bilan"]
```

Le [guide de fonctionnement](docs/fonctionnement.md) présente le parcours et les relations SQL. Le [guide des données](docs/donnees.md) détaille les règles de lecture, de normalisation, de dates, d’identité, de mise à jour et d’historisation ; les champs et leurs politiques se déclarent dans `app/schema.py`.

## Résultats et erreurs

```json
{
  "success": true,
  "message": "File imported successfully",
  "import_id": "00000000-0000-0000-0000-000000000001",
  "rows_processed": 20,
  "persons_created": 12,
  "persons_updated": 8,
  "persons_unchanged": 0,
  "jobs_created": 5,
  "warnings": []
}
```

Les compteurs de personnes portent sur des personnes distinctes du fichier ; une personne créée puis complétée dans ce même fichier reste une création. `jobs_created` compte les insertions SQL réellement effectuées, et `rows_processed` les lignes non vides. Un nouveau poste peut être ajouté pour une personne inchangée. Le résultat n’est renvoyé qu’après commit.

```json
{
  "success": false,
  "message": "File validation failed",
  "errors": [{"code": "invalid_value", "row": 12, "column": "email", "sheet": null, "message": "Invalid email address"}],
  "errors_truncated": false
}
```

422 : données/options à corriger ; 413 : limite dépassée ; 415 : contenu non pris en charge ; 409 : ambiguïté SQL/contrainte/verrou ; 429 : trop d’uploads, avec `Retry-After` ; 503 : SQL indisponible/délai ; 500 : échec interne. Aucun contenu de cellule rejetée ni stack trace dans la réponse. Les diagnostics sont bornés ; `errors_truncated` signale que d’autres erreurs peuvent exister. Aucun lot ou historique d’un fichier échoué n’est conservé.

## Alembic et historique

**Alembic versionne le schéma** : tables, colonnes, index, contraintes. **`person_history` versionne les valeurs métier** : anciennes coordonnées et changements lors des imports. Appliquer une migration n’importe pas de fichier et ne crée pas un historique métier.

Commandes communes à PowerShell et Bash, avec le venv activé pour les commandes Python locales :

```sh
# État et application des migrations dans le conteneur
docker compose exec api alembic current
docker compose exec api alembic upgrade head
# Nouvelle migration sur l’hôte, DATABASE_URL visant une base de développement
python -m alembic revision --autogenerate -m "add person mobile"
python -m alembic upgrade head
```

La V1 commence avec `0001_v1`, qui crée les cinq tables actives sur une base vide, puis `0002_shared_vivo_uri`, qui autorise les URI partagées et impose l’unicité du couple nom + URI sans supprimer de données. Relire et tester chaque nouvelle migration avant application ; un alias ou une validation purement applicative n’en nécessite pas. Une base issue d’une autre chaîne de migrations ne doit pas être simplement estampillée V1 : consulter les [procédures d’exploitation](docs/exploitation.md#schéma-initial-et-migrations).

## Faire évoluer la solution

Les [règles de données et l’ajout d’un champ](docs/donnees.md#faire-évoluer-un-champ) expliquent comment adapter le contrat central et les validations. Les [choix techniques et fonctionnalités possibles](docs/choix_et_evolutions.md) distinguent les mécanismes actuels des évolutions à étudier. La [mise en production, la configuration et l’échelle verticale/horizontale](docs/exploitation.md) sont regroupées dans un seul guide.

## Tests

Les tests vérifient la migration initiale, la cohérence ORM/SQL, les variantes CSV/XLSX, l’atomicité, les réimports et les changements historisés. Le scénario des quatre originaux compare aussi coordonnées, relations et anciennes/nouvelles valeurs.

Commandes communes à PowerShell et Bash ; activer le venv pour les trois premières :

```sh
python -m pytest tests/unit -q
python -m black --check --workers 1 app tests migrations
python -m pip check

docker compose -p ingestion-tests -f compose.test.yaml build tests
docker compose -p ingestion-tests -f compose.test.yaml run --rm tests python -m pytest -q --tb=short
# Nettoyer exclusivement les ressources de test
docker compose -p ingestion-tests -f compose.test.yaml down -v
```

L’intégration utilise PostgreSQL réel, exclusivement la base/utilisateur `ingestion_test` et un schéma aléatoire. Les quatre originaux sont montés en lecture seule, leurs empreintes vérifiées. Sans URL de test ou sources locales, les tests concernés sont explicitement ignorés. La suite complète validée sur PostgreSQL contient 133 tests, dont la conservation des URI VIVO, les relations entre organisations/postes et la migration depuis une V1 contenant des données ; aucun test ignoré ne vaut validation d’une source absente.

L’API reste locale sans authentification/HTTPS embarqué. Les lots et historiques sont consultables en SQL ; exemple : `docker compose exec db psql -U ingestion_user -d ingestion_db`. Le volume n’est pas une sauvegarde. Une exposition réseau ou une montée en charge doit dimensionner aussi PostgreSQL et l’hôte.
