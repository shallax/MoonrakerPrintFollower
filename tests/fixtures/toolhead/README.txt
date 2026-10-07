Synthetic toolhead import fixtures

These two STEP assemblies were constructed for this repository's tests and
carry the repository's GPL-3.0 licence. They are not Voron CAD assets.

Both represent the same millimetre geometry. assembly-inch.step declares
inches, so import must normalise its bounds back to millimetres.

The assembly contains two placed 2x3x4 mm boxes with blue/green instance
colours and an exact-black face, plus a yellow annular nozzle at the origin.
Expected maximum bounds are (22,3,9) mm. Nozzle minimum Z is zero, and its
automatic tip is centred at (0,0,0), despite the central hole. Conversion
produces 232 triangles and four colours with the pinned meshing settings.

The large Stealthburner_CW2_Assembly.step supplied by the author was tested
from /tmp/mpf and is not distributed with this repository or its packages.
