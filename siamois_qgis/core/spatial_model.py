"""Modèle de données spatiales SIAMOIS (Quentin Verriez) : tables, colonnes, répartition des entités. Sans QGIS.

Chaque entité SIAMOIS (UE, mobilier) est rangée dans une table du modèle. Sans évolution du serveur, la table est
choisie ainsi :

1. une correspondance « type → table » saisie par l'utilisateur (`SpatialConfig.type_tables`) ;
2. sinon la géométrie réelle de l'entité (polygone, ligne, point) ;
3. sinon la **géométrie par défaut de la table SIAMOIS** (`SpatialConfig.default_geometry` : UE = polygone,
   mobilier = point, projet = polygone).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Dict, List, Optional, Tuple

KIND_PROJECT, KIND_RU, KIND_FIND = "project", "recording_unit", "find"

POLYGON, LINE, POINT = "Polygon", "LineString", "Point"

INTEGER, TEXT, DATE = "integer", "text", "date"

# Colonnes SIAMOIS ajoutées aux tables du modèle (cf. docs/erd)
SIAMOIS_ID, SIAMOIS_REV = "siamois_id", "siamois_revision"

TABLE_SIAMOIS_LABEL = {KIND_PROJECT: "Projet", KIND_RU: "Unité d'enregistrement", KIND_FIND: "Mobilier"}
AUTHOR_FALLBACK = "SIAMOIS"


@dataclass(frozen=True)
class SpatialTable:
    name: str
    suffix: str  # id<suffix>, num<suffix>, typ<suffix>
    geom: Optional[str]  # POLYGON / LINE / POINT, None = attributaire
    kind: str  # entité SIAMOIS par défaut
    title: str
    interpret: bool = False  # interpret, datedebut, datefin (UO, UR)
    metacquis: bool = False  # méthode d'acquisition (UT, UO, UR)

    @property
    def id_col(self) -> str:
        return "id" + self.suffix

    @property
    def num_col(self) -> str:
        return "num" + self.suffix

    @property
    def typ_col(self) -> str:
        return "typ" + self.suffix


ENTITY_TABLES: Dict[str, SpatialTable] = {t.name: t for t in (
    SpatialTable("ut_poly", "utpoly", POLYGON, KIND_RU, "UT surfaciques", metacquis=True),
    SpatialTable("ut_ligne", "utlgn", LINE, KIND_RU, "UT linéaires", metacquis=True),
    SpatialTable("ut_point", "utpt", POINT, KIND_RU, "UT ponctuelles", metacquis=True),
    SpatialTable("uo_poly", "uopoly", POLYGON, KIND_RU, "UO surfaciques", interpret=True, metacquis=True),
    SpatialTable("uo_point", "uopt", POINT, KIND_FIND, "UO ponctuelles", interpret=True, metacquis=True),
    SpatialTable("ur_poly", "urpoly", POLYGON, KIND_RU, "UR surfaciques", interpret=True, metacquis=True),
    SpatialTable("ud_poly", "udpoly", POLYGON, KIND_RU, "UD surfaciques"),
    SpatialTable("ud_ligne", "udlgn", LINE, KIND_RU, "UD linéaires"),
)}
ENTITY_ORDER = list(ENTITY_TABLES)
PROJECT_TABLE = "projet"
RASTER_TABLE = "raster"

# Tables rattachées à la géométrie par défaut d'une famille
TABLE_BY_GEOMETRY = {POLYGON: "uo_poly", LINE: "ut_ligne", POINT: "ut_point"}
DEFAULT_GEOMETRY = {KIND_PROJECT: POLYGON, KIND_RU: POLYGON, KIND_FIND: POINT}
DEFAULT_TABLE = {KIND_RU: "uo_poly", KIND_FIND: "uo_point"}  # table « naturelle » de chaque entité SIAMOIS

AUTO = "auto"  # valeur d'une correspondance : laisser le plugin choisir

LINK_TABLES: Dict[str, Tuple[str, str]] = {
    "lien_doc_uopoly": ("iddoc", "iduopoly"),
    "lien_doc_uopt": ("iddoc", "iduopt"),
    "lien_uopoly_urpoly": ("iduopoly", "idurpoly"),
    "lien_uopoly_udpoly": ("iduopoly", "idudpoly"),
    "lien_urpoly_udpoly": ("idurpoly", "idudpoly"),
    "lien_uopoly_udlgn": ("iduopoly", "idudlgn"),
    "lien_urpoly_udlgn": ("idurpoly", "idudlgn"),
}


def geometry_family(geom: Optional[Dict[str, Any]]) -> Optional[str]:
    """POLYGON / LINE / POINT d'une géométrie GeoJSON de l'API (Multi* ramené à sa famille)."""
    t = (geom or {}).get("type")
    if not t:
        return None
    t = t.replace("Multi", "")
    return {"Polygon": POLYGON, "LineString": LINE, "Point": POINT}.get(t)


@dataclass
class SpatialConfig:
    """Réglages de répartition. Enregistrés par projet (JSON)."""

    type_tables: Dict[str, str] = field(default_factory=dict)  # libellé de type -> table du modèle ou AUTO
    default_geometry: Dict[str, str] = field(default_factory=lambda: dict(DEFAULT_GEOMETRY))

    def to_json(self) -> str:
        return json.dumps({"type_tables": self.type_tables, "default_geometry": self.default_geometry})

    @classmethod
    def from_json(cls, text: Optional[str]) -> "SpatialConfig":
        if not text:
            return cls()
        try:
            d = json.loads(text)
        except ValueError:
            return cls()
        cfg = cls()
        cfg.type_tables = {str(k): v for k, v in (d.get("type_tables") or {}).items()
                           if v == AUTO or v in ENTITY_TABLES}
        cfg.default_geometry.update({k: v for k, v in (d.get("default_geometry") or {}).items()
                                     if k in DEFAULT_GEOMETRY and v in (POLYGON, LINE, POINT)})
        return cfg


def tables_for_kind(kind: str) -> List[str]:
    """Tables où une entité SIAMOIS peut être rangée : `uo_point` est réservée au mobilier (Quentin), les autres aux UE.

    Une table ne reçoit qu'un seul type d'entité SIAMOIS, ce qui garde la synchronisation simple (un type d'API par couche).
    """
    if kind == KIND_FIND:
        return ["uo_point"]
    return [n for n in ENTITY_TABLES if n != "uo_point"]


def table_for_default_geometry(kind: str, config: SpatialConfig) -> str:
    """Table choisie quand l'entité n'a pas de géométrie : celle de la géométrie par défaut de sa table SIAMOIS."""
    fam = config.default_geometry.get(kind, DEFAULT_GEOMETRY[kind])
    natural = DEFAULT_TABLE[kind]
    if ENTITY_TABLES[natural].geom == fam:
        return natural
    return TABLE_BY_GEOMETRY[fam] if TABLE_BY_GEOMETRY[fam] in tables_for_kind(kind) else natural


def choose_table(kind: str, geom: Optional[Dict[str, Any]], type_label: Optional[str],
                 config: SpatialConfig) -> str:
    """Table du modèle pour une entité (voir l'ordre de décision en tête de module)."""
    allowed = tables_for_kind(kind)
    fam = geometry_family(geom)
    wanted = config.type_tables.get((type_label or "").strip())
    if wanted in allowed and (fam is None or ENTITY_TABLES[wanted].geom == fam):
        return wanted  # sinon (table incompatible avec la géométrie) la géométrie réelle l'emporte
    if fam is None:
        return table_for_default_geometry(kind, config)
    if kind == KIND_FIND:
        return DEFAULT_TABLE[kind]  # le mobilier est toujours ponctuel
    return TABLE_BY_GEOMETRY[fam]


# ------------------------------------------------------------------ colonnes


def core_columns(t: SpatialTable) -> List[Tuple[str, str]]:
    """Colonnes de Quentin d'une table d'entités, hors clé primaire et géométrie, dans l'ordre du modèle."""
    cols: List[Tuple[str, str]] = [("idprojet", INTEGER), (t.num_col, TEXT), (t.typ_col, TEXT), ("table_siamois", TEXT)]
    if t.interpret:
        cols += [("interpret", TEXT), ("datedebut", INTEGER), ("datefin", INTEGER)]
    cols += [("com", TEXT), ("autsaisie", TEXT), ("datesaisie", DATE)]
    if t.metacquis:
        cols.append(("metacquis", TEXT))
    return cols


def sync_columns(t: SpatialTable) -> List[Tuple[str, str]]:
    """Colonnes de synchronisation SIAMOIS ajoutées à une table d'entités."""
    return [(SIAMOIS_ID, TEXT), (SIAMOIS_REV, INTEGER)]


def today() -> str:
    return date.today().isoformat()


def core_values(t: SpatialTable, kind: str, full_identifier: Optional[str], type_label: Optional[str],
                author: str = AUTHOR_FALLBACK, day: Optional[str] = None) -> Dict[str, Any]:
    """Valeurs des colonnes de Quentin déduites d'une ligne SIAMOIS (le reste est laissé vide)."""
    return {"idprojet": 1, t.num_col: full_identifier, t.typ_col: type_label,
            "table_siamois": TABLE_SIAMOIS_LABEL[kind], "autsaisie": author, "datesaisie": day or today()}


# ------------------------------------------------------------------ tables hors entités


def project_columns() -> List[Tuple[str, str]]:
    return [("idprojet_parent", INTEGER), ("numoa", TEXT), ("numprojet", TEXT), ("typprojet", TEXT),
            ("table_siamois", TEXT), ("ro", TEXT), ("com", TEXT), ("autsaisie", TEXT), ("datesaisie", DATE)]


def metadata_columns() -> List[Tuple[str, str]]:
    cols = [("idprojet", INTEGER), ("table_siamois", TEXT)]
    cols += [(c, TEXT) for c in ("dc_title", "dc_creator", "dc_subject", "dc_descri", "dc_publish", "dc_date", "dc_type",
                                 "dc_format", "dc_id", "dc_source", "dc_lang", "dc_relation", "dc_coverage", "dc_right")]
    cols += [("epsg", INTEGER), ("alti", TEXT), ("geoid", TEXT), ("autsaisie", TEXT), ("datesaisie", DATE),
             ("com", TEXT)]
    cols += [("siamois_url", TEXT), ("siamois_organisation_id", TEXT), ("siamois_derniere_synchro", TEXT),
             ("siamois_langue", TEXT)]
    return cols


def concept_columns() -> List[Tuple[str, str]]:
    return [("nom_table", TEXT), ("type_entite", TEXT), ("nom_colonne", TEXT), ("label", TEXT), ("uri", TEXT),
            ("autsaisie", TEXT), ("datesaisie", DATE), ("siamois_id_concept", TEXT), ("siamois_code_vocabulaire", TEXT)]


def person_columns() -> List[Tuple[str, str]]:
    return [("nom", TEXT), ("typpersonne", TEXT), ("uri", TEXT), ("autsaisie", TEXT), ("datesaisie", DATE),
            ("siamois_id_personne", TEXT), ("siamois_email", TEXT)]


def raster_columns() -> List[Tuple[str, str]]:
    return [("idprojet", INTEGER), ("numdoc", TEXT), ("typdoc", TEXT), ("table_siamois", TEXT), ("lien", TEXT),
            ("com", TEXT), ("autsaisie", TEXT), ("datesaisie", DATE)]


def link_columns(name: str) -> List[Tuple[str, str]]:
    a, b = LINK_TABLES[name]
    return [(a, INTEGER), (b, INTEGER), ("autsaisie", TEXT), ("datesaisie", DATE)]


# ------------------------------------------------------------------ référentiel « concept »


def build_concepts(entity_columns: Dict[str, List[Tuple[str, str, List[Dict[str, Any]]]]],
                   author: str = AUTHOR_FALLBACK, day: Optional[str] = None) -> List[Dict[str, Any]]:
    """Lignes de la table `concept`.

    `entity_columns` : table du modèle -> [(nom de colonne, code de vocabulaire SIAMOIS, concepts API)].
    Un terme est unique par (table, colonne, libellé) ; `siamois_id_concept` garde l'identifiant du concept.
    """
    out: List[Dict[str, Any]] = []
    seen = set()
    for table, cols in entity_columns.items():
        for column, code, concepts in cols:
            for c in concepts:
                label = c.get("resolvedLabel") or c.get("label")
                if not label:
                    continue
                key = (table, column, label)
                if key in seen:
                    continue
                seen.add(key)
                out.append({"nom_table": table, "type_entite": None, "nom_colonne": column, "label": label,
                            "uri": c.get("uri") or c.get("externalId"), "autsaisie": author,
                            "datesaisie": day or today(), "siamois_id_concept": str(c.get("id") or ""),
                            "siamois_code_vocabulaire": code})
    return out
