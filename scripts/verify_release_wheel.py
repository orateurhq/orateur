#!/usr/bin/env python3
"""Fail if the built wheel does not contain the orateur Python package."""

from __future__ import annotations

import zipfile
from pathlib import Path


def main() -> None:
    whl = next(Path("dist").glob("*.whl"))
    with zipfile.ZipFile(whl) as z:
        names = z.namelist()
    py_under_pkg = [n for n in names if n.startswith("orateur/") and n.endswith(".py")]
    if not py_under_pkg:
        raise SystemExit(f"{whl} has no orateur/*.py (got {len(names)} entries): {names[:15]}")
    print(f"OK: {whl.name} has {len(py_under_pkg)} orateur/*.py files")


if __name__ == "__main__":
    main()
