# Écart entre le GeoPackage du plugin et le modèle de Quentin Verriez

*Analyse de `geopackage_siamois_v0_1.gpkg` (fichier préformaté fourni par Quentin) comparé au GeoPackage produit par le plugin
(v0.2, `core/qgis_layers.py`). Voir aussi [`DEMANDE_EVOLUTION_SIAMOIS.md`](DEMANDE_EVOLUTION_SIAMOIS.md).*

## 1. Contenu du fichier de Quentin
- **20 tables** du modèle : `projet`, `metadonnees`, `concept`, `personne`, `raster`, 8 tables d'entités (`ut_poly`, `ut_ligne`,
  `ut_point`, `uo_poly`, `uo_point`, `ur_poly`, `ud_poly`, `ud_ligne`) et 7 tables de liaison `lien_*`.
- **3 vues de contrôle** : `v_controle_referentiels`, `v_controle_regles` (règles du §11 du modèle), `v_uri_manquantes`.
- **`layer_styles`** (20 styles QGIS) et **`qgis_projects`** (un projet QGIS embarqué).
- Géométries figées : `MULTIPOLYGON` (projet, raster, `ut_poly`, `uo_poly`, `ur_poly`, `ud_poly`), `MULTILINESTRING`
  (`ut_ligne`, `ud_ligne`), `POINT` (`ut_point`, `uo_point`), SRID 2154.
- Le fichier contient des données d'exemple (3 projets, 24 `uo_poly`, 166 lignes de `concept`…) : à vider pour un modèle.

## 2. Ce qui est identique
Les **colonnes** : pour les 20 tables, mêmes noms, aucune colonne manquante ni en trop (hors colonnes `siamois_*`, ajoutées
volontairement). Seul l'**ordre** diffère pour 6 tables (`ut_*`, `uo_*`, `ur_poly`) : chez Quentin `geom` précède `metacquis`.

## 3. Ce qui manque ou diffère

| Sujet | Modèle de Quentin | Plugin v0.2 |
|---|---|---|
| Contraintes | PK `AUTOINCREMENT`, `NOT NULL`, défauts (`table_siamois`), clés étrangères (`idprojet`, `ON DELETE CASCADE` sur les liens), `UNIQUE` (`personne.nom`, `metadonnees.idprojet`, paires des liens), index `concept_unicite` | aucune (colonnes créées par l'écriture OGR) |
| Type de géométrie | figé (Multi*), SRID 2154 | déduit des données ; polygone simple si table vide ; SRID le plus fréquent |
| Vues de contrôle | 3 vues | absentes |
| Styles et projet QGIS | `layer_styles` (20), `qgis_projects` | absents |
| Listes de valeurs | `ValueRelation` sur `concept` (colonnes `typ…`, `interpret`, `metacquis`) | `ValueMap` propre à chaque couche |
| Table `concept` | types, `interpret`, `metacquis`, `table_siamois`, valeurs de `metadonnees` et `personne.typpersonne` | types et listes SIAMOIS uniquement |
| Projets | plusieurs par fichier (`idprojet_parent`) | un par fichier (`idprojet` = 1) |

## 4. Point bloquant : champs `NOT NULL`
Chez Quentin, `numXXX`, `typXXX`, `autsaisie`, `datesaisie` (et `ro`, `typprojet` pour `projet`, de nombreux `dc_*` pour
`metadonnees`) sont obligatoires. Une UE créée dans QGIS n'a pas d'identifiant avant la synchronisation : `numXXX` serait
vide et refusé. Il faut une **valeur provisoire** (par exemple `NOUVEAU-<n>`), remplacée par l'identifiant du serveur après
synchronisation, et des valeurs par défaut pour `ro`, `typprojet` et les métadonnées obligatoires.

## 5. Piste retenue : utiliser le fichier de Quentin comme modèle
Embarquer le fichier vidé de ses données dans le plugin, puis, à chaque ouverture de projet :
1. copier le modèle ;
2. ajouter nos colonnes `siamois_*` (`ALTER TABLE … ADD COLUMN`) et les tables techniques (champs, références hors ligne,
   journal) ;
3. insérer les lignes en ouvrant les tables du modèle (fournisseur OGR + `addFeatures`) pour respecter contraintes et index ;
4. écrire les géométries en Multi* (un polygone simple est enveloppé) ;
5. fusionner nos formulaires (onglets par panneau, champs obligatoires) avec leurs `ValueRelation` : garder les leurs pour
   `interpret` et `metacquis`.

Avantages : contraintes, vues de contrôle, styles et projet QGIS conservés ; les évolutions du modèle de Quentin se suivent
en remplaçant le fichier modèle. Les vues ne référencent que des colonnes nommées : ajouter des colonnes ne les casse pas.

## 6. Questions ouvertes
- Accord de Quentin pour embarquer son fichier (licence, versionnage `v0_1` → comment détecter une version plus récente) ?
- Valeur provisoire de `numXXX` et règle de remplacement après synchronisation.
- `typXXX` : colonne éditable chez Quentin (liste de `concept`) mais miroir en lecture seule chez nous (type SIAMOIS,
  non modifiable après création). À arbitrer.
- Plusieurs projets dans un même fichier (`idprojet_parent`) : hors v1 ?
- Remplir `concept` pour `interpret`, `metacquis` et `table_siamois` : à partir de quels champs SIAMOIS ?
