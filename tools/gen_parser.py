"""Mini-PL/SQL gramerinden Python ayrıştırıcısını yeniden üretir.

Kullanım:  python tools/gen_parser.py
Gereksinim: Java (>= 11) ve `antlr4-tools` paketi. Sürüm, antlr4-python3-runtime ile aynı olmalıdır.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

ANTLR_VERSION = "4.13.2"

ROOT = Path(__file__).resolve().parents[1]
GRAMMAR_DIR = ROOT / "plutosql" / "transformer" / "grammar"
OUT_DIR = ROOT / "plutosql" / "transformer" / "generated"


def main() -> int:
    antlr = shutil.which("antlr4") or str(Path(sys.executable).parent / "antlr4")
    cmd = [
        antlr, "-v", ANTLR_VERSION,
        "-Dlanguage=Python3", "-visitor", "-no-listener",
        "-o", str(OUT_DIR),
        "MiniPlsqlLexer.g4", "MiniPlsqlParser.g4",
    ]
    return subprocess.call(cmd, cwd=GRAMMAR_DIR)


if __name__ == "__main__":
    raise SystemExit(main())
