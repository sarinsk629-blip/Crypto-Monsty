#!/usr/bin/env python3
# repair_frontend.py
# Neutralizes the ghost file, restores clean HTML, and bulletproofs imports.

import re
from pathlib import Path

ROOT = Path("phoenix-frontend")

def main():
    print("⚡ Executing Ultimate Frontend Repair...")
    
    # 1. CLEAN THE HTML AND REDIRECT TO APPSHELL
    index_path = ROOT / "index.html"
    if index_path.exists():
        html = index_path.read_text(encoding="utf-8")
        
        # Strip the massive green bootloader and Eruda tools
        html = re.sub(r'<div id="boot-log".*?</div>', '', html, flags=re.DOTALL)
        html = re.sub(r'<script>\s*// Create a massive on-screen terminal.*?</script>', '', html, flags=re.DOTALL)
        html = re.sub(r'<script src="https://cdn.jsdelivr.net/npm/eruda"></script>\s*<script>eruda.init\(\);</script>', '', html)
        
        # Redirect the ghost bootstrap.js directly to the real appShell.js
        if '<script type="module"' not in html:
            html = html.replace('</body>', '<script type="module" src="./js/app/appShell.js"></script>\n</body>')
        else:
            html = html.replace('bootstrap.js', 'appShell.js')
        
        index_path.write_text(html, encoding="utf-8")
        print("✔ Restored clean index.html and bypassed the missing ghost file.")

    # 2. AUTO-IGNITE APPSHELL
    appshell_path = ROOT / "js/app/appShell.js"
    if appshell_path.exists():
        js = appshell_path.read_text(encoding="utf-8")
        
        # Dynamically find the class name the AI used
        match = re.search(r'class\s+([A-Za-z0-9_]+)', js)
        class_name = match.group(1) if match else "AppShell"

        if "window.cockpit =" not in js:
            js += f"\n\n// Force Auto-Ignition\nsetTimeout(() => {{ window.cockpit = new {class_name}(); console.log('🚀 Cockpit UI Online'); }}, 100);\n"
            appshell_path.write_text(js, encoding="utf-8")
            print(f"✔ Injected auto-ignition sequence for {class_name}")

    # 3. FIX BROWSER IMPORT EXTENSIONS (The Vercel 404 Killer)
    for js_file in ROOT.rglob("*.js"):
        content = js_file.read_text(encoding="utf-8")
        new_lines = []
        for line in content.split('\n'):
            # Look for lines like: import { Widget } from './widgets/algo'
            if line.strip().startswith('import ') and ' from ' in line:
                parts = line.split(' from ')
                import_path = parts[1].strip().strip('"\';')
                # If it's a local file but misses the .js extension, fix it
                if import_path.startswith('.') and not import_path.endswith('.js'):
                    line = line.replace(import_path, import_path + '.js')
            new_lines.append(line)
        
        new_content = '\n'.join(new_lines)
        if new_content != content:
            js_file.write_text(new_content, encoding="utf-8")
            print(f"✔ Fixed ES Module import extensions in {js_file.name}")

    print("\nRepair complete. Ready for final launch.")

if __name__ == "__main__":
    main()
