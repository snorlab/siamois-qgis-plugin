import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from siamois_qgis.core import loader, spatial_model as sm  # noqa: E402
from tests.test_core import FakeClient  # noqa: E402

POLY = {"type": "Polygon", "coordinates": []}
MPOLY = {"type": "MultiPolygon", "coordinates": []}
LINE = {"type": "LineString", "coordinates": []}
PT = {"type": "Point", "coordinates": [1, 2]}


class ChooseTableTests(unittest.TestCase):
    def setUp(self):
        self.cfg = sm.SpatialConfig()

    def test_geometry_decides(self):
        self.assertEqual(sm.choose_table(sm.KIND_RU, POLY, None, self.cfg), "uo_poly")
        self.assertEqual(sm.choose_table(sm.KIND_RU, MPOLY, None, self.cfg), "uo_poly")
        self.assertEqual(sm.choose_table(sm.KIND_RU, LINE, None, self.cfg), "ut_ligne")
        self.assertEqual(sm.choose_table(sm.KIND_RU, PT, None, self.cfg), "ut_point")

    def test_default_geometry_when_no_geometry(self):
        self.assertEqual(sm.choose_table(sm.KIND_RU, None, None, self.cfg), "uo_poly")
        self.assertEqual(sm.choose_table(sm.KIND_FIND, None, None, self.cfg), "uo_point")
        self.cfg.default_geometry[sm.KIND_RU] = sm.POINT
        self.assertEqual(sm.choose_table(sm.KIND_RU, None, None, self.cfg), "ut_point")

    def test_find_always_uo_point(self):
        self.assertEqual(sm.choose_table(sm.KIND_FIND, PT, "x", self.cfg), "uo_point")
        self.cfg.default_geometry[sm.KIND_FIND] = sm.LINE  # impossible pour du mobilier : table naturelle
        self.assertEqual(sm.choose_table(sm.KIND_FIND, None, None, self.cfg), "uo_point")

    def test_type_mapping(self):
        self.cfg.type_tables = {"Bâtiment": "ur_poly", "Coupe": "ut_ligne"}
        self.assertEqual(sm.choose_table(sm.KIND_RU, POLY, "Bâtiment", self.cfg), "ur_poly")
        self.assertEqual(sm.choose_table(sm.KIND_RU, None, "Bâtiment", self.cfg), "ur_poly")
        # géométrie incompatible avec la table du type : la géométrie réelle l'emporte
        self.assertEqual(sm.choose_table(sm.KIND_RU, PT, "Bâtiment", self.cfg), "ut_point")
        self.assertEqual(sm.choose_table(sm.KIND_RU, LINE, "Coupe", self.cfg), "ut_ligne")
        # uo_point est réservée au mobilier
        self.cfg.type_tables = {"Fait": "uo_point"}
        self.assertEqual(sm.choose_table(sm.KIND_RU, PT, "Fait", self.cfg), "ut_point")

    def test_config_json(self):
        cfg = sm.SpatialConfig({"A": "ur_poly", "B": "bidon", "C": sm.AUTO})
        back = sm.SpatialConfig.from_json(cfg.to_json())
        self.assertEqual(back.type_tables, {"A": "ur_poly", "C": sm.AUTO})  # table inconnue écartée
        self.assertEqual(sm.SpatialConfig.from_json("pas du json").default_geometry, sm.DEFAULT_GEOMETRY)


class ColumnsTests(unittest.TestCase):
    def test_entity_columns_follow_the_model(self):
        names = lambda t: [c for c, _ in sm.core_columns(sm.ENTITY_TABLES[t])]
        self.assertEqual(names("uo_poly"), ["idprojet", "numuopoly", "typuopoly", "table_siamois", "interpret",
                                            "datedebut", "datefin", "com", "autsaisie", "datesaisie", "metacquis"])
        self.assertEqual(names("ut_point"), ["idprojet", "numutpt", "typutpt", "table_siamois", "com", "autsaisie",
                                             "datesaisie", "metacquis"])
        self.assertEqual(names("ud_ligne"), ["idprojet", "numudlgn", "typudlgn", "table_siamois", "com", "autsaisie",
                                             "datesaisie"])
        self.assertEqual(sm.ENTITY_TABLES["ur_poly"].id_col, "idurpoly")

    def test_core_values(self):
        v = sm.core_values(sm.ENTITY_TABLES["uo_point"], sm.KIND_FIND, "M-5", "isolat", "Marie", "2026-10-30")
        self.assertEqual(v, {"idprojet": 1, "numuopt": "M-5", "typuopt": "isolat", "table_siamois": "Mobilier",
                             "autsaisie": "Marie", "datesaisie": "2026-10-30"})

    def test_link_tables(self):
        self.assertEqual([c for c, _ in sm.link_columns("lien_uopoly_urpoly")][:2], ["iduopoly", "idurpoly"])
        self.assertEqual(len(sm.LINK_TABLES), 7)

    def test_concepts_unique(self):
        rows = sm.build_concepts({"uo_poly": [("Type", "SIARU.TYPE", [{"id": 10, "resolvedLabel": "Fait"},
                                                                       {"id": 11, "resolvedLabel": "Fait"}])]},
                                 "Marie", "2026-10-30")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["siamois_code_vocabulaire"], "SIARU.TYPE")
        self.assertEqual(rows[0]["nom_table"], "uo_poly")


class BundleTests(unittest.TestCase):
    def test_dispatch_uses_geometry_then_type_mapping(self):
        b = loader.load_project(FakeClient(), 1, 7)
        self.assertEqual([l.name for l in b.layers_of(loader.KIND_FIND)], ["uo_point"])
        self.assertEqual(len(b.layer("ut_point").rows), 1)   # UE ponctuelle -> ut_point
        self.assertEqual(len(b.layer("uo_point").rows), 1)   # mobilier -> uo_point
        self.assertEqual(b.count(loader.KIND_RU), 1)
        visible = {l.name for l in b.layers if l.visible}
        self.assertEqual(visible, {"projet", "ut_point", "uo_poly", "uo_point"})  # non vides + tables par défaut

    def test_type_mapping_cannot_override_geometry(self):
        cfg = sm.SpatialConfig({"Bâtiment": "ur_poly"})
        b = loader.load_project(FakeClient(), 1, 7, spatial=cfg)
        self.assertEqual(len(b.layer("ut_point").rows), 1)  # la géométrie (point) l'emporte
        self.assertEqual(len(b.layer("ur_poly").rows), 0)

    def test_concepts_from_bundle(self):
        b = loader.load_project(FakeClient(), 1, 7)
        rows = loader.concept_rows(b)
        self.assertIn(("ut_point", "typutpt", "Bâtiment"), {(r["nom_table"], r["nom_colonne"], r["label"]) for r in rows})
        self.assertTrue(all(r["siamois_code_vocabulaire"] for r in rows))


if __name__ == "__main__":
    unittest.main()
