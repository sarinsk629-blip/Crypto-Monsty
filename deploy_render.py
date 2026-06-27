#!/usr/bin/env python3
# deploy_render.py
# Prepares the Infinite Court V8.3 for Render.com Zero-Cost Cloud Deployment

from pathlib import Path
import textwrap

ROOT = Path(".").resolve()

FILES = {
    # 1. THE RENDER BLUEPRINT (Infrastructure as Code)
    "render.yaml": r'''
    services:
      # 1. THE PYTHON ML BRIDGE
      - type: web
        name: phoenix-bridge
        env: python
        rootDir: phoenix-bridge
        buildCommand: pip install -r requirements.txt
        startCommand: uvicorn main:app --host 0.0.0.0 --port $PORT
        envVars:
          - key: PYTHON_VERSION
            value: 3.10.0
          - key: BRIDGE_API_KEY
            value: dev-bridge-key
          # Internal Render routing for zero-latency backend communication
          - key: BACKEND_BASE_URL
            value: http://phoenix-backend:8787 

      # 2. THE NODE.JS AGGREGATOR
      - type: web
        name: phoenix-backend
        env: node
        rootDir: phoenix-backend
        buildCommand: npm install
        startCommand: npm start
        envVars:
          - key: NODE_VERSION
            value: 18.0.0
          - key: PORT
            value: 8787

      # 3. THE COCKPIT UI
      - type: web
        name: phoenix-frontend
        env: static
        rootDir: phoenix-frontend
        buildCommand: ""
        staticPublishPath: "."
    '''
}

def write_file(path: Path, content: str):
    path.parent.mkdir(parents=True, exist_ok=True)
    cleaned = textwrap.dedent(content).lstrip("\n")
    path.write_text(cleaned, encoding="utf-8")

def patch_frontend_urls():
    shell_path = ROOT / "phoenix-frontend/js/app/appShell.js"
    if shell_path.exists():
        content = shell_path.read_text(encoding="utf-8")
        # Replace hardcoded localhosts with dynamic Window variables
        content = content.replace('"http://localhost:8787"', 'window.BACKEND_URL || "http://localhost:8787"')
        content = content.replace('"ws://localhost:8899/ws/signals"', 'window.BRIDGE_WS_URL || "ws://localhost:8899/ws/signals"')
        content = content.replace('"http://localhost:8899', '(window.BRIDGE_URL || "http://localhost:8899") + "')
        shell_path.write_text(content, encoding="utf-8")
        print("✔ Patched AppShell.js to support cloud URLs")

def patch_cockpit_html():
    html_path = ROOT / "phoenix-frontend/institutional-cockpit-v5.html"
    if html_path.exists():
        content = html_path.read_text(encoding="utf-8")
        injection = """
      <script>
        // RENDER CLOUD URL CONFIGURATION
        // We will update these manually in the browser or inject them later
        window.BACKEND_URL = "https://phoenix-backend-YOUR_URL.onrender.com";
        window.BRIDGE_URL = "https://phoenix-bridge-YOUR_URL.onrender.com";
        window.BRIDGE_WS_URL = "wss://phoenix-bridge-YOUR_URL.onrender.com/ws/signals";
      </script>
      <script type="module" src="./js/app/bootstrap.js"></script>
        """
        content = content.replace('<script type="module" src="./js/app/bootstrap.js"></script>', injection.strip())
        html_path.write_text(content, encoding="utf-8")
        print("✔ Patched Institutional Cockpit HTML for cloud routing")

def main():
    print("⚡ Forging Render.com Cloud Infrastructure Blueprint...")
    for rel, content in FILES.items():
        write_file(ROOT / rel, content)
        print(f"✔ {rel}")
    patch_frontend_urls()
    patch_cockpit_html()
    print("\nBlueprint Complete. Ready to push and deploy.")

if __name__ == "__main__":
    main()
