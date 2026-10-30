"""Couches QGIS : écriture du GeoPackage, widgets natifs (listes déroulantes, contraintes), lecture des modifs.

Dépend de PyQGIS (QGIS >= 3.22). La logique métier reste dans loader/syncplan (testables sans QGIS).
"""

from __future__ import annotations

import json
import os
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from qgis.core import (
    NULL, QgsAttributeEditorContainer, QgsAttributeEditorField, QgsCoordinateReferenceSystem,
    QgsCoordinateTransformContext, QgsDefaultValue, QgsEditFormConfig, QgsEditorWidgetSetup, QgsFeature, QgsField,
    QgsFieldConstraints, QgsGeometry, QgsLayerTreeGroup, QgsProject, QgsVectorFileWriter, QgsVectorLayer, QgsWkbTypes,
)
from qgis.PyQt.QtCore import QDate, QVariant

from . import _sdk_import  # noqa: F401
from siamois_sdk.flatten import (
    CHOICE, DATETIME, DECIMAL, INTEGER, MEASUREMENT, ColumnSpec, Vocabularies,
)
from . import log
from .geo import geojson_to_wkt, with_srid
from . import spatial_model as sm
from .geo import dominant_srid
from .loader import (
    FULL_IDENTIFIER_COLUMN, KIND_PROJECT, LayerData, ProjectBundle, VOCAB_STATUS, concept_rows, status_vocabulary,
    type_column,
)

ID_FIELD = sm.SIAMOIS_ID
REV_FIELD = sm.SIAMOIS_REV
PROP_DB = "siamois/db"
PROP_KIND = "siamois/kind"
PROP_NAME = "siamois/layer"
PROP_TABLE = "siamois/table"
EMPTY_LABEL = "(vide)"
WKT_PRECISION = 6


def _qvariant(spec: ColumnSpec):
    if spec.kind == INTEGER:
        return QVariant.Int
    if spec.kind in (DECIMAL, MEASUREMENT):
        return QVariant.Double
    return QVariant.String


def norm_wkt(wkt: Optional[str]) -> Optional[str]:
    """WKT normalisé (même précision des deux côtés de la comparaison)."""
    if not wkt:
        return None
    g = QgsGeometry.fromWkt(wkt)
    return None if g is None or g.isNull() else g.asWkt(WKT_PRECISION)


def _clean(v: Any) -> Any:
    if v is None or v == NULL:
        return None
    return v


# ------------------------------------------------------------------ écriture


def _layer_geometry_type(geoms: List[QgsGeometry]) -> str:
    """Type de géométrie de la couche : « None » (sans géométrie), un type unique, sa version Multi, ou « Geometry »."""
    kinds = set()
    for g in geoms:
        t = g.wkbType()
        flat = QgsWkbTypes.flatType(t)
        kinds.add((QgsWkbTypes.singleType(flat), QgsWkbTypes.hasZ(t)))
    if not kinds:
        return "None"
    if len(kinds) == 1:
        (single, z) = next(iter(kinds))
        multi = any(QgsWkbTypes.isMultiType(g.wkbType()) for g in geoms)
        t = QgsWkbTypes.multiType(single) if multi else single
        if z:
            t = QgsWkbTypes.addZ(t)
        return QgsWkbTypes.displayString(t)
    return "Geometry"


_SM_QTYPE = {sm.INTEGER: QVariant.Int, sm.TEXT: QVariant.String, sm.DATE: QVariant.Date}
_FAMILY_URI = {sm.POLYGON: "Polygon", sm.LINE: "LineString", sm.POINT: "Point"}


def _qgis_value(sm_type: str, value: Any) -> Any:
    if value is None:
        return None
    if sm_type == sm.DATE and isinstance(value, str):
        d = QDate.fromString(value, "yyyy-MM-dd")
        return d if d.isValid() else None
    return value


def _geometry(geojson: Optional[Dict[str, Any]]) -> Optional[QgsGeometry]:
    wkt = geojson_to_wkt(geojson)
    g = QgsGeometry.fromWkt(wkt) if wkt else None
    return g if g is not None and not g.isNull() else None


def _table_layer(name: str, family: Optional[str], srid: int, fields: List[QgsField],
                 records: List[Tuple[List[Any], Optional[QgsGeometry]]]) -> QgsVectorLayer:
    """Couche mémoire d'une table : `family` = famille de géométrie attendue (None = attributaire).

    Le type exact (Polygon/MultiPolygon…) est déduit des géométries présentes ; table vide : type simple du modèle.
    """
    geoms = [g for _, g in records if g is not None]
    gtype = "None"
    if family is not None:
        gtype = _layer_geometry_type(geoms) if geoms else _FAMILY_URI[family]
    uri = "None" if gtype == "None" else f"{gtype}?crs=epsg:{srid}"
    mem = QgsVectorLayer(uri, name, "memory")
    pr = mem.dataProvider()
    pr.addAttributes(fields)
    mem.updateFields()
    feats = []
    for attrs, g in records:
        f = QgsFeature(mem.fields())
        f.setAttributes(attrs)
        if g is not None and gtype != "None":
            if QgsWkbTypes.isMultiType(mem.wkbType()) and not g.isMultipart():
                g.convertToMultiType()
            f.setGeometry(g)
        feats.append(f)
    pr.addFeatures(feats)
    return mem


def _typed_fields(columns: List[Tuple[str, str]]) -> List[QgsField]:
    return [QgsField(n, _SM_QTYPE[t]) for n, t in columns]


def _entity_layer(ld: LayerData, bundle: ProjectBundle) -> QgsVectorLayer:
    """Table du modèle spatial d'une couche SIAMOIS : colonnes de Quentin, colonnes de synchronisation, colonnes à plat."""
    table = sm.ENTITY_TABLES.get(ld.table)
    taken = {s.name for s in ld.specs}
    if table is not None:
        core = [(n, t) for n, t in sm.core_columns(table) if n not in taken]
        family = table.geom
    else:  # projet
        core = [(n, t) for n, t in sm.project_columns() if n not in taken]
        family = sm.POLYGON
    sync = [(sm.SIAMOIS_ID, sm.TEXT)] + ([(sm.SIAMOIS_REV, sm.INTEGER)] if ld.kind != KIND_PROJECT else [])
    fields = _typed_fields(core + sync) + [QgsField(s.name, _qvariant(s)) for s in ld.specs]
    type_col = type_column(ld.specs)
    records = []
    for r in ld.rows:
        full = r.cells.get(FULL_IDENTIFIER_COLUMN)
        label = r.cells.get(type_col) if type_col else None
        if table is not None:
            mirror = sm.core_values(table, ld.kind, full, label, bundle.author)
        else:
            mirror = {"numprojet": full, "table_siamois": sm.TABLE_SIAMOIS_LABEL[sm.KIND_PROJECT],
                      "autsaisie": bundle.author, "datesaisie": sm.today()}
        values = [_qgis_value(t, mirror.get(n)) for n, t in core]
        values += [r.id] + ([r.revision] if ld.kind != KIND_PROJECT else [])
        values += [_clean(r.cells.get(s.name)) for s in ld.specs]
        records.append((values, _geometry(r.geom)))
    return _table_layer(ld.name, family, ld.srid, fields, records)


def _plain_layer(name: str, family: Optional[str], srid: int, columns: List[Tuple[str, str]],
                 rows: Optional[List[Dict[str, Any]]] = None) -> QgsVectorLayer:
    rows = rows or []
    records = [([_qgis_value(t, row.get(n)) for n, t in columns], None) for row in rows]
    return _table_layer(name, family, srid, _typed_fields(columns), records)


def _auxiliary_tables(bundle: ProjectBundle, srid: int) -> List[Tuple[str, str, QgsVectorLayer]]:
    """Tables du modèle hors entités : (nom, colonne clé primaire, couche). Seules metadonnees et concept sont remplies."""
    day = sm.today()
    meta = {"idprojet": 1, "table_siamois": "Document", "dc_title": f"Données spatiales – {bundle.project_name}",
            "dc_type": "Dataset", "dc_format": "application/geopackage+sqlite3", "dc_lang": "français",
            "dc_date": day, "epsg": srid, "autsaisie": bundle.author, "datesaisie": day,
            "siamois_url": bundle.base_url, "siamois_organisation_id": bundle.org_id,
            "siamois_derniere_synchro": datetime.now().isoformat(timespec="seconds"), "siamois_langue": "fr"}
    out = [
        ("metadonnees", "idmeta", _plain_layer("metadonnees", None, srid, sm.metadata_columns(), [meta])),
        ("concept", "idconcept", _plain_layer("concept", None, srid, sm.concept_columns(), concept_rows(bundle))),
        ("personne", "idpersonne", _plain_layer("personne", None, srid, sm.person_columns())),
        ("raster", "iddoc", _plain_layer("raster", sm.POLYGON, srid, sm.raster_columns())),
    ]
    for name in sm.LINK_TABLES:
        out.append((name, "idlien", _plain_layer(name, None, srid, sm.link_columns(name))))
    return out


def write_geopackage(bundle: ProjectBundle, gpkg_path: str) -> Dict[str, Dict[str, Optional[str]]]:
    """Écrit le GeoPackage au format du modèle spatial (recréé). Retourne {couche: {id SIAMOIS: wkt normalisé}}."""
    if os.path.exists(gpkg_path):
        os.remove(gpkg_path)
    srid = dominant_srid(r.geom for l in bundle.layers for r in l.rows)
    wkts: Dict[str, Dict[str, Optional[str]]] = {}
    jobs: List[Tuple[str, str, QgsVectorLayer, bool]] = []
    for ld in bundle.layers:
        pk = "idprojet" if ld.table == sm.PROJECT_TABLE else sm.ENTITY_TABLES[ld.table].id_col
        jobs.append((ld.name, pk, _entity_layer(ld, bundle), True))
    jobs += [(n, pk, mem, False) for n, pk, mem in _auxiliary_tables(bundle, srid)]
    for i, (name, pk, mem, tracked) in enumerate(jobs):
        opts = QgsVectorFileWriter.SaveVectorOptions()
        opts.driverName = "GPKG"
        opts.layerName = name
        opts.fileEncoding = "UTF-8"
        opts.layerOptions = [f"FID={pk}"]
        opts.actionOnExistingFile = (QgsVectorFileWriter.CreateOrOverwriteFile if i == 0
                                     else QgsVectorFileWriter.CreateOrOverwriteLayer)
        res = QgsVectorFileWriter.writeAsVectorFormatV3(mem, gpkg_path, QgsCoordinateTransformContext(), opts)
        log.info(f"GPKG {name}: {mem.featureCount()} ligne(s), type {QgsWkbTypes.displayString(mem.wkbType())}, "
                 f"résultat {res}")
        if res[0] != QgsVectorFileWriter.NoError:
            raise RuntimeError(f"Écriture GeoPackage impossible ({name}, code {res[0]}, "
                               f"type {QgsWkbTypes.displayString(mem.wkbType())}) : {res[1] or 'aucun détail'}")
        if tracked:
            wkts[name] = {f[ID_FIELD]: norm_wkt(f.geometry().asWkt() if f.hasGeometry() else None)
                          for f in mem.getFeatures()}
    return wkts


# ------------------------------------------------------------------ widgets natifs


def _setup(layer: QgsVectorLayer, idx: int, wtype: str, config: Dict[str, Any]) -> None:
    layer.setEditorWidgetSetup(idx, QgsEditorWidgetSetup(wtype, config))


def _value_map(labels: List[str]) -> Dict[str, Any]:
    entries = [{EMPTY_LABEL: ""}] + [{l: l} for l in labels]
    return {"map": entries}


def configure_layer(layer: QgsVectorLayer, specs: List[ColumnSpec], vocabs: Vocabularies, geom_editable: bool) -> None:
    """Listes déroulantes, bornes, dates, champs obligatoires, lecture seule, onglets par panneau."""
    form = layer.editFormConfig()
    fields = layer.fields()
    for hidden in (ID_FIELD, REV_FIELD):
        idx = fields.indexOf(hidden)
        if idx >= 0:
            _setup(layer, idx, "Hidden", {})
            form.setReadOnly(idx, True)
    for s in specs:
        idx = fields.indexOf(s.name)
        if idx < 0:
            continue
        vocab = vocabs.get(s.vocab_code or "")
        if s.kind == CHOICE and vocab is not None and s.editable:
            _setup(layer, idx, "ValueMap", _value_map(vocab.sorted_labels))
        elif s.kind in (INTEGER, DECIMAL, MEASUREMENT) and s.editable:
            big = 1e12
            _setup(layer, idx, "Range", {
                "Min": s.min if s.min is not None else -big, "Max": s.max if s.max is not None else big,
                "Step": 1 if s.kind == INTEGER else 0.01, "Style": "SpinBox", "AllowNull": True,
                "Precision": 0 if s.kind == INTEGER else 3,
                "Suffix": f" {s.unit}" if s.unit else ""})
        elif s.kind == DATETIME and s.editable:
            _setup(layer, idx, "DateTime", {
                "field_iso_format": True, "display_format": "yyyy-MM-dd", "calendar_popup": True,
                "allow_null": True})
        form.setReadOnly(idx, not s.editable)
        if s.required:
            layer.setFieldConstraint(idx, QgsFieldConstraints.ConstraintNotNull, QgsFieldConstraints.ConstraintStrengthSoft)
    _build_tabs(layer, form, specs)
    layer.setEditFormConfig(form)


def _build_tabs(layer: QgsVectorLayer, form: QgsEditFormConfig, specs: List[ColumnSpec]) -> None:
    """Fiche = formulaire QGIS à onglets, un onglet par panneau du formulaire serveur."""
    try:
        layout = QgsEditFormConfig.TabLayout
    except AttributeError:  # QGIS >= 3.30 (enum déplacé)
        from qgis.core import Qgis
        layout = Qgis.AttributeFormLayout.DragAndDrop
    form.setLayout(layout)
    root = form.invisibleRootContainer()
    root.clear()
    panels: Dict[str, List[ColumnSpec]] = {}
    for s in specs:
        panels.setdefault(s.panel or "Général", []).append(s)
    for name, items in panels.items():
        tab = QgsAttributeEditorContainer(name, root)
        for s in items:
            idx = layer.fields().indexOf(s.name)
            if idx >= 0:
                tab.addChildElement(QgsAttributeEditorField(s.name, idx, tab))
        root.addChildElement(tab)
    # colonnes du modèle spatial (clés, numéro, type, auteur, date…) dans un onglet à part
    known = {s.name for s in specs} | {ID_FIELD, REV_FIELD}
    others = [f.name() for f in layer.fields() if f.name() not in known]
    if others:
        tab = QgsAttributeEditorContainer("Modèle spatial", root)
        for name in others:
            tab.addChildElement(QgsAttributeEditorField(name, layer.fields().indexOf(name), tab))
        root.addChildElement(tab)


def remove_project_layers(project_name: str, project: Optional[QgsProject] = None) -> None:
    """Retire le groupe d'un projet SIAMOIS (à faire avant de réécrire son GeoPackage : fichier verrouillé sinon)."""
    project = project or QgsProject.instance()
    root = project.layerTreeRoot()
    old = root.findGroup(f"SIAMOIS – {project_name}")
    if old is not None:
        for node in old.findLayers():
            project.removeMapLayer(node.layerId())
        root.removeChildNode(old)


def configure_model_columns(layer: QgsVectorLayer, table: str, kind: str, author: str) -> None:
    """Colonnes de Quentin : clés et miroirs en lecture seule, valeurs par défaut des nouvelles lignes."""
    fields = layer.fields()
    form = layer.editFormConfig()
    t = sm.ENTITY_TABLES.get(table)
    readonly = ["idprojet", "table_siamois", "numprojet"] + ([t.id_col, t.num_col, t.typ_col] if t else ["idprojet"])
    for name in readonly:
        idx = fields.indexOf(name)
        if idx >= 0:
            form.setReadOnly(idx, True)
    defaults = {"idprojet": "1", "table_siamois": "'" + sm.TABLE_SIAMOIS_LABEL[kind].replace("'", "''") + "'",
                "autsaisie": "'" + author.replace("'", "''") + "'", "datesaisie": "to_date(now())"}
    for name, expr in defaults.items():
        idx = fields.indexOf(name)
        if idx >= 0:
            layer.setDefaultValueDefinition(idx, QgsDefaultValue(expr, False))
    layer.setEditFormConfig(form)


def mirror_values(table: str, kind: str, full: Optional[str], label: Optional[str]) -> Dict[str, Any]:
    t = sm.ENTITY_TABLES.get(table)
    if t is not None:
        return {t.num_col: full, t.typ_col: label}
    return {"numprojet": full}


def refresh_mirrors(layer: QgsVectorLayer, specs: List[ColumnSpec], table: str, kind: str) -> int:
    """Recopie « Identifiant complet » et « Type » dans les colonnes numXXX / typXXX du modèle. Retourne le nombre de lignes."""
    fields = layer.fields()
    type_col = type_column(specs)
    changes: Dict[int, Dict[int, Any]] = {}
    for f in layer.getFeatures():
        want = mirror_values(table, kind, _clean(f[FULL_IDENTIFIER_COLUMN]) if FULL_IDENTIFIER_COLUMN in fields.names() else None,
                             _clean(f[type_col]) if type_col else None)
        upd = {}
        for name, value in want.items():
            idx = fields.indexOf(name)
            if idx >= 0 and _clean(f[name]) != value:
                upd[idx] = value
        if upd:
            changes[f.id()] = upd
    if changes:
        layer.dataProvider().changeAttributeValues(changes)
        layer.reload()
    return len(changes)


def add_to_project(bundle: ProjectBundle, gpkg_path: str, db_path: str, vocabs: Vocabularies,
                   project: Optional[QgsProject] = None) -> List[Tuple[LayerData, QgsVectorLayer]]:
    """Ajoute les couches visibles (tables non vides + tables par défaut) dans un groupe ; retourne (données, couche)."""
    project = project or QgsProject.instance()
    group_name = f"SIAMOIS – {bundle.project_name}"
    remove_project_layers(bundle.project_name, project)
    group = project.layerTreeRoot().insertGroup(0, group_name)
    layers = []
    for ld in bundle.layers:
        if not ld.visible:
            continue
        layer = QgsVectorLayer(f"{gpkg_path}|layername={ld.name}", ld.title, "ogr")
        if not layer.isValid():
            raise RuntimeError(f"Couche invalide : {ld.name}")
        layer.setCustomProperty(PROP_DB, db_path)
        layer.setCustomProperty(PROP_KIND, ld.kind)
        layer.setCustomProperty(PROP_NAME, ld.name)
        layer.setCustomProperty(PROP_TABLE, ld.table)
        configure_layer(layer, ld.specs, vocabs, ld.geom_editable)
        configure_model_columns(layer, ld.table, ld.kind, bundle.author)
        project.addMapLayer(layer, False)
        group.addLayer(layer)
        layers.append((ld, layer))
    return layers


def siamois_layers(project: Optional[QgsProject] = None) -> List[QgsVectorLayer]:
    project = project or QgsProject.instance()
    return [l for l in project.mapLayers().values()
            if isinstance(l, QgsVectorLayer) and l.customProperty(PROP_DB)]


# ------------------------------------------------------------------ lecture des modifications


def read_current(layer: QgsVectorLayer, specs: List[ColumnSpec]) -> Tuple[Dict[str, Dict[str, Any]], List[Dict[str, Any]]]:
    """(id -> {cells, wkt, geojson, fid}, lignes neuves [{cells, wkt, geojson, fid}] sans identifiant SIAMOIS)."""
    srid = layer.crs().postgisSrid() or 4326
    out: Dict[str, Dict[str, Any]] = {}
    new_rows: List[Dict[str, Any]] = []
    for f in layer.getFeatures():
        sid = _clean(f[ID_FIELD])
        g = f.geometry() if f.hasGeometry() else None
        has_geom = g is not None and not g.isNull()
        row = {
            "cells": {s.name: _clean(f[s.name]) for s in specs},
            "wkt": norm_wkt(g.asWkt()) if has_geom else None,
            "geojson": with_srid(json.loads(g.asJson(8)), srid) if has_geom else None,
            "fid": f.id(),
        }
        if sid is None:
            new_rows.append(row)
        else:
            out[str(sid)] = row
    return out, new_rows


def update_after_sync(layer: QgsVectorLayer, fid: int, revision: Optional[int]) -> None:
    """Met à jour la révision d'une ligne directement dans le fournisseur (sans passer par le tampon d'édition)."""
    if revision is None:
        return
    idx = layer.fields().indexOf(REV_FIELD)
    layer.dataProvider().changeAttributeValues({fid: {idx: revision}})


def apply_server_row(layer: QgsVectorLayer, fid: int, specs: List[ColumnSpec], cells: Dict[str, Any],
                     geom_wkt: Optional[str], revision: Optional[int]) -> None:
    """Résolution « garder le serveur » : réécrit la ligne locale avec la version serveur."""
    attrs = {layer.fields().indexOf(s.name): _clean(cells.get(s.name)) for s in specs}
    if revision is not None:
        attrs[layer.fields().indexOf(REV_FIELD)] = revision
    layer.dataProvider().changeAttributeValues({fid: attrs})
    if geom_wkt is not None:
        g = QgsGeometry.fromWkt(geom_wkt)
        if g is not None and not g.isNull():
            layer.dataProvider().changeGeometryValues({fid: g})
    layer.reload()


def apply_created_row(layer: QgsVectorLayer, fid: int, specs: List[ColumnSpec], siamois_id: str,
                      cells: Dict[str, Any], revision: Optional[int]) -> None:
    """Après création serveur : la ligne locale reçoit son identifiant, sa révision et les valeurs du serveur."""
    attrs = {layer.fields().indexOf(ID_FIELD): siamois_id}
    if revision is not None:
        attrs[layer.fields().indexOf(REV_FIELD)] = revision
    for s in specs:
        idx = layer.fields().indexOf(s.name)
        if idx >= 0:
            attrs[idx] = _clean(cells.get(s.name))
    layer.dataProvider().changeAttributeValues({fid: attrs})
    layer.reload()


def refresh_choice_widget(layer: QgsVectorLayer, spec: ColumnSpec, labels: List[str]) -> None:
    """Met à jour la liste déroulante d'une colonne (ex. nouvelles UE proposées pour le mobilier)."""
    idx = layer.fields().indexOf(spec.name)
    if idx >= 0:
        _setup(layer, idx, "ValueMap", _value_map(labels))
