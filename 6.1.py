#!/usr/bin/env python3
# fix_build6_quotes.py

from pathlib import Path
import re

TARGET = Path("build6.0.py")

def main():
    if not TARGET.exists():
        print("❌ build6.0.py not found in current directory.")
        return

    src = TARGET.read_text(encoding="utf-8")
    original = src

    # 1) Inject constants after imports if missing
    if "TRIPLE_SINGLE = \"'\" * 3" not in src:
        m = re.search(r"^import[^\n]*\n(?:from[^\n]*\n|import[^\n]*\n)*", src, flags=re.M)
        insert_block = '\nTRIPLE_SINGLE = "\'" * 3\nTRIPLE_DOUBLE = \'"\' * 3\n'
        if m:
            src = src[:m.end()] + insert_block + src[m.end():]
        else:
            src = insert_block + "\n" + src

    # 2) Replace brittle triple-quote literal checks
    replacements = {
        'if "///" not in body:': 'if TRIPLE_SINGLE not in body:',
        'if "\\\'\\\'\\\'" not in body:': 'if TRIPLE_SINGLE not in body:',
        'if "\\\"\\\"\\\"" not in body:': 'if TRIPLE_DOUBLE not in body:',
        'if "\\\'\\\'\\\'" in body:': 'if TRIPLE_SINGLE in body:',
        'if "\\\"\\\"\\\"" in body:': 'if TRIPLE_DOUBLE in body:',
        'if "\'\'\'" not in body:': 'if TRIPLE_SINGLE not in body:',
        'if "\"\"\"" not in body:': 'if TRIPLE_DOUBLE not in body:',
        'if "\'\'\'" in body:': 'if TRIPLE_SINGLE in body:',
        'if "\"\"\"" in body:': 'if TRIPLE_DOUBLE in body:',
    }
    for k, v in replacements.items():
        src = src.replace(k, v)

    # regex variants
    src = re.sub(r'if\s+"\'\'\'"\s+not\s+in\s+body\s*:', 'if TRIPLE_SINGLE not in body:', src)
    src = re.sub(r'if\s+"\'\'\'"\s+in\s+body\s*:', 'if TRIPLE_SINGLE in body:', src)
    src = re.sub(r'if\s+"\"\"\""\s+not\s+in\s+body\s*:', 'if TRIPLE_DOUBLE not in body:', src)
    src = re.sub(r'if\s+"\"\"\""\s+in\s+body\s*:', 'if TRIPLE_DOUBLE in body:', src)

    # 3) Convert FILES raw wrappers from r""" ... """ to r''' ... '''
    # only starts that look like: "path": r"""
    src = re.sub(r'(".*?":\s*)r"""', r"\1r'''", src)

    # close wrappers: lines that are just     """,
    src = re.sub(r'^(\s*)""",\s*$', r"\1''',", src, flags=re.M)

    if src == original:
        print("ℹ️ No changes were needed.")
        return

    backup = TARGET.with_suffix(".py.bak")
    backup.write_text(original, encoding="utf-8")
    TARGET.write_text(src, encoding="utf-8")

    print(f"✅ Patched {TARGET}")
    print(f"🧷 Backup saved: {backup}")
    print("\nNext:")
    print("1) python -m py_compile build6.0.py")
    print("2) python build6.0.py")

if __name__ == "__main__":
    main()
