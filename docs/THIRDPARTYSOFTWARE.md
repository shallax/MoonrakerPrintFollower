# Third-party software

Moonraker Print Follower is licensed **GPL-3.0-only**. Dependencies retain their
own licences; this document does not relicense them.

The optional STEP reader, Python helper, Obico model and ONNX Runtime are
downloaded directly from upstream after the user enables the relevant feature.
Their binaries and model weights are not included in the plugin package. The
installers preserve notices supplied in the downloaded archives. This index
links the upstream licence texts instead of duplicating those distributions.
The plugin's complete GPLv3 text is supplied in the package's `LICENSE` file.

## Optional downloads

| Software | Purpose and source | Licence |
| --- | --- | --- |
| Obico / The Spaghetti Detective failure-detection model | [Obico source](https://github.com/TheSpaghettiDetective/obico-server), including the [ONNX model reference](https://github.com/TheSpaghettiDetective/obico-server/blob/release/ml_api/model/model-weights.onnx.url) | [AGPL-3.0](https://github.com/TheSpaghettiDetective/obico-server/blob/release/LICENSE) |
| ONNX Runtime 1.23.2 | [Local CPU inference](https://github.com/microsoft/onnxruntime/tree/v1.23.2); includes its upstream third-party notices | [MIT](https://github.com/microsoft/onnxruntime/blob/v1.23.2/LICENSE) |
| cadquery-ocp-novtk and cadquery-ocp-proxy 7.9.3.1.1 | [OCP bindings](https://github.com/CadQuery/OCP/releases/tag/7.9.3.1.1), [wheel build sources](https://github.com/CadQuery/ocp-build-system/tree/v7.9.3.1.1) | [Apache-2.0](https://github.com/CadQuery/ocp-build-system/blob/v7.9.3.1.1/LICENSE) |
| Open CASCADE Technology 7.9.3 | [CAD geometry and STEP conversion](https://github.com/Open-Cascade-SAS/OCCT/tree/V7_9_3) | [LGPL-2.1](https://github.com/Open-Cascade-SAS/OCCT/blob/V7_9_3/LICENSE_LGPL_21.txt) with the [OCCT exception](https://github.com/Open-Cascade-SAS/OCCT/blob/V7_9_3/OCCT_LGPL_EXCEPTION.txt) |
| CPython 3.12.15, from python-build-standalone 20261003 | [Python source](https://www.python.org/ftp/python/3.12.15/), [standalone release and build sources](https://github.com/astral-sh/python-build-standalone/releases/tag/20261003) | [PSF-2.0 and historical Python notices](https://docs.python.org/3.12/license.html) |

The selected download versions, sizes and SHA-256 values are recorded in
`toolhead/cad-runtime.json`, `toolhead/helper-runtime.json` and
`detection/DetectionAssets.py`. The standalone interpreter's build tools use
MPL-2.0; that is not the licence of the interpreter or every bundled library.

## Runtime components, grouped by licence

The upstream CAD and Python distributions include the following components;
their presence and versions vary by operating system. pip and its vendored
libraries arrive with the Python helper but are not used to install the STEP
reader. Component copyright notices remain in their original upstream sources
and in the downloaded distributions where supplied.

| Software | Licence text / upstream notice |
| --- | --- |
| FreeImage; JBIG-KIT (GPL-2.0-or-later, selecting version 3) | [GPL-3.0](https://www.gnu.org/licenses/gpl-3.0.html); [FreeImage's GPLv3 option](https://freeimage.sourceforge.io/license.html), [JBIG-KIT source](https://www.cl.cam.ac.uk/~mgk25/jbigkit/) |
| LibRaw | [LGPL-2.1](https://github.com/LibRaw/LibRaw/blob/master/LICENSE.LGPL) |
| libgomp | [GPL-3.0 with GCC Runtime Library Exception 3.1](https://www.gnu.org/licenses/gcc-exception-3.1.html) |
| Lerc, OpenSSL 3; pip's CacheControl, distro, msgpack, packaging and requests | [Apache-2.0](https://www.apache.org/licenses/LICENSE-2.0); packaging also offers BSD-2-Clause |
| fmt, libdeflate, Little CMS, Expat, libffi, libXau, libxcb; pip, pkg_resources, platformdirs, pyproject-hooks, Rich, tomli, tomli-w, truststore and urllib3 | [MIT](https://opensource.org/license/mit); [pip's retained component notices](https://github.com/pypa/pip/tree/26.2.1/src/pip/_vendor) |
| Fontconfig | [Fontconfig permissive licence](https://gitlab.freedesktop.org/fontconfig/fontconfig/-/blob/main/COPYING) |
| RapidJSON | [MIT and included BSD notices](https://github.com/Tencent/rapidjson/blob/master/license.txt) |
| pybind11, OpenEXR / IlmBase, Imath, Zstandard, libuuid, libedit; pip's idna | [BSD-3-Clause](https://opensource.org/license/bsd-3-clause) |
| OpenJPEG, OpenJPH, JPEG XR, libmpdec / mpdecimal; pip's Pygments | [BSD-2-Clause](https://opensource.org/license/bsd-2-clause) |
| libwebp, webpmux, sharpyuv | [BSD-3-Clause](https://chromium.googlesource.com/webm/libwebp/+/refs/heads/main/COPYING) and [patent grant](https://chromium.googlesource.com/webm/libwebp/+/refs/heads/main/PATENTS) |
| libc++ | [Apache-2.0 with LLVM exception and retained legacy notices](https://github.com/llvm/llvm-project/blob/main/libcxx/LICENSE.TXT) |
| FreeType | [FreeType License (FTL)](https://freetype.org/license.html) |
| libjpeg-turbo / Independent JPEG Group code | [IJG, BSD-3-Clause and Zlib notices](https://github.com/libjpeg-turbo/libjpeg-turbo/blob/main/LICENSE.md) |
| libpng | [Libpng-2.0 and earlier retained notices](https://github.com/pnggroup/libpng/blob/libpng16/LICENSE) |
| libtiff | [libtiff permissive licence](https://gitlab.com/libtiff/libtiff/-/blob/master/LICENSE.md) |
| zlib | [Zlib licence](https://github.com/madler/zlib/blob/develop/LICENSE) |
| liblzma / XZ Utils | [0BSD and retained older public-domain notices](https://github.com/tukaani-project/xz/blob/master/COPYING) |
| bzip2 | [bzip2 licence](https://sourceware.org/git/?p=bzip2.git;a=blob;f=LICENSE;hb=bzip2-1.0.8) |
| libX11 and ncurses | [MIT/X11 notices in the pinned Python build](https://github.com/astral-sh/python-build-standalone/tree/20261003) |
| Tcl, Tk and Tix | [Tcl/Tk licences](https://github.com/python/cpython-source-deps/tree/tcltk-8.6.15) |
| SQLite | [Public-domain dedication](https://sqlite.org/copyright.html) |
| Berkeley DB 6.0.19 (Linux helper's `_dbm` extension) | [Sleepycat licence](https://opensource.org/license/sleepycat); [corresponding source](https://ftp.osuosl.org/pub/blfs/conglomeration/db/db-6.0.19.tar.gz) |
| pip's certifi / Mozilla CA certificate bundle | [MPL-2.0](https://www.mozilla.org/en-US/MPL/2.0/); [component notice](https://github.com/pypa/pip/blob/26.2.1/src/pip/_vendor/certifi/LICENSE) |
| pip's distlib | [Python licence and incorporated notices](https://github.com/pypa/pip/blob/26.2.1/src/pip/_vendor/distlib/LICENSE.txt) |
| pip's resolvelib | [ISC](https://github.com/pypa/pip/blob/26.2.1/src/pip/_vendor/resolvelib/LICENSE) |
| Microsoft C++ / OpenMP runtime components (Windows) | [Microsoft runtime terms](https://visualstudio.microsoft.com/license-terms/vs2022-cruntime/); obtained with the upstream runtime, not redistributed in the plugin |

Dependency versions and source archives for the Python helper are recorded in
its [pinned build recipe](https://github.com/astral-sh/python-build-standalone/blob/20261003/pythonbuild/downloads.json).
CPython 3.12 uses OpenSSL 3 and its bundled libmpdec. The build repository also
contains notices for other interpreter versions; those do not imply that this
helper contains those versions.

## Acknowledgements

This software makes use of facilities provided by Open CASCADE Technology.

This software uses the FreeImage open source image library under GNU GPL
version 3. FreeImage was designed and implemented by Floris van den Berg and
Hervé Drolon; see [the FreeImage project](https://freeimage.sourceforge.io/).

This software is based in part on the work of the FreeType Team and the
Independent JPEG Group.

## GPLv3 compatibility

The optional components retain their original licences. For dual-licensed
components the selections above use GPLv3-compatible options: GPLv3 for
FreeImage and JBIG-KIT, LGPL-2.1 for LibRaw, FTL for FreeType, and BSD-3-Clause
for Zstandard. Apache-2.0 is [compatible with GPLv3](https://apache.org/licenses/GPL-compatibility.html);
FTL is [compatible with GPLv3](https://freetype.org/license.html). The GNU
[licence compatibility list](https://www.gnu.org/licenses/license-list.html)
also covers the permissive, Python and Sleepycat licences. Microsoft platform
runtimes have separate terms and are not presented as GPL-licensed components;
the GPL's [system-library exception](https://www.gnu.org/licenses/gpl-faq.en.html#SystemLibraryException)
is relevant to standard compiler/runtime libraries.

Obico remains AGPL-3.0. GPLv3 section 13 and AGPLv3 section 13 explicitly permit
[combining GPLv3 and AGPLv3 work](https://www.gnu.org/licenses/gpl-faq.en.html#AGPLGPL).
That permission does not remove AGPL obligations, including applicable source
and network-interaction requirements. MPF's own code remains GPL-3.0-only.
The optional model runs locally; MPF does not host an Obico inference service.
Mozilla's [MPL/GPL compatibility provisions](https://www.mozilla.org/en-US/MPL/2.0/FAQ/#q14-may-i-combine-mpl-licensed-code-and-lgpl-licensed-code-in-the-same-executable-program)
cover the helper's MPL-2.0 certificate data without changing its own licence.

GPL and AGPL grant redistribution rights to the works they cover; no separate
permission is needed when their conditions are met. This index currently
describes direct upstream downloads. Mirroring binaries or weights requires
preserving the applicable full notices and licence copies and providing the
required corresponding source. An index of upstream links alone is not a
complete redistribution bundle. Components under other licences retain those
licences and their own redistribution conditions.
