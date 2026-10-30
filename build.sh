#!/usr/bin/env sh
# Produit dist/siamois_qgis.zip (installable via « Installer depuis un ZIP ») avec le SDK embarqué dans vendor/.
set -e
cd "$(dirname "$0")"
rm -rf dist build_tmp && mkdir -p dist build_tmp
cp -r siamois_qgis build_tmp/siamois_qgis
mkdir -p build_tmp/siamois_qgis/vendor
SDK="${SIAMOIS_SDK:-../siamois-python-sdk}"   # dépôt MOM-CNRS/siamois-python-sdk cloné à côté
[ -d "$SDK/siamois_sdk" ] || { echo "SDK introuvable : $SDK (définir SIAMOIS_SDK=/chemin/siamois-python-sdk)" >&2; exit 1; }
cp -r "$SDK/siamois_sdk" build_tmp/siamois_qgis/vendor/siamois_sdk
find build_tmp -name __pycache__ -prune -exec rm -rf {} +
(cd build_tmp && python3 -m zipfile -c ../dist/siamois_qgis.zip siamois_qgis)
rm -rf build_tmp
echo "dist/siamois_qgis.zip"
