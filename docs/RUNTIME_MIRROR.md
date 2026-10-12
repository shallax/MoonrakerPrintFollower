# Optional runtime mirror

The `v5.4.0` release hosts every optional download selected by the plugin.
The tag workflow stages the pinned bytes, uploads them to a draft release and
checks GitHub's stored size and digest before publication. The public release
requires the corresponding-source and notices companion plus a full direct-URL
download check. Binary/model payloads stay outside the plugin package and Git
history.

## Inventory

The current pinned matrices contain 27 selectable assets (1,045,150,482 bytes,
about 997 MiB) before source archives and notices. The earlier CAD manifest
also carried 20 wheels for Python ABIs the STEP helper never selects; those
entries have been removed.
An individual installation only downloads its selected platform's optional
assets.

| Distribution | Assets | Total MiB | Largest MiB |
| --- | ---: | ---: | ---: |
| Obico ONNX model | 1 | 192.9 | 192.9 |
| ONNX Runtime 1.23.2 | 15 | 235.8 | 18.3 |
| CAD reader and proxy 7.9.3.1.1 | 6 | 397.5 | 150.6 |
| Standalone CPython 3.12.15 helper | 5 | 170.6 | 62.6 |

Pins live in `mpf/detection/DetectionAssets.py`,
`mpf/toolhead/cad-runtime.json` and `mpf/toolhead/helper-runtime.json`.

The initial staging audit on 7 October 2026 downloaded all 47 distributions
and verified their existing size/SHA-256 pins (2,108,829,477 bytes total).
The CAD reader wheels declare Apache-2.0 in metadata but contain no licence or
notice files. Their release needs an assembled notices/source companion. ONNX
Runtime includes its licence and ThirdPartyNotices; the Python helpers retain
Python/pip notices, which do not alone describe every linked native dependency.

The active set's staging is reproducible with `python3 tools/stage_runtime_mirror.py
/path/outside/checkout`. The tool reads the committed pins, checks the PyPI
index against all 15 ONNX Runtime wheel hashes, downloads the original 27
payloads, verifies every byte and writes `manifest.json`. `--verify-only`
rechecks an existing staging directory without contacting upstream hosts.
For later releases, `--source-release v5.4.0` copies and verifies the exact
bytes from this plugin's release rather than fetching them from upstream.

On 12 October 2026 the full 47-pin matrix was reverified; the publication set
was narrowed to the 27 selectable assets. Source
archives for Obico (release commit `6f938964`), ONNX Runtime v1.23.2, OCP
7.9.3.1.1, OCCT V7_9_3, ocp-build-system v7.9.3.1.1 and
python-build-standalone 20261003 were retrieved for the companion audit.
These archives do not yet form a complete corresponding-source bundle: the CAD
wheels contain bundled native libraries without their licence files or exact
source/build records. The Windows release assets are derived from upstream
archives by removing Microsoft's bundled C++ runtime DLLs and repairing the
CAD import names to use the system runtime. The original source sizes and hashes,
derived output pins and transformation script are committed. The release needs
the completed CAD source/notice companion before publication.

## Hosting and migration

Use assets attached to the public `v5.4.0` plugin release in
`shallax/MoonrakerPrintFollower`. The plugin constructs direct URLs from its
packaged version; the normal install path uses neither the GitHub API nor the
PyPI index. If a release download fails, the plugin tries the original pinned
source. ONNX Runtime resolves its original file through PyPI's version index
only during that fallback and verifies the index size and SHA-256 before
downloading. If both hosts fail, installation reports an error.
Keep payloads outside Git history and retain pinned filenames, sizes and SHA-256
hashes. Windows outputs are documented DLL-free derivatives. Each later plugin release that retains these
optional features must attach the same pinned assets to its own version tag.

[GitHub's Release limits](https://docs.github.com/en/repositories/releasing-projects-on-github/about-releases)
allow 1,000 assets per release, each under 2 GiB, with no stated total-release
size or bandwidth limit. The pinned payload matrix fits these limits.
Use public download URLs without API discovery or an account requirement.
Workflow artifacts expire and require repository access; they are unsuitable
as the persistent runtime download endpoint.

Before publishing the draft release, attach the source/notices companion and
run `python tools/verify_runtime_release.py v5.4.0 --require-companion`.
After publication, run the same command with `--public-downloads` to GET and
hash every direct URL, including encoded `+` in Python helper names. The plugin
resolves each pinned ONNX wheel directly on the normal release path.
HTTPS-only redirects,
cancellation, archive checks and exact content checks remain. The detection
cache version is unchanged. The CAD cache hashes the recorded source URL for
identity only, preserving existing verified installations without a redownload;
network downloads try the release URL first. On Windows, an original-source
fallback removes the Microsoft DLLs and verifies the resulting derivative
against the published pin before extraction. The Windows STEP path checks the system
Visual C++ x64 runtime and links to Microsoft's official installer if needed;
Microsoft DLLs are absent from the release assets.

Users retain optional, platform-specific downloads and local caching. Normal
installs no longer depend on the original hosts. Measure redirects, cancellation and
installation on each supported platform during release validation.

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
are obtained from Microsoft when missing.
Do not mark the bundle complete until these materials match the actual pinned
distribution contents.
