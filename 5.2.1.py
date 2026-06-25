#!/usr/bin/env python3
from pathlib import Path

p = Path("build5.2.py")
src = p.read_text(encoding="utf-8")

# Replace only FILES raw wrappers if your pattern is consistent
src = src.replace('": r"""', '": r\'\'\'')
src = src.replace('\n    """,\n', '\n    \'\'\',\n')

p.write_text(src, encoding="utf-8")
print("Fixed wrapper quotes in build5.2.py")
