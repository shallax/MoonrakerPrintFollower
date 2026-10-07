# Optional runtime mirror

The hosting migration remains follow-up work after the 5.3.0 Toolhead changes.
Downloads still use their existing upstream URLs; no runtime mirror release
has been published and no binary/model payloads are included in this branch.

## Inventory

The current pinned matrices contain 47 binary/model assets, about 1.96 GiB
in total before source archives and notices. An individual installation only
downloads its selected platform's optional assets.

| Distribution | Assets | Total MiB | Largest MiB |
| --- | ---: | ---: | ---: |
| Obico ONNX model | 1 | 192.9 | 192.9 |
| ONNX Runtime 1.23.2 | 15 | 235.8 | 18.3 |
| CAD reader and proxy 7.9.3.1.1 | 26 | 1453.5 | 64.5 |
| Standalone CPython 3.12.15 helper | 5 | 129.0 | 32.7 |

Pins live in `mpf/detection/DetectionAssets.py`,
`mpf/toolhead/cad-runtime.json` and `mpf/toolhead/helper-runtime.json`.

The initial staging audit on 7 October 2026 downloaded all 47 distributions
and verified their existing size/SHA-256 pins (2,108,829,477 bytes total).
The CAD reader wheels declare Apache-2.0 in metadata but contain no licence or
notice files. Their release therefore needs an assembled notices/source
companion. ONNX Runtime includes its licence and ThirdPartyNotices; the Python
helpers retain Python/pip notices, which do not alone describe every linked
native dependency. Staging is complete; redistribution companions and the
production URL/cache migration remain to be completed.

## Hosting and migration

Use versioned public Release assets in `shallax/MoonrakerPrintFollower`, with a
runtime tag distinct from the plugin's `v*` tags. The existing plugin release
workflow triggers on `v*`; a runtime release must not invoke it or replace the
latest plugin release. Keep payloads outside Git history and retain the exact
upstream bytes, filenames, sizes and SHA-256 pins.

[GitHub's Release limits](https://docs.github.com/en/repositories/releasing-projects-on-github/about-releases)
allow 1,000 assets per release, each under 2 GiB, with no stated total-release
size or bandwidth limit. The pinned payload matrix fits these limits.
Use public download URLs without API discovery or an account requirement.
Workflow artifacts expire and require repository access; they are unsuitable
as the persistent runtime download endpoint.

Before changing production URLs, publish and download-verify the mirror,
including its notices and source assets. ONNX Runtime can resolve its pinned
wheel directly rather than making a separate PyPI index request. Preserve
HTTPS-only redirects, cancellation, archive checks and exact content checks.
The detection cache version need not change. The CAD cache currently hashes
the entire asset record, including its URL: adapt its identity or explicitly
adopt the verified old cache so changing the host does not force a redownload.

Users retain optional, platform-specific downloads and local caching. This
removes reliance on several upstream hosts; it does not establish that GitHub
downloads are faster in every region. Measure redirects, cancellation and
installation on each supported platform before calling the migration done.

## Redistribution bundle

The licences grant redistribution rights subject to their conditions. Retain
each distribution's licence and notices rather than treating the plugin's GPL
as a replacement. The component inventory is in `THIRDPARTYSOFTWARE.md`.

For applicable GPL/AGPL/LGPL components, provide the corresponding source and
build material for the exact binaries/weights being mirrored. The AGPL's
[section 6](https://github.com/TheSpaghettiDetective/obico-server/blob/release/LICENSE)
permits online distribution with equivalent source access and clear directions
beside the binary download; source need not be downloaded with the runtime.
Keeping source in the same runtime release also supports the intended
resilience against upstream disappearance.

The Obico bundle needs its applicable model/source and conversion material;
the CAD bundle needs the exact OCCT and included copyleft-library sources;
the Python helper needs its pinned build recipe, component sources and retained
notices. Preserve permissive-library notices too. Microsoft runtime components
have separate redistribution terms and must be checked against those terms.
Do not mark the bundle complete until these materials match the actual pinned
distribution contents.
