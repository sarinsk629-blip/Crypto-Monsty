#!/usr/bin/env python3
# final_wire.py

import shutil
from pathlib import Path

ROOT = Path(".").resolve()

def main():
    print("⚡ Initiating Final Network Wiring...")

    # 1. Update the JavaScript to point to your live cloud servers
    # I have pre-filled your backend URL from your screenshot.
    BACKEND_URL = "https://phoenix-backend-6h1n.onrender.com"
    BRIDGE_URL = "https://phoenix-bridge.onrender.com"
    BRIDGE_WS_URL = "wss://phoenix-bridge.onrender.com/ws/signals"

    shell_path = ROOT / "phoenix-frontend/js/app/appShell.js"
    if shell_path.exists():
        content = shell_path.read_text(encoding="utf-8")
        # Strip out the old localhost logic
        content = content.replace('http://localhost:8787', BACKEND_URL)
        content = content.replace('window.BACKEND_URL || ', '')
        content = content.replace('http://localhost:8899', BRIDGE_URL)
        content = content.replace('(window.BRIDGE_URL || ', '')
        content = content.replace('ws://localhost:8899/ws/signals', BRIDGE_WS_URL)
        content = content.replace('window.BRIDGE_WS_URL || ', '')
        shell_path.write_text(content, encoding="utf-8")
        print("✔ Hardcoded production URLs into AppShell.js")

    # 2. Promote the V5 Cockpit to be the main index page
    v5_html = ROOT / "phoenix-frontend/institutional-cockpit-v5.html"
    index_html = ROOT / "phoenix-frontend/index.html"
    
    if v5_html.exists():
        shutil.copyfile(v5_html, index_html)
        print("✔ Promoted Institutional Cockpit V5 to default index.html")

    print("\nWiring complete. Ready for final deployment.")

if __name__ == "__main__":
    main()
