# Toolhead appearance presets

STEP physical material names and descriptions supply the first classification.
When those are absent, clear component-name tokens/suffixes can identify a
polymer or metal. Recognized finish words in a body name can supplement a
physical material with no recorded finish; physical finish annotations win.
Only finish changes come from this supplement, never material identity. RGB alone never identifies a material. Missing/ambiguous
evidence remains unidentified. Original CAD colour and alpha remain unchanged;
recognizing glass or a polymer never invents transparency.

The selectable profiles include smooth/glossy/matte/rough generic plastics,
ABS, ASA, PETG, PLA, PC, nylon/PA, TPU, PP, PEEK, PEI, PTFE, POM, carbon-fibre
composite and PC/PA/PETG/PLA-CF, plus glass, metal and unidentified material.
Every profile has independent roughness and reflectivity settings. Automatic
roughness retains the individual authored finish rather than flattening all
materials of a profile to one value. Body and face assignments are optional,
bounded, asset-specific overrides. Material-paint colours are temporary identification
colours; leaving paint mode restores the CAD palette with any explicit colour overrides.

## Evidence and numerical limits

Preview's View Options places **Enable reflections** beside **Enable lighting**.
Disabling reflections stops environment capture and releases its owned maps,
while retaining the model's colours, finish settings and transparency. The choice
is saved like the other global Preview display preferences. Lighting off disables
the reflection checkbox and retains its checked choice for when lighting returns.

Manufacturer descriptions support the direction of the defaults, not precise
PBR roughness measurements:

- [Prusa PETG](https://help.prusa3d.com/article/petg_2059) describes ordinary PETG as glossy.
- [Polymaker ABS/ASA](https://wiki.polymaker.com/polymaker-products/polymaker-filaments/prime-materials/abs-and-asa) describes ASA's matte finish relative to ABS.
- [Prusament PC-CF](https://prusament.com/materials/prusament-pc-blend-carbon-fiber/) and [its introduction](https://blog.prusa3d.com/prusament_pc_blend_carbon_fiber_mechanical_resistance_pccf_51028/) describe matte, carbon-filled PC.
- [Polymaker printing speed](https://wiki.polymaker.com/the-basics/fun-3d-printing-facts/printing-fast-matte-surface-finish) explains why printing conditions can change gloss.
- [Covestro Makrolon 3107](https://solutions.covestro.com/en/products/makrolon/makrolon-3107_000000000057534595) reports PC refractive index 1.586.

Roughness and grain strengths are adjustable **visual estimates**, not material
certification. Polymer formulations, processing, coatings and surface finishing
change appearance. Explicit matte/glossy/polished finish evidence takes precedence
over the polymer's default; smooth controls grain separately. Conflicting
polymer identities remain unknown. CF/GF fillers are resolved before glass;
filled polymers remain nonmetallic. Printed chopped-fibre composites receive
fine filtered grain, not an invented woven-cloth texture.

Reflectivity means normal-incidence reflection F0, independent of roughness.
PC's approximate default uses ((1.586 - 1)/(1.586 + 1))² = 0.05135.
Other profile F0 values are conservative rendering approximations, not claimed
measurements of the STEP part. Metal uses the CAD colour for tinted reflection;
its reflectivity adjustment scales that colour. The retained STEP opacity is
independent of both controls. See [Khronos BRDF](https://registry.khronos.org/glTF/specs/2.0/glTF-2.0.html#appendix-b-brdf-implementation).

## Safe demonstration captures

The October 2026 development demo imports the pinned screenshot assembly through
the production STEP reader. Its annotations identify ABS, steel, aluminium,
brass and nylon. The synthetic Voron cube contours and bed are rendered into a
512px six-face colour/depth environment map; the toolhead samples that map with
the production shader's bounded local-depth lookup. A flat mirror reference and
moving-toolhead diagnostic make reflection angles visible. A single probe cannot
reconstruct hidden surfaces, and finite sampling can miss thin geometry.
No live Cura profile, printer connection or machine command is involved.

An explicitly exaggerated demonstration uses ABS roughness 0.06/F0 0.75,
metal roughness 0.05/F0 scale 1, and nylon roughness 0.08/F0 0.5. These are
**demonstration overrides**, not realistic filament defaults and not saved to
any user's configuration. Normal profiles stay unchanged.

## Editing selected parts

Bodies and CAD faces share an additive selection. Assign a material type or
adjust colour, roughness, reflectivity and opacity independently; each readout reports
automatic, body, face or mixed provenance. Face overrides take precedence over
body overrides, which take precedence over the imported material/profile.
**Clear body / face selections**, beside the selection summary, deselects both
without changing any appearance edits.
Automatic finish resets only that selected property, including bypassing a body
override on a selected face. Restore imported transparency restores its original
STEP alpha independently of material and finish. Whole-body changes clear the
same property's descendant face overrides before applying explicitly selected
faces. Each sparse body/face map permits at most 2,048 entries; an oversized edit
is refused atomically.

Invisible geometry remains selectable through the body-name list or faint edit
ghosts. Cyan selection and material-type highlights are temporary and exclusive;
normal rendering uses the imported CAD colours plus any explicit colour overrides.
Choose colour opens an RGB picker for the selected parts; transparency stays
independent. Restore imported colour restores each original face colour, even
when its body has a colour override. Cancelling the picker changes nothing. Done keeps the printer-settings
draft; Save persists it, while Cancel restores all appearance properties.
