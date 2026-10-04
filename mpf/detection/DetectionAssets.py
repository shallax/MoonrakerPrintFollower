"""Pinned local inference assets and the supported host matrix."""

from dataclasses import dataclass
import ctypes
from importlib.util import find_spec
import os
import platform
import sys

MODEL_URL = "https://www.obico.io/static_pub/ml-models/model-weights-5a6b1be1fa.onnx"
MODEL_SHA256 = "0a6ebd8e30dbf6a450c50f9c0a5406f04ba7eb1c99fd5996e888c78bb383b9aa"
MODEL_SIZE = 202_223_918
RUNTIME_VERSION = "1.23.2"
RUNTIME_INDEX_URL = f"https://pypi.org/pypi/onnxruntime/{RUNTIME_VERSION}/json"
ASSET_VERSION = "obico-onnx-v1"

# PyPI's published SHA-256 and byte length, pinned for the three Cura Python
# ABIs on the supported native CPU platforms. A changed index cannot select
# or substitute another wheel.
_WHEELS = {
    ("310", "macosx_13_0_arm64"): ("a7730122afe186a784660f6ec5807138bf9d792fa1df76556b27307ea9ebcbe3", 17195934),
    ("310", "macosx_13_0_x86_64"): ("b28740f4ecef1738ea8f807461dd541b8287d5650b5be33bca7b474e3cbd1f36", 19153079),
    ("310", "manylinux_2_27_aarch64.manylinux_2_28_aarch64"): ("8f7d1fe034090a1e371b7f3ca9d3ccae2fabae8c1d8844fb7371d1ea38e8e8d2", 15219883),
    ("310", "manylinux_2_27_x86_64.manylinux_2_28_x86_64"): ("4ca88747e708e5c67337b0f65eed4b7d0dd70d22ac332038c9fc4635760018f7", 17370357),
    ("310", "win_amd64"): ("0be6a37a45e6719db5120e9986fcd30ea205ac8103fd1fb74b6c33348327a0cc", 13467651),
    ("311", "macosx_13_0_arm64"): ("6f91d2c9b0965e86827a5ba01531d5b669770b01775b23199565d6c1f136616c", 17196113),
    ("311", "macosx_13_0_x86_64"): ("87d8b6eaf0fbeb6835a60a4265fde7a3b60157cf1b2764773ac47237b4d48612", 19153857),
    ("311", "manylinux_2_27_aarch64.manylinux_2_28_aarch64"): ("bbfd2fca76c855317568c1b36a885ddea2272c13cb0e395002c402f2360429a6", 15220095),
    ("311", "manylinux_2_27_x86_64.manylinux_2_28_x86_64"): ("da44b99206e77734c5819aa2142c69e64f3b46edc3bd314f6a45a932defc0b3e", 17372080),
    ("311", "win_amd64"): ("902c756d8b633ce0dedd889b7c08459433fbcf35e9c38d1c03ddc020f0648c6e", 13468349),
    ("312", "macosx_13_0_arm64"): ("b8f029a6b98d3cf5be564d52802bb50a8489ab73409fa9db0bf583eabb7c2321", 17195929),
    ("312", "macosx_13_0_x86_64"): ("218295a8acae83905f6f1aed8cacb8e3eb3bd7513a13fe4ba3b2664a19fc4a6b", 19157705),
    ("312", "manylinux_2_27_aarch64.manylinux_2_28_aarch64"): ("76ff670550dc23e58ea9bc53b5149b99a44e63b34b524f7b8547469aaa0dcb8c", 15226915),
    ("312", "manylinux_2_27_x86_64.manylinux_2_28_x86_64"): ("0f9b4ae77f8e3c9bee50c27bc1beede83f786fe1d52e99ac85aa8d65a01e9b77", 17382649),
    ("312", "win_amd64"): ("25de5214923ce941a3523739d34a520aac30f21e631de53bba9174dc9c004435", 13470528),
}


@dataclass(frozen=True)
class RuntimeWheel:
    filename: str
    sha256: str
    size: int


def host_wheel() -> RuntimeWheel:
    abi = f"{sys.version_info.major}{sys.version_info.minor}"
    machine = platform.machine().lower()
    if sys.platform == "darwin":
        release = platform.mac_ver()[0].split(".")
        if not release or not release[0].isdigit() or int(release[0]) < 13:
            raise ValueError("Local detection needs macOS 13 or newer for its CPU runtime")
        suffix = f"macosx_13_0_{machine}"
    elif sys.platform.startswith("linux"):
        libc, version = platform.libc_ver()
        numbers = version.split(".")
        if libc != "glibc" or len(numbers) < 2 or not all(part.isdigit() for part in numbers[:2]) \
                or tuple(map(int, numbers[:2])) < (2, 27):
            raise ValueError("Local detection needs glibc 2.27 or newer")
        suffix = f"manylinux_2_27_{machine}.manylinux_2_28_{machine}"
    elif sys.platform == "win32":
        if sys.getwindowsversion().major < 10:
            raise ValueError("Local detection needs Windows 10 or newer")
        suffix = "win_amd64" if machine in ("amd64", "x86_64") else "unsupported"
    else:
        raise ValueError("No verified local detection runtime for this operating system")
    pin = _WHEELS.get((abi, suffix))
    if pin is None:
        raise ValueError(f"No verified local detection runtime for Python {sys.version_info.major}.{sys.version_info.minor} on {machine}")
    memory = physical_memory()
    if memory < 4 * 1024 ** 3:
        raise ValueError("Local detection needs at least 4 GiB of physical memory")
    try:
        numpy_available = find_spec("numpy") is not None
    except (ImportError, ValueError) as exc:
        raise ValueError("Local detection cannot locate NumPy in Cura's Python environment") from exc
    if not numpy_available:
        raise ValueError("Local detection needs NumPy in Cura's Python environment")
    return RuntimeWheel(
        f"onnxruntime-{RUNTIME_VERSION}-cp{abi}-cp{abi}-{suffix}.whl",
        pin[0], pin[1],
    )


def physical_memory() -> int:
    if sys.platform == "win32":
        class MemoryStatus(ctypes.Structure):
            _fields_ = [("length", ctypes.c_ulong), ("memory_load", ctypes.c_ulong),
                        ("total_physical", ctypes.c_ulonglong),
                        ("available_physical", ctypes.c_ulonglong),
                        ("total_page_file", ctypes.c_ulonglong),
                        ("available_page_file", ctypes.c_ulonglong),
                        ("total_virtual", ctypes.c_ulonglong),
                        ("available_virtual", ctypes.c_ulonglong),
                        ("available_extended", ctypes.c_ulonglong)]

        status = MemoryStatus()
        status.length = ctypes.sizeof(status)
        return status.total_physical if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)) else 0
    try:
        return os.sysconf("SC_PHYS_PAGES") * os.sysconf("SC_PAGE_SIZE")
    except (OSError, ValueError, AttributeError):
        return 0


def installed_paths(root: str) -> tuple[str, str]:
    directory = os.path.join(root, "detection", ASSET_VERSION)
    return (os.path.join(directory, "runtime"),
            os.path.join(directory, "model-weights.onnx"))
