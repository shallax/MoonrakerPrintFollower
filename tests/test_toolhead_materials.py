"""Authored material evidence, unknown fallbacks and alpha-independent presets."""
import unittest
from unittest.mock import patch
import numpy as np

from mpf.geometry.ToolheadGeometry import ToolheadMaterial
from mpf.geometry.ToolheadMaterials import UNKNOWN, material_parameters, material_profile, surface_detail, MATERIAL_TYPES, PROFILES, material_overrides, painted_materials, finish_uniforms, type_colour
from tests.test_toolhead_metadata import annotated_mesh


class ToolheadMaterialTests(unittest.TestCase):
    def profile(self, name, source="step-material", description=""):
        return material_profile(ToolheadMaterial(name, description, source))

    def test_occurrence_finish_supplements_but_never_replaces_physical_evidence(self):
        material = ToolheadMaterial('ABS', '', 'step-material')
        polished = material_profile(material, occurrence_name='Glass silver polished housing')
        self.assertEqual(polished.kind, 'abs')
        self.assertEqual(polished.roughness, .18)
        self.assertEqual(polished.metalness, 0)
        authored = ToolheadMaterial('ABS', 'Rough surface', 'step-material')
        self.assertEqual(material_profile(authored, occurrence_name='polished housing').roughness, .85)
        from mpf.geometry.ToolheadGeometry import mesh_metadata, mesh_from_arrays
        mesh = annotated_mesh();metadata = mesh_metadata(mesh)
        metadata['bodies'][0]['name'] = 'polished housing'
        mesh = mesh_from_arrays(mesh.triangles, mesh.colours, mesh.surfaces, material_ids=mesh.material_ids, body_ids=mesh.body_ids, metadata=metadata)
        values = material_parameters(mesh)
        self.assertAlmostEqual(float(values[0,0]), .18, places=6)
        self.assertEqual(float(values[0,3]), MATERIAL_TYPES.index('abs'))
        automatic = material_parameters(mesh, {'7':'automatic'}, {'0':'metal'})
        np.testing.assert_array_equal(automatic[0], values[0])
        np.testing.assert_allclose(mesh.colours[:,3], [1,.35], atol=1e-7)

    def test_unused_finish_body_does_not_reclassify_material_body_pairs(self):
        from mpf.geometry.ToolheadGeometry import mesh_metadata, mesh_from_arrays
        mesh=annotated_mesh();metadata=mesh_metadata(mesh)
        metadata['bodies'].append(dict(name='unused polished part',source='step-name',centre=None,axis=None))
        mesh=mesh_from_arrays(mesh.triangles,mesh.colours,mesh.surfaces,material_ids=mesh.material_ids,body_ids=mesh.body_ids,metadata=metadata)
        with patch('mpf.geometry.ToolheadMaterials.material_profile',wraps=material_profile) as classifier:
            parameters=material_parameters(mesh)
        self.assertEqual(classifier.call_count,len(mesh.materials))
        self.assertAlmostEqual(float(parameters[0,0]),PROFILES['abs'].roughness,places=6)

    def test_physical_material_and_clear_part_suffixes_get_conservative_presets(self):
        for name in ("ABS", "ASA", "PETG", "PLA", "Polycarbonate", "Nylon", "PA12", "TPU", "PEI", "PEEK", "PTFE"):
            with self.subTest(name=name):
                profile = self.profile(name)
                self.assertEqual((profile.kind, profile.metalness, profile.grain), (PROFILES[profile.kind].kind, 0, PROFILES[profile.kind].grain))
        self.assertEqual(self.profile("FanCover_ABS", "step-name").kind, "abs")
        self.assertEqual(self.profile("PC").kind, "pc")
        self.assertEqual(self.profile("PC", "step-name").kind, "pc")
        for name in ("Aluminium 6061", "Aluminum", "Stainless steel", "Brass", "Copper", "Titanium", "Bronze", "Nickel"):
            with self.subTest(name=name):
                profile = self.profile(name)
                self.assertEqual((profile.kind, profile.metalness, profile.grain), ("metal", 1, 0))
                self.assertEqual(profile.roughness, .35)

    def test_finish_is_used_only_when_supported_by_annotations(self):
        self.assertEqual(self.profile("Brass", description="Polished finish").roughness, .12)
        self.assertEqual(self.profile("Steel blasted").roughness, .55)
        self.assertEqual(self.profile("Copper sandblasted").roughness, .55)
        self.assertEqual(self.profile("Glass").roughness, .08)
        self.assertEqual(self.profile("Glass").grain, 0)

    def test_unknown_ambiguous_and_colour_like_names_do_not_become_metals(self):
        for name, source in (("Aluminium", "unknown"), ("Stealthburner", "step-name"),
                             ("SteelBlue", "step-name"), ("copperhead", "step-name"),
                             ("glassy", "step-material"), ("ABS steel composite", "step-material"),
 ("Metal housing", "step-name")):
            with self.subTest(name=name): self.assertIs(self.profile(name, source), UNKNOWN)

    def test_surface_strength_is_bounded_and_corrupt_settings_do_not_inject_nan(self):
        for value in (None, "0.5", True, float("nan"), float("inf"), 10**400):
            self.assertEqual(surface_detail(value), .35)
        self.assertEqual(surface_detail(-1), 0)
        self.assertEqual(surface_detail(2), 1)
        self.assertEqual(surface_detail(.125), .125)

    def test_parameters_preserve_triangle_groups_without_modifying_colour_or_alpha(self):
        mesh = annotated_mesh()
        before = mesh.colours.copy()
        parameters = material_parameters(mesh)
        np.testing.assert_allclose(parameters, [[.58, 0, .8, 8], [.35, 1, 0, 3]])
        np.testing.assert_array_equal(mesh.colours, before)

    def test_common_polymer_and_finish_mapping_does_not_require_face_setup(self):
        for name, kind in (("ABS Plastic", "abs"), ("Duct_PETG", "petg"), ("PLA", "pla"),
                           ("ASA", "asa"), ("Polyamide", "nylon"), ("PA11", "nylon"),
                           ("PA6", "nylon"), ("TPU", "tpu"), ("Polypropylene", "pp"),
                           ("PTFE", "ptfe"), ("POM", "pom")):
            self.assertEqual(self.profile(name).kind, kind)
        self.assertGreater(self.profile("ABS").roughness, self.profile("PETG").roughness)
        self.assertNotEqual(self.profile("ABS").reflectivity, self.profile("PC").reflectivity)
        self.assertEqual(self.profile("PETG matte").roughness, .65)
        self.assertEqual(self.profile("ABS smooth glossy").roughness, .18)
        self.assertEqual(self.profile("ABS smooth matte").grain, .15)
        self.assertEqual(self.profile("Glossy ABS", "step-name").roughness, .18)
        self.assertIs(self.profile("PC motherboard", "step-name"), UNKNOWN)
        self.assertIs(self.profile("ABS PETG"), UNKNOWN)

    def test_filled_polymers_are_neither_glass_nor_metal_and_keep_alpha(self):
        for name, kind in (("PC-CF", "pc-cf"), ("PCCF", "pc-cf"), ("PA11-CF", "pa-cf"),
                           ("PETG-CF", "petg-cf"), ("PLA-CF", "pla-cf"),
                           ("Carbon fibre", "carbon-fibre"), ("CFRP", "carbon-fibre"),
                           ("FanCover_PC-CF_Matte", "pc-cf")):
            profile = self.profile(name, "step-name")
            self.assertEqual(profile.kind, kind)
            self.assertEqual(profile.metalness, 0)
            self.assertGreater(profile.grain, 1)
        for name in ("PA6-GF30", "Glass filled nylon", "Nylon glass fibre"):
            profile = self.profile(name)
            self.assertEqual(profile.kind, "nylon")
            self.assertEqual((profile.roughness, profile.grain), (.7, 1.5))
        self.assertIs(self.profile("CF", "step-name"), UNKNOWN)
        self.assertIs(self.profile("Glass ABS steel composite"), UNKNOWN)

    def test_finish_validation_and_banks_keep_each_profile_independent(self):
        bad = {"abs": {"roughness": .7, "reflectivity": .12}, "petg": {"roughness": .2},
               "bogus": {"roughness": .9}, "metal": {"roughness": True, "reflectivity": float("nan")},
               "glass": {"roughness": "0.1"}, "pla": [], "pc": {"roughness": 10**400}}
        checked = material_overrides(bad)
        self.assertEqual(checked, {"abs": {"roughness": .7, "reflectivity": .12}, "petg": {"roughness": .2}})
        self.assertEqual(material_overrides(None), {})
        uniforms = finish_uniforms(checked)
        self.assertEqual(uniforms["u_materialRoughness2"], [.7, -1, .2, -1])
        self.assertEqual(uniforms["u_materialReflectivity2"][0], .12)
        self.assertEqual(uniforms["u_materialReflectivity2"][2], PROFILES["petg"].reflectivity)
        self.assertEqual(finish_uniforms({})["u_materialRoughness2"], [-1]*4)
        for kind in MATERIAL_TYPES: self.assertEqual(len(type_colour(kind)), 3)
        self.assertNotEqual(type_colour("glass"), type_colour("plastic"))
        self.assertNotEqual(type_colour("metal"), type_colour("carbon-fibre"))

    def test_face_paint_is_asset_fenced_and_changes_no_original_rgba(self):
        mesh = annotated_mesh(); before = mesh.colours.copy()
        surface = int(mesh.surfaces[0])
        assignments = {str(surface): "petg", "9999": "metal", "x": "glass", True: "metal", -1: "glass", "2": []}
        self.assertEqual(painted_materials(assignments, mesh), {str(surface): "petg"})
        parameters = material_parameters(mesh, assignments)
        self.assertEqual(parameters[0, 3], MATERIAL_TYPES.index("petg"))
        np.testing.assert_array_equal(mesh.colours, before)
        self.assertEqual(painted_materials(None), {})
        self.assertEqual(len(painted_materials({str(i): "abs" for i in range(3000)})), 2048)
        self.assertEqual(painted_materials({"16777216": "abs", "100000000": "abs"}), {})


if __name__ == "__main__": unittest.main()
