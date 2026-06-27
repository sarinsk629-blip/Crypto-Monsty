#!/usr/bin/env python3
# fix_require.py
# Injects a 'require' polyfill into ES Modules to prevent ReferenceErrors

from pathlib import Path

def patch_file(filepath):
    p = Path(filepath)
    if not p.exists():
        print(f"❌ Could not find {filepath}")
        return
    
    content = p.read_text(encoding="utf-8")
    
    # Strip out weird eval() hallucinations from the AI
    content = content.replace("eval(\"require('ws')\")", "require('ws')")
    content = content.replace("eval('require(\"ws\")')", "require('ws')")
    content = content.replace("eval(`require('ws')`)", "require('ws')")
    
    # Inject the require polyfill at the very top
    if "createRequire" not in content:
        polyfill = "import { createRequire } from 'module';\nconst require = createRequire(import.meta.url);\nglobalThis.require = require;\n"
        content = polyfill + content
        p.write_text(content, encoding="utf-8")
        print(f"✔ Polyfilled require() in {filepath}")
    else:
        print(f"⚡ {filepath} already polyfilled.")

def main():
    print("🔧 Patching ES Module 'require' compatibility...")
    patch_file("phoenix-backend/server.js")
    patch_file("phoenix-backend/exchangeMultiplexer.js")
    print("\nDone. The backend is now polyfilled.")

if __name__ == "__main__":
    main()
