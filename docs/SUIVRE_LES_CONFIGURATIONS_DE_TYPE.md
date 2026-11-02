# Suivre vraiment les configurations de type SIAMOIS

> Document de cadrage. Il décrit l'écart entre ce que fait le plugin aujourd'hui et ce qu'il faudrait pour
> reproduire fidèlement les formulaires SIAMOIS : un formulaire par type, ses règles, la génération des
> identifiants et les sélections d'entités. Rien de ce qui suit n'est implémenté, sauf là où c'est dit.
> Les faits côté serveur ont été relevés sur la branche `main` de SIAMOIS (novembre 2026) ; les points non
> vérifiés sont signalés **[à vérifier]**.

## 1. État actuel

| Sujet | Aujourd'hui |
|---|---|
| Formulaires | Un formulaire par type est lu (`/recording-unit-types`, `/find-types`), puis **fusionné** : une couche = l'union des champs de tous les types (`merge_schemas`). Si un champ existe dans plusieurs types, la définition du **premier** formulaire rencontré (défaut, puis types) est conservée. |
| Champs hors type | Tous les champs sont visibles pour toutes les lignes. À la synchronisation, un champ non prévu pour le type de la ligne est simplement ignoré (`allowed` par ligne et `type_allowed` par couche). |
| Règles de formulaire | Ignorées : `enabledWhen`, `requiredWhen`, `options`, `constraints` conditionnelles. Seuls `required` et `readOnly` du layout sont repris, tels quels. |
| Vocabulaires | `GET /organizations/{id}/concepts?fieldId=&projectId=` (SDK ≥ 0.5.0), **sans** `valueConceptId` : la liste ne tient pas compte du type, ni des listes dépendantes. |
| Identifiants | Jamais calculés localement. Une UE créée dans QGIS a un identifiant provisoire vide ; le serveur le génère à la synchronisation. |
| Sélection d'entités | Seule la liste des UE (pour le mobilier) est une liste déroulante. Personnes, unités spatiales, unités d'action, phases, conteneurs, spécimens : colonnes en lecture seule ou texte. |
| Choix multiples | Texte, libellés séparés par `;`. |

## 2. Cible

Pour une ligne donnée, la fiche QGIS se comporte comme la fiche SIAMOIS de son type :

1. seuls les champs **du type** sont affichés (les autres sont masqués), dans les mêmes panneaux et le même ordre ;
2. chaque champ a **ses propres** libellé, obligation, bornes, unité, lecture seule, liste de valeurs **pour ce type** ;
3. les règles conditionnelles s'appliquent en direct (champ grisé, rendu obligatoire, liste filtrée) ;
4. un nouvel élément reçoit **l'identifiant que proposerait SIAMOIS**, modifiable avant envoi ;
5. les champs « référence » (personne, UE, unité spatiale…) sont de vraies sélections, pas du texte ;
6. les choix multiples sont des cases à cocher ou une liste à sélection multiple.

## 3. Ce que le serveur fournit déjà

| Besoin | Source côté serveur |
|---|---|
| Formulaire par type | `GET /projects/{id}/recording-unit-types` et `/find-types` : pour chaque type, `formBundle` (layout) et `fields` (définitions). |
| Définition d'un champ | `FieldResource` : `answerType`, `label`, `hint`, `valueBinding`, `constraints` (min, max, unité, heure), `readOnly`, `isTextArea`, **`rules`** (`enabledWhen`, `requiredWhen`, `options`, `constraints`), **`vocabulary`**. |
| Origine d'une liste | `fields[<fieldId>].vocabulary` dans le `fields` **imbriqué de chaque type** (jamais dans le catalogue racine, qui est l'union des types) : source (branche, collection), mode (suivre ou figé), état des concepts. |
| Valeurs d'une liste | `GET /organizations/{id}/concepts?fieldId=&projectId=&valueConceptId=` ; `valueConceptId` = type de l'entité ; `relatedToConceptId` pour une liste dépendante (`options: RELATED_CONCEPTS`) ; `relatedToType=true` pour `options: RELATED_TO_TYPE`. |
| Identifiant suggéré | `GET /identifier-suggestions?entityType=&projectId=&typeId=` (+ `recordingUnitId` pour un mobilier, `parentRecordingUnitId` pour une UE enfant, dont les jetons `NUM_PARENT` / `ID_PARENT` sont évalués). Ne consomme pas le compteur. Réponse : `suggestedIdentifier`, `null` si le projet ne configure pas de format. L'identifiant choisi se renvoie à la création dans `identifier`. |
| Valeurs d'un multivalué | `GET /{collection}/{id}/fields/{fieldId}/values` (paginé). |
| Recherche | `GET /search?organizationId=&q=&types=` (projets, UE, mobiliers, lieux). **[à vérifier]** : pas d'endpoint de recherche dédié aux personnes ni aux unités d'action relevé. |

## 4. Travaux à mener

### 4.1 Un formulaire par type (SDK + modèle de données)

Le SDK doit cesser de fusionner et conserver, **par type**, la liste de colonnes :

- `ColumnSpec` par `(type, champ)` : libellé, obligation, bornes, unité, lecture seule, panneau, ordre ;
- `FieldDef` : lire `rules` et `vocabulary` (aujourd'hui ignorés) ;
- le **nom de colonne** reste celui de la fusion (une colonne physique par champ) ; une table
  `type → [colonnes visibles + options]` est stockée dans la base d'état (`store.py`), à côté de `type_allowed`.

Conflits à trancher : un même champ avec des libellés différents selon le type. Garder un nom de colonne unique
(premier libellé, suffixé de l'id si collision, comme aujourd'hui) et **afficher le libellé du type** dans la fiche.

### 4.2 Fiche adaptée au type (plugin)

QGIS n'a pas de formulaire par ligne ; trois mécanismes se combinent :

1. **Visibilité conditionnelle des onglets et groupes** : chaque conteneur du formulaire à glisser-déposer accepte une
   expression de visibilité. Un champ propre à certains types est placé dans un groupe `visible si "Type" IN (…)`.
   C'est le mécanisme principal, sans code Python.
2. **Fonction d'initialisation du formulaire** (`QgsEditFormConfig.setInitCodeSource`) : Python exécuté à l'ouverture,
   et à chaque changement de « Type » (signal `valueChanged`), pour régler obligation, bornes, listes et lecture seule
   selon le type. Nécessaire dès que les propriétés d'un champ **diffèrent** entre types.
3. **Expressions de contrainte** (douces ou fortes) sur les champs : `"Champ" IS NOT NULL` si `"Type" = …` pour les
   obligations par type.

Limites connues : le formulaire de table d'attributs en mode « tableau » ne suit pas ces règles ; seule la fiche
(formulaire) les applique. Un changement de type après création reste interdit (liaison immuable côté serveur).

Alternative écartée par défaut : **une couche QGIS par type**. Elle donnerait des formulaires exacts sans code, mais
elle multiplie les couches, rompt avec les tables du modèle spatial de Quentin (une table ≠ un type) et complique la
synchronisation. À reconsidérer seulement si l'option précédente s'avère trop fragile.

### 4.3 Règles conditionnelles (`rules`)

À traduire une fois le langage lu **[à vérifier : forme exacte de `rules` et de `options`]** :

| Règle serveur | Traduction QGIS |
|---|---|
| `enabledWhen` | champ lecture seule ou masqué : expression sur les autres champs du formulaire |
| `requiredWhen` | contrainte d'expression conditionnelle |
| `constraints` | bornes dynamiques : `setFieldConstraint` ou code d'initialisation |
| `options: RELATED_CONCEPTS` | liste filtrée par la valeur d'un autre champ (liste dépendante) |
| `options: RELATED_TO_TYPE` | liste filtrée par le type de l'entité |

Les mêmes règles doivent être **revérifiées avant envoi** (`syncplan`) : la fiche aide, la synchronisation reste le
point d'application, avec le message d'erreur par ligne déjà en place.

### 4.4 Listes de valeurs par type et listes dépendantes

- Charger les concepts **par `(champ, type)`** avec `valueConceptId` ; stocker `vocab[(champ, type)]`.
  Volume : nombre de champs à liste × nombre de types, un appel paginé chacun (à paralléliser, à mettre en cache).
- Exposer la liste dans QGIS via une **table auxiliaire** `concept` (déjà présente dans le GeoPackage de Quentin, avec
  `siamois_code_vocabulaire`) et un widget **Relation de valeur** filtré par expression
  (`"siamois_code_vocabulaire" = 'field:21:type:10'`), plutôt qu'une liste figée « ValueMap » par couche. Avantages :
  une seule source, filtre par type, et compatibilité avec les `ValueRelation` du modèle de Quentin.
- Listes dépendantes : filtre par expression sur la valeur d'un autre champ, avec les liens issus de
  `relatedToConceptId` (un appel par valeur parente, ou chargement préalable du graphe de relations).
- Listes volumineuses ou « suivre le thésaurus » : prévoir un rafraîchissement manuel plutôt qu'un chargement complet.

### 4.5 Génération des identifiants

À la création d'un élément dans QGIS :

1. choix du type (déjà obligatoire à la création) ;
2. appel de `identifier-suggestions` (`entityType`, `projectId`/`recordingUnitId`, `typeId`, éventuellement
   `parentRecordingUnitId`) ;
3. valeur proposée dans « Identifiant » et dans le miroir `numXXX`, **modifiable** ; envoyée dans `identifier` à la création ;
4. si `suggestedIdentifier` est `null` : colonne vide, attribuée par le serveur.

Points durs :

- c'est un **appel réseau à chaque nouvelle ligne** : à déclencher au changement de type (fonction d'initialisation),
  avec repli hors ligne (valeur provisoire du type `NOUVEAU-n`, remplacée à la synchronisation) ;
- la suggestion ne réserve rien : deux personnes peuvent obtenir le même numéro. Le serveur reste maître au moment
  de la création ; prévoir le cas « identifiant déjà pris » (409 ou 400 **[à vérifier]**) avec re-proposition ;
- plusieurs nouvelles lignes dans une même session : le serveur ne sait pas qu'elles existent. Il faut soit rappeler le
  serveur après chaque création effective, soit **incrémenter localement** la dernière suggestion, soit laisser vide et
  tout attribuer à la synchronisation (comportement actuel, le plus sûr) ;
- cela résout aussi le cas `NOT NULL` des colonnes `numXXX` du modèle de Quentin (voir
  `ANALYSE_ECART_GEOPACKAGE_QUENTIN.md`).

### 4.6 Sélection d'entités dans les listes

Un champ référence passe de « texte non modifiable » à une vraie sélection. Selon la cible :

| `answerType` | Cible | Approche |
|---|---|---|
| `SELECT_ONE_RECORDING_UNIT`, `SELECT_MULTIPLE_RECORDING_UNIT` | UE du projet | **Relation de valeur** vers la couche des UE déjà chargée (clé `siamois_id`, libellé « Identifiant complet »). Déjà fait pour le mobilier ; à généraliser. |
| `SELECT_ONE_SPATIAL_UNIT`, `SELECT_MULTIPLE_SPATIAL_UNIT(_TREE)` | lieux | table auxiliaire locale des lieux du projet **[à vérifier : endpoint de liste]** ; l'arbre devient une liste indentée. |
| `SELECT_ONE_PERSON`, `SELECT_MULTIPLE_PERSON` | personnes | table `personne` du modèle de Quentin, remplie avec les membres du projet **[à vérifier : endpoint]**. |
| `SELECT_ONE_ACTION_UNIT` | unités d'action | le projet courant, et ses parents/enfants **[à vérifier]**. |
| `SELECT_MULTIPLE_PHASE`, `_CONTAINER`, `_SPECIMEN`, `_STRATIGRAPHY` | phases, conteneurs, mobilier, relations stratigraphiques | à charger comme des couches annexes ; la stratigraphie est une **relation** (antérieur/postérieur), pas une simple liste. |
| `SELECT_ONE_DOCUMENT`, `SELECT_ADDRESS` | documents, adresses | hors périmètre à ce stade. |

Principe : **tout ce qui est chargé localement devient une Relation de valeur**, sans appel réseau à la saisie. Pour les
ensembles trop grands pour être chargés (lieux de toute une organisation), recherche à la demande via
`/search` ou un endpoint dédié, dans une boîte de dialogue plutôt que dans une liste déroulante.

Pour les **multivalués**, la Relation de valeur de QGIS stocke une liste sous forme de tableau PostgreSQL ou de texte
JSON : le format actuel (libellés séparés par `;`) devra être remplacé par une liste d'identifiants, avec conversion en
libellés à l'affichage.

Les liens entre entités déjà dans le modèle de Quentin (`lien_*`, sept tables) sont l'endroit naturel pour les relations
UE–UE, UE–mobilier, etc. ; ils sont aujourd'hui vides.

### 4.7 Synchronisation

- Envoyer uniquement les champs **du type** (déjà fait via `allowed`) et appliquer les règles de §4.3 avant envoi.
- Création : `identifier` (§4.5), références par identifiant (§4.6), listes dépendantes cohérentes.
- Conflits : inchangé (révision et 409 pour les UE ; le mobilier n'a pas encore de révision côté serveur).

## 5. Ordre proposé

1. **SDK** : lire `rules` et `vocabulary`, conserver les formulaires par type (§4.1). Aucun changement visible.
2. **Visibilité par type** (§4.2, mécanisme 1) : masquer les champs hors type. Gain immédiat, sans code d'initialisation.
3. **Listes par type** via la table `concept` et la Relation de valeur (§4.4).
4. **Références** locales : UE, puis personnes et lieux (§4.6).
5. **Identifiants suggérés** (§4.5).
6. **Règles conditionnelles** (§4.3) et fonction d'initialisation du formulaire.
7. Multivalués en vraie sélection, relations stratigraphiques et tables `lien_*`.

Les étapes 1 à 3 donnent déjà des fiches très proches de SIAMOIS ; les étapes 5 à 7 sont les plus coûteuses.

## 6. Questions pour l'équipe SIAMOIS

1. Forme exacte et jeu complet des clés de `rules` et de `options` (grammaire des expressions de `enabledWhen` /
   `requiredWhen`) : y a-t-il une spécification partagée avec le formulaire web ?
2. Endpoint de **liste** (pas seulement de recherche) pour les personnes d'un projet, les lieux et les unités d'action.
3. Comportement exact quand l'identifiant fourni à la création existe déjà : code HTTP et corps de l'erreur.
4. Possibilité de **réserver** un identifiant (ou de demander plusieurs suggestions consécutives) pour la saisie hors
   ligne de plusieurs éléments.
5. Format d'une relation stratigraphique en lecture/écriture (`SELECT_MULTIPLE_STRATIGRAPHY`).
6. Les listes qui dépendent du type (`RELATED_TO_TYPE`) : peut-on obtenir **en une requête** toutes les listes d'un type
   plutôt qu'un appel par champ ?

## 7. Risques

- **Volume d'appels** au chargement (champs × types) : prévoir un chargement en parallèle et un cache par projet.
- **Fidélité des règles** : le langage de règles du serveur évolue ; tout ce qui est retraduit en expressions QGIS doit
  rester une aide, la validation serveur faisant foi.
- **Complexité du formulaire QGIS** : un code d'initialisation Python par couche est plus difficile à tester (aucune
  suite de tests ne tourne sous QGIS aujourd'hui) ; isoler la logique (types → colonnes visibles, règles → expressions)
  dans un module sans dépendance QGIS, testé comme `loader` et `syncplan`.
