#!/usr/bin/env python3
"""
hotfix_quote_collision.py
Scans build*.py files and replaces raw triple double quote wrappers r"""..."""
with raw triple single quote wrappers r[TRIPLE_SINGLE]... [TRIPLE_SINGLE] when safe.
"""

from pathlib import Path

ROOT = Path(".").resolve()

def patch_file(p: Path):
  txt = p.read_text(encoding="utf-8")
  if 'r"""' not in txt:
    return False

  out = []
  i = 0
  changed = False
  n = len(txt)

  while i < n:
    if txt.startswith('r"""', i):
      # find closing """
      j = i + 4
      end = txt.find('"""', j)
      if end == -1:
        out.append(txt[i:])
        break

      body = txt[j:end]
      # only convert if body does NOT contain triple single quotes
      if TRIPLE_SINGLE not in body:
        out.append("r" + TRIPLE_SINGLE)
        out.append(body)
        out.append(TRIPLE_SINGLE)
        changed = True
      else:
        out.append(txt[i:end+3])

      i = end + 3
    else:
      out.append(txt[i])
      i += 1

  if changed:
    p.write_text("".join(out), encoding="utf-8")
  return changed

def main():
  count = 0
  for p in ROOT.glob("build*.py"):
    if patch_file(p):
      count += 1
      print(f"patched: {p}")
  print(f"done. files patched: {count}")

if __name__ == "__main__":
  main()
