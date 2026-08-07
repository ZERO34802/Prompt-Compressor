"""Flask backend for live template recall.

Exposes the REAL matcher from templates/match_template.py over HTTP so the
dashboard can query it. Nothing is faked: every response comes from the same
sentence-transformers model (all-MiniLM-L6-v2) and template signatures the
pipeline uses.

The model and signatures are loaded ONCE at startup (module load) via a warmup
call; after that each request is a single embedding + dot product.

Run:
  python3 server/app.py          # backend on http://localhost:5001

Routes:
  GET  /api/health  -> {"ok": true}   (only reachable once the model is loaded)
  POST /api/recall  {"prompt": "..."} -> match() result as JSON
"""

from __future__ import annotations

import os
import sys

from flask import Flask, jsonify, request
from flask_cors import CORS

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO_ROOT, "templates"))

from match_template import match  # noqa: E402 -- the real matcher, not a copy

app = Flask(__name__)
CORS(app)  # the dashboard is served from :8000, the API from :5001

# Warm up at module load: the first match() call loads MiniLM and embeds every
# template signature, and match_template caches both in memory.
print("[server] warming up: loading all-MiniLM-L6-v2 + template signatures ...")
match("warmup")
print("[server] ready -- POST /api/recall on http://localhost:5001")


@app.get("/api/health")
def health():
    return jsonify({"ok": True})


@app.post("/api/recall")
def recall():
    body = request.get_json(silent=True) or {}
    prompt = (body.get("prompt") or "").strip()
    if not prompt:
        return jsonify({"error": 'body must be {"prompt": "..."}'}), 400
    return jsonify(match(prompt))


if __name__ == "__main__":
    app.run(port=5001, debug=False)
