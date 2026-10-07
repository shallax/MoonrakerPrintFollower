Reproducible toolhead lighting showcase
=====================================

Copyright Voron Design and contributors. The upstream Stealthburner and
Voron Design Cube assets are GPL-3.0; complete upstream licences are beside
this file. Source URLs, pinned revisions and SHA-256 digests are recorded in
provenance.json. These assets are test/capture inputs, not plugin payloads.

stealthburner.npz is a lossless compressed copy of the production STEP
reader's mesh: triangles, CAD colours, and surface IDs. The source is the
unmodified Stealthburner_CW2_Assembly.step at the pinned upstream revision.
It was converted with the production StepWorker/OCP 7.9.3.1.1 path, then
stored with numpy.savez_compressed. The original MPFHEAD2 mesh digest is
recorded so repacking cannot silently alter geometry or face identities.
The corresponding source STEP is available at the exact URL in provenance.

lighting.json retains the selected five-light configuration (two amber
nozzle LEDs, three purple logo LEDs), including brightness, outward normal,
paint choice and surface IDs. No machine identifiers, connection details,
or user profile are included. An empty tip selects production automatic
nozzle detection, just as in the user's configuration.

Voron_Design_Cube_v7.stl is unmodified upstream geometry. capture_toolhead.py
sections it at 0.2 mm to illustrate 90 deposited contour layers (18 of
30 mm height). This is a fixed print illustration, not printable G-code:
it contains the outer/inner contours and logo recesses, without slicer
infill or machine commands. The nozzle is placed on the right outer wall.

The normal make generate_screenshots / make verify_captures paths render
these fixtures. No network, CAD download, live Cura application, printer,
or local settings are used during generation. Production head GLSL and
production scene-lighting lightSurface() are compiled directly. The
receiver base colour is a neutral fixture palette; it is not a clone of
Cura's SimulationView. On Linux, pinned Mesa llvmpipe/EGL supplies canonical
pixels with fixed SSE2/128-bit arithmetic for reproducible CI captures; native hosts render a smoke
capture with their GPU driver. This does not change the plugin renderer.
