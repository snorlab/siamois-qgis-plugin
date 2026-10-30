"""Chargement d'un projet depuis l'API vers des structures à plat (sans QGIS)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Set

from . import _sdk_import  # noqa: F401
from siamois_sdk import SiamoisClient
from siamois_sdk.flatten import (
    CHOICE, TEXT, ColumnSpec, Vocabulary, Vocabularies, flatten_schema, incomplete_columns, merge_schemas,
    row_to_cells,
)
from .geo import dominant_srid, srid_of
from . import spatial_model as sm

FID_FULL = "__fullIdentifier"
FULL_IDENTIFIER_COLUMN = "Identifiant complet"
FID_STATUS = "__validated"
VOCAB_STATUS = "__validated"
VOCAB_RU = "__ru"  # vocabulaire synthétique : UE du projet (id -> identifiant complet)
FID_TYPE = "__type"  # le type n'est pas un champ du formulaire (il vit dans l'en-tête de la fiche) : colonne ajoutée
VOCAB_TYPE = {"recording_unit": "__type_ru", "find": "__type_find"}  # vocabulaires synthétiques des types
BINDING_TYPE = "type"
BINDING_RU = "recordingUnit"
IMMUTABLE_BINDINGS = (BINDING_TYPE, BINDING_RU)  # non modifiables après création

STATUS_LABELS = {"INCOMPLETE": "En cours", "COMPLETE": "Terminé", "CANCELLED": "Annulé", "VALIDATED": "Validé"}
EDITABLE_STATUSES = ("INCOMPLETE", "COMPLETE", "CANCELLED")  # VALIDATED exige un droit validateur : lecture seule

KIND_PROJECT, KIND_RU, KIND_FIND = sm.KIND_PROJECT, sm.KIND_RU, sm.KIND_FIND
KIND_TITLES = {KIND_PROJECT: "Projet", KIND_RU: "UE", KIND_FIND: "Mobilier"}


class Cancelled(Exception):
    pass


Progress = Callable[[str, int, int], bool]


@dataclass
class RowData:
    id: str
    cells: Dict[str, Any]
    geom: Optional[Dict[str, Any]] = None
    revision: Optional[int] = None
    incomplete: List[str] = field(default_factory=list)
    allowed: Set[str] = field(default_factory=set)  # field_id éditables pour le type de la ligne
    type_id: Optional[str] = None


@dataclass
class LayerData:
    kind: str
    name: str
    title: str
    specs: List[ColumnSpec]
    rows: List[RowData]
    srid: int = 4326
    geom_editable: bool = True
    type_allowed: Dict[str, Set[str]] = field(default_factory=dict)  # type_id -> field_id du formulaire du type
    default_allowed: Set[str] = field(default_factory=set)
    table: str = ""  # table du modèle spatial (= nom de la couche)
    visible: bool = True  # ajoutée au projet QGIS (les tables vides, hors tables par défaut, restent dans le fichier)

    @property
    def has_geometry(self) -> bool:
        return any(r.geom for r in self.rows)


@dataclass
class ProjectBundle:
    org_id: str
    project_id: str
    project_name: str
    layers: List[LayerData]
    vocab_concepts: Dict[str, List[Dict[str, Any]]]
    base_url: str = ""
    spatial: sm.SpatialConfig = field(default_factory=sm.SpatialConfig)
    author: str = sm.AUTHOR_FALLBACK

    def layer(self, table: str) -> LayerData:
        return next(l for l in self.layers if l.name == table)

    def layers_of(self, kind: str) -> List[LayerData]:
        return [l for l in self.layers if l.kind == kind]

    def count(self, kind: str) -> int:
        return sum(len(l.rows) for l in self.layers_of(kind))


def status_vocabulary() -> Vocabulary:
    return Vocabulary({"id": k, "resolvedLabel": v} for k, v in STATUS_LABELS.items() if k in EDITABLE_STATUSES)


def fixed_specs(kind: Optional[str] = None) -> List[ColumnSpec]:
    specs = [
        ColumnSpec(FULL_IDENTIFIER_COLUMN, FID_FULL, TEXT, "TEXT", editable=False),
        ColumnSpec("Statut", FID_STATUS, CHOICE, "SELECT_ONE_FROM_FIELD_CODE", vocab_code=VOCAB_STATUS),
    ]
    if kind in VOCAB_TYPE:  # choisi à la création, non modifiable ensuite (IMMUTABLE_BINDINGS)
        specs.append(ColumnSpec("Type", FID_TYPE, CHOICE, "SELECT_ONE_FROM_FIELD_CODE", vocab_code=VOCAB_TYPE[kind],
                                required=True, binding=BINDING_TYPE))
    return specs


def with_fixed(answers: Dict[str, Any], full_identifier: Optional[str], validated: Optional[str],
               type_id: Optional[str] = None, type_label: Optional[str] = None) -> Dict[str, Any]:
    out = dict(answers or {})
    if type_id:
        out[FID_TYPE] = {"resourceId": type_id, "label": type_label}
    out[FID_FULL] = full_identifier
    out[FID_STATUS] = {"resourceId": validated, "label": STATUS_LABELS.get(validated or "", validated)} if validated else None
    return out


def ru_vocabulary(concepts) -> Vocabulary:
    return Vocabulary(concepts)


def entity_answers(kind: str, entity: Any, specs: List[ColumnSpec]) -> Dict[str, Any]:
    """Réponses « à plat » d'une entité API (colonnes techniques + champ UE du mobilier incluses)."""
    answers = with_fixed(entity.answers, entity.full_identifier, entity.validated,
                         getattr(entity, "type_id", None), getattr(entity, "type_label", None))
    if kind == KIND_FIND and getattr(entity, "recording_unit_id", None):
        for s in specs:
            if s.binding == BINDING_RU:
                answers[s.field_id] = {"resourceId": entity.recording_unit_id}
    return answers


def _step(progress: Optional[Progress], label: str, done: int = 0, total: int = 0) -> None:
    if progress is not None and progress(label, done, total) is False:
        raise Cancelled()


def _vocab_codes(layers: List[LayerData]) -> Set[str]:
    return {s.vocab_code for l in layers for s in l.specs if s.vocab_code and s.vocab_code != VOCAB_STATUS}


def load_project(client: SiamoisClient, org_id: Any, project_id: Any, progress: Optional[Progress] = None,
                 spatial: Optional[sm.SpatialConfig] = None, author: str = sm.AUTHOR_FALLBACK) -> ProjectBundle:
    """Charge projet + UE + mobilier, rangés dans les tables du modèle spatial. Lève `Cancelled` si `progress(...)` renvoie False."""
    spatial = spatial or sm.SpatialConfig()
    _step(progress, "Projet")
    project = client.project(project_id, fields="all")
    pid = project.id
    project_form = client.project_form(org_id)

    _step(progress, "Formulaires")
    ru_default, ru_by_type, ru_types = client.recording_unit_types(pid)
    find_default, find_by_type, find_types = client.find_types(pid)

    ru_specs = merge_schemas(fixed_specs(KIND_RU), flatten_schema(ru_default),
                             *(flatten_schema(f) for f in ru_by_type.values()))
    find_specs = merge_schemas(fixed_specs(KIND_FIND), flatten_schema(find_default),
                               *(flatten_schema(f) for f in find_by_type.values()))
    project_specs = merge_schemas(fixed_specs(), flatten_schema(project_form))

    for sp in find_specs:  # l'UE d'un mobilier : liste des UE du projet (utile à la création)
        if sp.binding == BINDING_RU:
            sp.kind, sp.vocab_code, sp.editable = CHOICE, VOCAB_RU, True

    # Vocabulaires de toutes les colonnes (un appel par code)
    synthetic = (VOCAB_STATUS, VOCAB_RU, *VOCAB_TYPE.values())
    codes = sorted({s.vocab_code for specs in (ru_specs, find_specs, project_specs) for s in specs
                    if s.vocab_code and s.vocab_code not in synthetic})
    vocab_concepts: Dict[str, List[Dict[str, Any]]] = {VOCAB_TYPE[KIND_RU]: ru_types, VOCAB_TYPE[KIND_FIND]: find_types}
    vocabs: Vocabularies = {VOCAB_STATUS: status_vocabulary(), VOCAB_RU: Vocabulary(),
                            VOCAB_TYPE[KIND_RU]: Vocabulary(ru_types), VOCAB_TYPE[KIND_FIND]: Vocabulary(find_types)}
    for i, code in enumerate(codes):
        _step(progress, f"Vocabulaire {code}", i, len(codes))
        v = client.vocabulary(pid, code)
        vocabs[code] = v
        vocab_concepts[code] = v.concepts

    # Projet (1 ligne)
    p_allowed = {s.field_id for s in project_specs}
    p_row = RowData(
        id=pid,
        cells=row_to_cells(project_specs, with_fixed(project.answers, project.full_identifier, project.validated), vocabs),
        geom=project.geom, allowed=p_allowed,
        incomplete=incomplete_columns(project_specs, project.answers),
    )

    # UE
    ru_rows: List[RowData] = []
    ru_allowed = {t: {*(c for c in f.field_ids()), FID_FULL, FID_STATUS, FID_TYPE} for t, f in ru_by_type.items()}
    default_allowed = {*ru_default.field_ids(), FID_FULL, FID_STATUS, FID_TYPE}

    def on_ru(done: int, total: int) -> bool:
        return progress("Unités d'enregistrement", done, total) is not False if progress else True

    for ru in client.iter_all(lambda **kw: client.recording_units(pid, **kw), on_progress=on_ru):
        ans = entity_answers(KIND_RU, ru, ru_specs)
        allowed = default_allowed | ru_allowed.get(ru.type_id or "", set())
        ru_rows.append(RowData(ru.id, row_to_cells(ru_specs, ans, vocabs), ru.geom, ru.sync_revision,
                               incomplete_columns(ru_specs, ru.answers), allowed, ru.type_id))
    if progress and progress("Unités d'enregistrement", len(ru_rows), len(ru_rows)) is False:
        raise Cancelled()

    ru_concepts = [{"id": r.id, "resolvedLabel": r.cells.get("Identifiant complet")}
                   for r in ru_rows if r.cells.get("Identifiant complet")]
    vocabs[VOCAB_RU] = ru_vocabulary(ru_concepts)
    vocab_concepts[VOCAB_RU] = ru_concepts

    # Mobilier
    find_rows: List[RowData] = []
    find_allowed = {t: set(f.field_ids()) for t, f in find_by_type.items()}
    default_find = {*find_default.field_ids(), FID_FULL, FID_STATUS, FID_TYPE}

    def on_find(done: int, total: int) -> bool:
        return progress("Mobilier", done, total) is not False if progress else True

    for f in client.iter_all(lambda **kw: client.finds(pid, **kw), on_progress=on_find):
        ans = entity_answers(KIND_FIND, f, find_specs)
        allowed = default_find | find_allowed.get(f.type_id or "", set())
        find_rows.append(RowData(f.id, row_to_cells(find_specs, ans, vocabs), f.geom, None,
                                 incomplete_columns(find_specs, f.answers), allowed, f.type_id))

    layers = [LayerData(KIND_PROJECT, sm.PROJECT_TABLE, layer_title(KIND_PROJECT, sm.PROJECT_TABLE), project_specs,
                        [p_row], srid_of(project.geom), table=sm.PROJECT_TABLE)]
    ru_type = type_column(ru_specs)
    find_type = type_column(find_specs)
    ru_layers = split_by_table(KIND_RU, ru_specs, ru_rows, ru_type, spatial,
                               type_allowed=ru_allowed, default_allowed=default_allowed)
    # L'API ne permet pas de modifier la géométrie d'un mobilier : lecture seule
    find_layers = split_by_table(KIND_FIND, find_specs, find_rows, find_type, spatial, geom_editable=False,
                                 type_allowed={t: a | {FID_FULL, FID_STATUS, FID_TYPE} for t, a in find_allowed.items()},
                                 default_allowed=default_find)
    layers += ru_layers + find_layers
    return ProjectBundle(str(org_id), pid, project.name, layers, vocab_concepts, client.base_url, spatial, author)


def layer_title(kind: str, table: str) -> str:
    t = sm.ENTITY_TABLES.get(table)
    return f"SIAMOIS – {KIND_TITLES[kind]} · {table}" + (f" ({t.title})" if t else "")


def type_column(specs: List[ColumnSpec]) -> Optional[str]:
    """Nom de la colonne « Type » (champ lié au type de l'entité), s'il existe."""
    return next((s.name for s in specs if s.binding == BINDING_TYPE), None)


def split_by_table(kind: str, specs: List[ColumnSpec], rows: List[RowData], type_col: Optional[str],
                   config: sm.SpatialConfig, geom_editable: bool = True, **layer_kwargs) -> List[LayerData]:
    """Une couche par table du modèle compatible avec `kind` ; chaque ligne va dans la table choisie par `choose_table`."""
    per_table: Dict[str, List[RowData]] = {name: [] for name in sm.tables_for_kind(kind)}
    for r in rows:
        label = r.cells.get(type_col) if type_col else None
        per_table[sm.choose_table(kind, r.geom, label if isinstance(label, str) else None, config)].append(r)
    keep = {sm.DEFAULT_TABLE[kind], sm.table_for_default_geometry(kind, config)}
    out = []
    for name, items in per_table.items():
        out.append(LayerData(kind, name, layer_title(kind, name), specs, items, dominant_srid(r.geom for r in items),
                             geom_editable=geom_editable, table=name, visible=bool(items) or name in keep,
                             **layer_kwargs))
    return out


def concept_rows(bundle: ProjectBundle) -> List[Dict[str, Any]]:
    """Lignes de la table `concept` : libellés des listes de chaque table du modèle (type compris)."""
    sources: Dict[str, List] = {}
    for ld in bundle.layers:
        if not ld.rows:
            continue
        t = sm.ENTITY_TABLES.get(ld.table)
        cols = []
        for s in ld.specs:
            code = s.vocab_code
            if s.kind != CHOICE or not code or code in (VOCAB_STATUS, VOCAB_RU) or code not in bundle.vocab_concepts:
                continue
            column = t.typ_col if (t is not None and s.binding == BINDING_TYPE) else s.name
            cols.append((column, code, bundle.vocab_concepts[code]))
        sources[ld.table] = cols
    return sm.build_concepts(sources, bundle.author)
