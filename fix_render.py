#!/usr/bin/env python3
# fix_render.py

from pathlib import Path
import textwrap

ROOT = Path(".").resolve()

FILES = {
    "render.yaml": r'''
    services:
      # 1. THE PYTHON ML BRIDGE
      - type: web
        name: phoenix-bridge
        env: python
        plan: free
        rootDir: phoenix-bridge
        buildCommand: pip install -r requirements.txt
        startCommand: uvicorn main:app --host 0.0.0.0 --port $PORT
        envVars:
          - key: PYTHON_VERSION
            value: 3.10.0
          - key: BRIDGE_API_KEY
            value: dev-bridge-key
          - key: BACKEND_BASE_URL
            value: http://phoenix-backend:8787 

      # 2. THE NODE.JS AGGREGATOR
      - type: web
        name: phoenix-backend
        env: node
        plan: free
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

def main():
    for rel, content in FILES.items():
        write_file(ROOT / rel, content)
        print(f"✔ Patched {rel} with 'plan: free' bypass.")

if __name__ == "__main__":
    main()
