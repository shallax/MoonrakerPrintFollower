"""Conservative appearance presets from authored CAD evidence, never RGB."""
from __future__ import annotations

from dataclasses import dataclass, replace
import re
import math
from collections.abc import Mapping

import numpy as np


@dataclass(frozen=True)
class MaterialProfile:
    kind: str
    roughness: float
    metalness: float
    grain: float
    reflectivity: float = .04


DEFAULT_SURFACE_DETAIL = .35
# Stable type IDs are stored in the fourth vertex-material component. New
# types append; saved face assignments use strings, never ordinal positions.
MATERIAL_TYPES = ("unknown", "plastic", "glass", "metal", "plastic-glossy", "plastic-matte",
    "plastic-rough", "carbon-fibre", "abs", "asa", "petg", "pla", "pc", "nylon", "tpu", "pp",
    "peek", "pei", "ptfe", "pom", "pc-cf", "pa-cf", "petg-cf", "pla-cf")
MATERIAL_LABELS = ("Unidentified", "Plastic · smooth", "Glass", "Metal", "Plastic · glossy", "Plastic · matte",
    "Plastic · rough", "Carbon-fibre composite", "ABS", "ASA", "PETG", "PLA", "Polycarbonate (PC)",
    "Nylon (PA)", "TPU", "Polypropylene (PP)", "PEEK", "PEI", "PTFE", "Acetal (POM)",
    "PC-CF", "PA-CF", "PETG-CF", "PLA-CF")
MAX_PAINTED_FACES = 2048
# Roughness/grain are rendering estimates, not measured filament constants.
# F0 is normal-incidence reflection; pigment and STEP alpha stay independent.
# Research and precedence are documented in docs/TOOLHEAD_MATERIALS.md.
_DEFAULTS = ((.5, 0., 0., .04), (.45, 0., 1., .04), (.08, 0., 0., .04), (.35, 1., 0., 1.),
    (.18, 0., .15, .04), (.65, 0., .8, .04), (.85, 0., 1.5, .04), (.75, 0., 2., .05),
    (.58, 0., .8, .049), (.62, 0., .8, .049), (.25, 0., .3, .049), (.4, 0., .6, .041),
    (.3, 0., .3, .05135), (.5, 0., .8, .045), (.5, 0., .6, .045), (.4, 0., .4, .035),
    (.4, 0., .6, .045), (.45, 0., .6, .05), (.55, 0., .6, .026), (.3, 0., .4, .045),
    (.78, 0., 2., .05135), (.75, 0., 2., .045), (.7, 0., 2., .049), (.72, 0., 2., .041))
TYPE_PROFILES = tuple(MaterialProfile(kind, *values) for kind, values in zip(MATERIAL_TYPES, _DEFAULTS, strict=True))
PROFILES = dict(zip(MATERIAL_TYPES, TYPE_PROFILES, strict=True))
MATERIAL_BANKS = len(MATERIAL_TYPES)//4


def type_colour(kind):
    if kind == "glass": return (.18, .55, 1.)
    if kind == "metal": return (1., .7, .12)
    if kind == "carbon-fibre" or kind.endswith("-cf"): return (.3, .3, .35)
    if kind == "unknown": return (.55, .55, .55)
    return (1., 1., 1.)


def unit_value(value):
    if type(value) not in (int, float): return None
    try: value = float(value)
    except OverflowError: return None
    return value if math.isfinite(value) and 0 <= value <= 1 else None


def material_overrides(value):
    """Only supported types and two bounded numeric finish fields survive."""
    if not isinstance(value, Mapping): return {}
    result = {}
    for kind in MATERIAL_TYPES:
        row = value.get(kind)
        if not isinstance(row, Mapping): continue
        checked = {name: number for name in ("roughness", "reflectivity")
                   if (number := unit_value(row.get(name))) is not None}
        if checked: result[kind] = checked
    return result


def painted_materials(value, mesh=None, *, bodies=False):
    """Surface identity is tied to the current immutable asset, never RGB."""
    if not isinstance(value, Mapping): return {}
    result = {}
    for key, kind in value.items():
        if len(result) >= MAX_PAINTED_FACES: break
        if type(key) is int: surface = key
        elif type(key) is str and key.isascii() and key.isdecimal() and len(key) <= 8: surface = int(key)
        else: continue
        if not 0 <= surface < 16777216 or type(kind) is not str or kind not in (*MATERIAL_TYPES, "automatic"): continue
        if mesh is not None and surface not in (mesh.present_bodies if bodies else mesh.present_surfaces): continue
        result[str(surface)] = kind
    return result


def finish_uniforms(value):
    checked = material_overrides(value)
    # Banks avoid driver-dependent dynamic uniform-array indexing on legacy
    # OpenGL. Every type gets its own F0, even without a user override.
    result = {"u_materialOverridesEnabled": 1}
    for bank in range(MATERIAL_BANKS):
        kinds = MATERIAL_TYPES[bank*4:bank*4+4]
        result["u_materialRoughness"+str(bank)] = [checked.get(kind, {}).get("roughness", -1.) for kind in kinds]
        result["u_materialReflectivity"+str(bank)] = [checked.get(kind, {}).get("reflectivity", PROFILES[kind].reflectivity) for kind in kinds]
    return result


def surface_detail(value):
    """Corrupt settings fall back; finite numeric values clamp to the slider."""
    if type(value) not in (int, float): return DEFAULT_SURFACE_DETAIL
    try: value = float(value)
    except OverflowError: return DEFAULT_SURFACE_DETAIL
    if not math.isfinite(value): return DEFAULT_SURFACE_DETAIL
    return min(1., max(0., float(value)))


UNKNOWN = PROFILES["unknown"]
_POLYMERS = {"abs": "abs", "asa": "asa", "petg": "petg", "pla": "pla", "polycarbonate": "pc",
    "nylon": "nylon", "polyamide": "nylon", "pa": "nylon", "pa12": "nylon", "pa6": "nylon", "pa66": "nylon",
    "paht": "nylon", "tpu": "tpu", "polypropylene": "pp", "pei": "pei", "peek": "peek", "ptfe": "ptfe",
    "pom": "pom", "acetal": "pom", "plastic": "plastic"}
METALS = frozenset(("aluminium", "aluminum", "steel", "brass", "copper", "titanium", "bronze", "nickel"))


def material_profile(material, *, occurrence_name=""):
    if material.source == "unknown": return UNKNOWN
    # Physical annotations outrank component names in the import traversal.
    # Only whole tokens/explicit suffixes count: Copperhead is not copper.
    text = material.name.casefold()
    if material.source == "step-material": text += " " + material.description.casefold()
    finish_words = {"glossy", "polished", "smooth", "matte", "matt", "rough", "blasted", "sandblasted"}
    # An occurrence can describe a finish, never replace its physical material.
    # Existing physical finish evidence is stronger than a component name.
    if material.source == "step-material" and not set(re.findall(r"[a-z]+", text)) & finish_words:
        text += " " + " ".join(sorted(set(re.findall(r"[a-z]+", occurrence_name.casefold())) & finish_words))
    words = frozenset(re.findall(r"[a-z]+[0-9]*", text))
    polymers = {_POLYMERS[word] for word in words if word in _POLYMERS}
    if any(re.fullmatch(r"pa[0-9]+", word) for word in words): polymers.add("nylon")
    # Short PC/PP aliases are safe in explicit physical material annotations,
    # or as a delimiter-separated component suffix, not anywhere in a name.
    suffix = re.search(r"(?:^|[_ .-])(pc|pp)(?:[_ .-]cf[0-9]*)?(?:[_ .-](?:matte|matt|rough|smooth|glossy|polished))*$", material.name.casefold())
    for word in ("pc", "pp"):
        if word in words and (material.source == "step-material" or suffix and suffix[1] == word): polymers.add(word)
    if "pccf" in words: polymers.add("pc")
    carbon = ("carbon" in words and bool(words & {"fiber", "fibre", "fibers", "fibres"})) or "cfrp" in words or "pccf" in words
    carbon |= any(re.fullmatch(r"cf[0-9]*", word) for word in words) and bool(polymers)
    glass_filled = bool(polymers) and (("glass" in words and bool(words & {"fiber", "fibre", "filled", "reinforced"})) or any(re.fullmatch(r"gf[0-9]*", word) for word in words))
    metal, glass = bool(words & METALS), "glass" in words and not glass_filled
    if metal or glass:
        if polymers or carbon or metal and glass: return UNKNOWN
        profile = PROFILES["metal" if metal else "glass"]
        if metal: profile = replace(profile, roughness=.12 if "polished" in words else .55 if words & {"blasted", "sandblasted"} else .35)
        return profile
    if len(polymers) > 1: polymers.discard("plastic")
    if len(polymers) > 1: return UNKNOWN
    if not polymers and not carbon: return UNKNOWN
    kind = next(iter(polymers), "carbon-fibre")
    if carbon: kind = {"pc": "pc-cf", "nylon": "pa-cf", "petg": "petg-cf", "pla": "pla-cf"}.get(kind, "carbon-fibre")
    profile = PROFILES[kind]
    finishes = words & {"glossy", "polished", "smooth", "matte", "matt", "rough"}
    families = [bool(finishes & {"glossy", "polished"}), "smooth" in finishes and not finishes & {"glossy", "polished", "matte", "matt", "rough"}, bool(finishes & {"matte", "matt"}), "rough" in finishes]
    if sum(families) == 1:
        index = families.index(True)
        profile = replace(profile, roughness=(.18, .45, .65, .85)[index],
            grain=(.15, .5, .8, 1.5)[index] if not carbon else profile.grain)
    if "smooth" in finishes: profile = replace(profile, grain=.15)
    if glass_filled and not finishes: profile = replace(profile, roughness=.7, grain=1.5)
    return profile


def _occurrence_finish(kind, words):
    if kind == 'metal':
        return (.12 if 'polished' in words else .55 if words & {'blasted','sandblasted'} else -1., -1.)
    if kind in ('glass', 'unknown'): return (-1., -1.)
    carbon = kind == 'carbon-fibre' or kind.endswith('-cf')
    families = [bool(words & {'glossy','polished'}), 'smooth' in words and not words & {'glossy','polished','matte','matt','rough'}, bool(words & {'matte','matt'}), 'rough' in words]
    roughness, grain = -1., -1.
    if sum(families) == 1:
        index = families.index(True)
        roughness = (.18,.45,.65,.85)[index]
        if not carbon: grain = (.15,.5,.8,1.5)[index]
    if 'smooth' in words: grain = .15
    return roughness, grain


def material_parameters(mesh, painted=None, body_painted=None):
    """Resolve face > body > original classification, including face automatic."""
    table = np.asarray([(profile.roughness, profile.metalness, profile.grain, float(MATERIAL_TYPES.index(profile.kind)))
                        for profile in map(material_profile, mesh.materials)], dtype=np.float32)
    original = table[mesh.material_ids]
    # Bound finish work by the material types and canonical finish sets,
    # never the potentially million distinct material/body pairs.
    finish_words = {"glossy", "polished", "smooth", "matte", "matt", "rough", "blasted", "sandblasted"}
    words = [frozenset(re.findall(r"[a-z]+", body.name.casefold())) & finish_words for body in mesh.bodies]
    kinds = original[:, 3].astype(int)
    used = mesh.present_bodies if any(words) else ()
    groups = {wordset for index, wordset in enumerate(words) if index in used and wordset}
    if groups:
        ordered = [frozenset(), *sorted(groups, key=lambda value: tuple(sorted(value)))]
        identities = {wordset: index for index, wordset in enumerate(ordered)}
        body_groups = np.array([identities.get(wordset, 0) for wordset in words])
        eligible = np.array([material.source == 'step-material' and not
            set(re.findall(r"[a-z]+", (material.name+' '+material.description).casefold())) & finish_words for material in mesh.materials])
        changes = np.array([[_occurrence_finish(kind, wordset) for kind in MATERIAL_TYPES] for wordset in ordered], np.float32)
        values = changes[body_groups[mesh.body_ids], kinds]
        for column, source_column in ((0,0),(1,2)):
            mask = eligible[mesh.material_ids] & (values[:,column] >= 0)
            original[mask,source_column] = values[mask,column]
    result = original.copy()
    for identities, values in ((mesh.body_ids, painted_materials(body_painted, mesh, bodies=True)),
                               (mesh.surfaces, painted_materials(painted, mesh))):
        if not values: continue
        keys = np.asarray(sorted(map(int, values)), dtype=np.uint32)
        positions = np.minimum(np.searchsorted(keys, identities), len(keys)-1)
        mask = keys[positions] == identities
        types = [values[str(int(key))] for key in keys]
        automatic = np.array([kind == "automatic" for kind in types])
        paint = np.asarray([(profile.roughness, profile.metalness, profile.grain, float(MATERIAL_TYPES.index(profile.kind)))
                            for profile in (PROFILES.get(kind, UNKNOWN) for kind in types)], dtype=np.float32)
        reset = mask & automatic[positions]
        result[mask] = paint[positions[mask]]
        result[reset] = original[reset]
    return result


def local_finish_overrides(value, mesh=None, *, bodies=False):
    """Bounded target properties; explicit automatic bypasses body inheritance."""
    if not isinstance(value, Mapping): return {}
    result = {}
    for raw_key, row in value.items():
        if len(result) >= MAX_PAINTED_FACES: break
        valid = painted_materials({raw_key: "unknown"}, mesh, bodies=bodies)
        if not valid: continue
        key = next(iter(valid))
        if not isinstance(row, Mapping): continue
        checked = {}
        for field in ("roughness", "reflectivity"):
            raw = row.get(field)
            number = "automatic" if type(raw) is str and raw == "automatic" else unit_value(raw)
            if number is not None: checked[field] = number
        if checked: result[key] = checked
    return result


def local_finish_parameters(mesh, bodies=None, faces=None):
    result = np.full((len(mesh.triangles), 2), -1., dtype=np.float32)
    for identities, values in ((mesh.body_ids, local_finish_overrides(bodies, mesh, bodies=True)),
                               (mesh.surfaces, local_finish_overrides(faces, mesh))):
        if not values: continue
        keys = np.asarray(sorted(map(int, values)), dtype=np.uint32)
        positions = np.minimum(np.searchsorted(keys, identities), len(keys)-1)
        mask = keys[positions] == identities
        for column, field in enumerate(("roughness", "reflectivity")):
            present = np.array([field in values[str(int(key))] for key in keys])
            numbers = np.array([values[str(int(key))].get(field, -1.) for key in keys], dtype=object)
            numbers = np.array([-1. if raw == "automatic" else float(raw) for raw in numbers], dtype=np.float32)
            admitted = mask & present[positions]
            result[admitted, column] = numbers[positions[admitted]]
    return result


def resolved_finishes(mesh, painted=None, body_painted=None, profiles=None, bodies=None, faces=None, *, parameters=None):
    if parameters is None: parameters = material_parameters(mesh, painted, body_painted)
    overrides = material_overrides(profiles)
    roughness = np.array([overrides.get(kind, {}).get("roughness", -1.) for kind in MATERIAL_TYPES])
    reflectivity = np.array([overrides.get(kind, {}).get("reflectivity", PROFILES[kind].reflectivity) for kind in MATERIAL_TYPES])
    kinds = parameters[:, 3].astype(int)
    result = np.stack((np.where(roughness[kinds] >= 0, roughness[kinds], parameters[:, 0]), reflectivity[kinds]), axis=1)
    local = local_finish_parameters(mesh, bodies, faces)
    return np.where(local >= 0, local, result)
