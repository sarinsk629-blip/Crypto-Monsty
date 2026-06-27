#!/usr/bin/env python3
# vercel_migration.py

from pathlib import Path
import textwrap

ROOT = Path(".").resolve()

RENDER_YAML = r'''
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
'''

def main():
    print("⚡ Ripping frontend out of Render...")
    path = ROOT / "render.yaml"
    path.write_text(textwrap.dedent(RENDER_YAML).lstrip("\n"), encoding="utf-8")
    print("✔ render.yaml updated. Backend and Bridge only.")

if __name__ == "__main__":
    main()

