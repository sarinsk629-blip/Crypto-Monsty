#!/usr/bin/env python3
# eruda.py

from pathlib import Path

def main():
    print("⚡ Injecting Eruda Mobile DevTools...")
    index_path = Path("phoenix-frontend/index.html")
    
    if not index_path.exists():
        print("❌ index.html not found!")
        return

    html = index_path.read_text(encoding="utf-8")
    
    # The Eruda CDN payload
    eruda_payload = '''
    <script src="https://cdn.jsdelivr.net/npm/eruda"></script>
    <script>eruda.init();</script>
    '''
    
    if "eruda.init()" not in html:
        # Inject right after the <head> tag opens
        html = html.replace("<head>", "<head>\n" + eruda_payload)
        index_path.write_text(html, encoding="utf-8")
        print("✔ Eruda DevTools injected into index.html")
    else:
        print("⚡ Eruda is already injected.")

if __name__ == "__main__":
    main()
