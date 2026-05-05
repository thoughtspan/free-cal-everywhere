#!/usr/bin/env python3
"""
Run free-cal locally.

  python run.py              # start on localhost:8080
  python run.py --port 3000  # custom port

To share your booking page publicly, expose it with a free tunnel:
  cloudflared tunnel --url http://localhost:8080
  ngrok http 8080
"""

import argparse
import os

# Load .env before importing the app
if os.path.exists('.env'):
    with open('.env') as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith('#') and '=' in line:
                k, _, v = line.partition('=')
                os.environ.setdefault(k.strip(), v.strip())

# Seed GOOGLE_TOKEN from token.json if not already in env
import json
if 'GOOGLE_TOKEN' not in os.environ and os.path.exists('token.json'):
    with open('token.json') as f:
        os.environ['GOOGLE_TOKEN'] = f.read().strip()

import uvicorn

if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--port', type=int, default=8080)
    args = parser.parse_args()
    print(f"\n  free-cal running at http://localhost:{args.port}\n")
    uvicorn.run("main:app", host="0.0.0.0", port=args.port, reload=True)
