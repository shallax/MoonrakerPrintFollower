from __future__ import annotations

import argparse
import pathlib
import re
import sys
from dataclasses import dataclass, field
from typing import Iterable, List, Optional

IDENT = re.compile(r"[A-Za-z_][A-Za-z0-9_.]*")
OPENERS = {"{": "}", "(": ")", "[": "]"}
CLOSERS = {value: key for key, value in OPENERS.items()}


@dataclass
class Frame:
    opener: str
    line: int
    qml_object: bool = False
    properties: dict[str, int] = field(default_factory=dict)
    statement_start: bool = True


def _sanitise(text: str) -> tuple[str, List[str]]:
    out: List[str] = []
    errors: List[str] = []
    state = "code"
    quote = ""
    escaped = False
    line = 1
    string_line = 1
    block_line = 1
    i = 0
    while i < len(text):
        ch = text[i]
        nxt = text[i + 1] if i + 1 < len(text) else ""
        if ch == "\n":
            line += 1
        if state == "line-comment":
            out.append("\n" if ch == "\n" else " ")
            if ch == "\n":
                state = "code"
            i += 1
            continue
        if state == "block-comment":
            if ch == "*" and nxt == "/":
                out.extend((" ", " "))
                i += 2
                state = "code"
                continue
            out.append("\n" if ch == "\n" else " ")
            i += 1
            continue
        if state == "string":
            out.append("\n" if ch == "\n" else " ")
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == quote:
                state = "code"
            i += 1
            continue
        if ch == "/" and nxt == "/":
            out.extend((" ", " "))
            i += 2
            state = "line-comment"
            continue
        if ch == "/" and nxt == "*":
            block_line = line
            out.extend((" ", " "))
            i += 2
            state = "block-comment"
            continue
        if ch in {'"', "'"}:
            quote = ch
            string_line = line
            state = "string"
            out.append(" ")
            i += 1
            continue
        out.append(ch)
        i += 1
    if state == "string":
        errors.append(f"line {string_line}: unterminated string")
    elif state == "block-comment":
        errors.append(f"line {block_line}: unterminated block comment")
    return "".join(out), errors


def _is_qml_type(identifier: Optional[str]) -> bool:
    if not identifier:
        return False
    tail = identifier.rsplit(".", 1)[-1]
    return bool(tail and tail[0].isupper())


def check_text(text: str, name: str = "<qml>") -> List[str]:
    clean, errors = _sanitise(text)
    stack: List[Frame] = []
    line = 1
    previous_identifier: Optional[str] = None
    i = 0
    while i < len(clean):
        ch = clean[i]
        if ch == "\n":
            line += 1
            if stack and stack[-1].qml_object:
                stack[-1].statement_start = True
            previous_identifier = None
            i += 1
            continue
        if ch.isspace():
            i += 1
            continue
        match = IDENT.match(clean, i)
        if match:
            ident = match.group(0)
            end = match.end()
            j = end
            while j < len(clean) and clean[j] in " \t\r":
                j += 1
            if stack and stack[-1].qml_object and stack[-1].statement_start and j < len(clean) and clean[j] == ":":
                first = stack[-1].properties.get(ident)
                if first is not None:
                    errors.append(f"line {line}: duplicate property '{ident}' (first set on line {first})")
                else:
                    stack[-1].properties[ident] = line
                stack[-1].statement_start = False
            elif stack and stack[-1].qml_object and stack[-1].statement_start:
                # Keep statement_start true for a QML type name immediately
                # followed by an object brace; otherwise this begins an expression.
                k = j
                if k >= len(clean) or clean[k] != "{":
                    stack[-1].statement_start = False
            previous_identifier = ident
            i = end
            continue
        if ch in OPENERS:
            qml_object = ch == "{" and _is_qml_type(previous_identifier)
            stack.append(Frame(ch, line, qml_object=qml_object))
            previous_identifier = None
            i += 1
            continue
        if ch in CLOSERS:
            if not stack or stack[-1].opener != CLOSERS[ch]:
                errors.append(f"line {line}: unmatched '{ch}'")
            else:
                stack.pop()
                if stack and stack[-1].qml_object:
                    stack[-1].statement_start = True
            previous_identifier = None
            i += 1
            continue
        if ch == ";":
            if stack and stack[-1].qml_object:
                stack[-1].statement_start = True
            previous_identifier = None
            i += 1
            continue
        if ch == ":":
            previous_identifier = None
            i += 1
            continue
        if stack and stack[-1].qml_object and ch not in ",":
            stack[-1].statement_start = False
        previous_identifier = None
        i += 1

    for frame in reversed(stack):
        errors.append(f"line {frame.line}: unclosed '{frame.opener}'")
    first = next((line.strip() for line in text.splitlines() if line.strip()), "")
    if first and not first.startswith("import "):
        errors.append("line 1: QML file should begin with imports")
    return [f"{name}: {error}" for error in errors]


def iter_qml(paths: Iterable[pathlib.Path]) -> Iterable[pathlib.Path]:
    for path in paths:
        if path.is_dir():
            yield from sorted(path.rglob("*.qml"))
        elif path.suffix.lower() == ".qml":
            yield path


def main() -> int:
    parser = argparse.ArgumentParser(description="Structural QML release sanity checker")
    parser.add_argument("paths", nargs="+", type=pathlib.Path)
    args = parser.parse_args()
    failures: List[str] = []
    files = list(iter_qml(args.paths))
    if not files:
        print("No QML files found", file=sys.stderr)
        return 2
    for path in files:
        failures.extend(check_text(path.read_text(encoding="utf-8"), str(path)))
    if failures:
        print("\n".join(failures), file=sys.stderr)
        return 1
    print(f"QML sanity check passed for {len(files)} files")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
