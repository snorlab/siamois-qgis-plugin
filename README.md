# Plugin QGIS SIAMOIS (v1, expérimental)

Charge un projet SIAMOIS dans un **GeoPackage local au format du modèle de données spatiales SIAMOIS**
(Quentin Verriez : `projet`, `ut_*`, `uo_*`, `ur_poly`, `ud_*`, `metadonnees`, `concept`, `personne`, `raster`, `lien_*`),
permet l'édition classique QGIS ou via la fiche, puis **synchronise** vers l'API. Voir
[`docs/CONCEPTION_CHARGEMENT_COUCHES.md`](docs/CONCEPTION_CHARGEMENT_COUCHES.md).

## Installer
- Dev / mise à jour : `./install_dev.sh` (remplace le dossier installé, SDK à jour inclus dans `vendor/`). **Ne copiez pas `siamois_qgis/` à la main par-dessus l'ancien** : un ancien `vendor/` resterait.
- Zip : `./build.sh` → `dist/siamois_qgis.zip` (SDK embarqué) → *Extensions ▸ Installer depuis un ZIP*.

## Utiliser
Menu **SIAMOIS** : *Ouvrir un projet…* (connexion, organisation, projet) ▸ éditer ▸ *Ouvrir la fiche* ▸ (*Dessiner la géométrie de l'élément sélectionné* pour un élément sans géométrie) ▸
*Vérifier mes modifications* ▸ *Synchroniser avec SIAMOIS*.

**Créer un élément** : ajouter une ligne dans la couche UE (type obligatoire, géométrie optionnelle) ou mobilier (type + UE),
enregistrer la couche, puis synchroniser : l'identifiant est attribué par le serveur. Créer l'UE avant ses mobiliers.

## Modèle spatial
Chaque UE est rangée dans une table du modèle ; le mobilier va dans `uo_point`. Sans évolution du serveur, la table est
choisie ainsi : (1) le type d'UE → table saisi dans *SIAMOIS ▸ Répartition dans le modèle spatial…* ; (2) sinon la
**géométrie réelle** (surface → `uo_poly`, ligne → `ut_ligne`, point → `ut_point`) ; (3) sinon la **géométrie par défaut de la
table SIAMOIS** (UE : polygone, réglable ; mobilier : point ; projet : polygone). Changer la répartition recharge le projet.
Les colonnes `siamois_id` / `siamois_revision` portent la synchronisation ; `numXXX` et `typXXX` recopient l'identifiant
complet et le type. Détails et demandes d'évolution serveur : [`docs/DEMANDE_EVOLUTION_SIAMOIS.md`](docs/DEMANDE_EVOLUTION_SIAMOIS.md).

Écart avec le fichier préformaté de Quentin (contraintes, vues, styles) et piste « fichier modèle » :
[`docs/ANALYSE_ECART_GEOPACKAGE_QUENTIN.md`](docs/ANALYSE_ECART_GEOPACKAGE_QUENTIN.md).

## Prérequis serveur
Le plugin envoie les géométries telles quelles (UE : surface, ligne ou point ; mobilier : point ; projet : surface). Le serveur doit
embarquer la branche `feat/geometry-generic-geom` (sinon : 400 « MultiPolygon attendu »). SDK requis : ≥ 0.4.0.

## Tests (sans QGIS)
`python -m unittest discover -s tests -t .` — couvre chargement, détection des changements, envoi, conflits, fichier annexe.
Le code PyQGIS (`core/qgis_layers.py`, `ui/`, `plugin.py`) est compilé mais **n'a pas encore été exécuté dans QGIS**.
