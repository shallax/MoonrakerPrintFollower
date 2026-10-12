"""Remove bundled Microsoft CRT/OpenMP files from the pinned Windows archives.

The CAD wheel's PE import names are repaired to the ordinary system DLL names.
Windows users therefore need the Visual C++ runtime installed on the host.
"""

from __future__ import annotations

import base64
import csv
import hashlib
import io
from pathlib import Path
import re
import tarfile
import zipfile

_REDIST = re.compile(rb"(?i)(msvcp140|vcomp140)-[0-9a-f]{32}\.dll\x00")
_WINDOWS_DLL = re.compile(r"(?i)^(msvcp140|vcomp140)-[0-9a-f]{32}\.dll$")
_HELPER_DLLS = {"python/vcruntime140.dll", "python/vcruntime140_1.dll"}


def _record_line(name, data):
    digest = base64.urlsafe_b64encode(hashlib.sha256(data).digest()).rstrip(b"=").decode("ascii")
    return name, "sha256=" + digest, str(len(data))


def strip_cad_wheel(source: Path, destination: Path):
    removed, repaired = set(), 0
    rows = []
    with zipfile.ZipFile(source) as original, zipfile.ZipFile(
        destination, "w", compression=zipfile.ZIP_STORED, allowZip64=True
    ) as output:
        members = original.infolist()
        record_name = next(info.filename for info in members if info.filename.endswith(".dist-info/RECORD"))
        for info in members:
            name = info.filename
            if name == record_name:
                continue
            if _WINDOWS_DLL.match(name.rsplit("/", 1)[-1]):
                removed.add(name.rsplit("/", 1)[-1].split("-", 1)[0].lower())
                continue
            data = original.read(info)
            if name.lower().endswith((".dll", ".pyd")):
                def replace(match):
                    nonlocal repaired
                    repaired += 1
                    plain = match.group(1).lower() + b".dll\x00"
                    return plain + b"\x00" * (len(match.group()) - len(plain))
                data = _REDIST.sub(replace, data)
            member = zipfile.ZipInfo(name, date_time=info.date_time)
            member.external_attr = info.external_attr
            member.create_system = info.create_system
            member.comment = info.comment
            member.compress_type = zipfile.ZIP_STORED
            output.writestr(member, data)
            if not info.is_dir():
                rows.append(_record_line(name, data))
        record = io.StringIO(newline="")
        writer = csv.writer(record, lineterminator="\n")
        writer.writerows(rows)
        writer.writerow((record_name, "", ""))
        member = zipfile.ZipInfo(record_name, date_time=original.getinfo(record_name).date_time)
        # ZipInfo defaults create_system from the host OS. Pin this generated
        # member so Windows and Unix produce identical release bytes.
        member.create_system = 3
        member.compress_type = zipfile.ZIP_STORED
        output.writestr(member, record.getvalue().encode("utf-8"))
    if removed != {"msvcp140", "vcomp140"} or repaired < 2:
        destination.unlink(missing_ok=True)
        raise ValueError("Unexpected Windows CAD wheel redistributable inventory")


def strip_python_helper(source: Path, destination: Path):
    removed = set()
    with tarfile.open(source, "r:gz") as original, tarfile.open(destination, "w") as output:
        for member in original:
            if member.name in _HELPER_DLLS:
                removed.add(member.name)
                continue
            with original.extractfile(member) if member.isfile() else _null_file() as data:
                output.addfile(member, data)
    if removed != _HELPER_DLLS:
        destination.unlink(missing_ok=True)
        raise ValueError("Unexpected Windows Python helper redistributable inventory")


class _null_file:
    def __enter__(self):
        return None

    def __exit__(self, *_args):
        return False
