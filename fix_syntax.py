#!/usr/bin/env python3
# fix_syntax.py

from pathlib import Path

def main():
    print("⚡ Scanning for syntax errors in appShell.js...")
    path = Path("phoenix-frontend/js/app/appShell.js")
    
    if path.exists():
        content = path.read_text(encoding="utf-8")
        original = content
        
        # Snipe the rogue closing parenthesis left by the previous regex
        content = content.replace('") +', '" +')
        content = content.replace("') +", "' +")
        
        if original != content:
            path.write_text(content, encoding="utf-8")
            print("✔ Syntax Error neutralized. Rogue parenthesis removed.")
        else:
            print("⚡ No syntax errors found.")
    else:
        print("❌ appShell.js not found.")

if __name__ == "__main__":
    main()
