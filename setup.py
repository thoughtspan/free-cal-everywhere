#!/usr/bin/env python3
"""
cal-book setup — run this once to connect your Google Calendar.

What it does:
  1. Asks for your Google OAuth credentials (Client ID + Secret)
  2. Opens a browser so you can sign in with Google
  3. Saves your token and writes a .env file ready for local use or deployment

You need a Google Cloud project with Calendar API enabled.
Instructions: https://github.com/YOUR_USERNAME/cal-book#setup
"""

import json
import os
import shutil
import sys
import webbrowser

# ── Dependency check ──────────────────────────────────────────────────────────
try:
    import yaml
    from google_auth_oauthlib.flow import InstalledAppFlow
    from googleapiclient.discovery import build
except ImportError:
    print("\n  Run this first:  pip install -r requirements.txt\n")
    sys.exit(1)

SCOPES    = ['https://www.googleapis.com/auth/calendar', 'openid',
             'https://www.googleapis.com/auth/userinfo.email']
ENV_FILE  = '.env'
TOK_FILE  = 'token.json'
CFG_FILE  = 'config.yaml'

# ── Helpers ───────────────────────────────────────────────────────────────────

def ask(prompt, default=None):
    suffix = f" [{default}]" if default else ""
    val = input(f"  {prompt}{suffix}: ").strip()
    return val or default

def write_env(vals: dict):
    lines = []
    if os.path.exists(ENV_FILE):
        with open(ENV_FILE) as f:
            for line in f:
                key = line.split('=')[0].strip()
                if key not in vals:
                    lines.append(line.rstrip())
    for k, v in vals.items():
        lines.append(f'{k}={v}')
    with open(ENV_FILE, 'w') as f:
        f.write('\n'.join(lines) + '\n')

# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    print("\n" + "─" * 50)
    print("  cal-book setup")
    print("─" * 50 + "\n")

    # ── Step 1: Google credentials ──────────────────────────────────────────

    print("Step 1 of 3 — Google OAuth credentials\n")
    print("  You need a Google Cloud project with Calendar API enabled.")
    print("  Full instructions in the README, but the short version:\n")
    print("    1. https://console.cloud.google.com/ → create a project")
    print("    2. APIs & Services → Enable → Google Calendar API")
    print("    3. Credentials → Create → OAuth client ID → Desktop app")
    print("    4. Copy the Client ID and Client Secret\n")

    client_id     = ask("Client ID")
    client_secret = ask("Client Secret")

    if not client_id or not client_secret:
        print("\n  Client ID and Secret are required.\n")
        sys.exit(1)

    # ── Step 2: OAuth sign-in ───────────────────────────────────────────────

    print("\nStep 2 of 3 — Sign in with Google\n")
    print("  Opening your browser. Sign in and allow calendar access.")
    print("  (The browser tab will close automatically.)\n")

    client_config = {
        "installed": {
            "client_id":     client_id,
            "client_secret": client_secret,
            "auth_uri":      "https://accounts.google.com/o/oauth2/auth",
            "token_uri":     "https://oauth2.googleapis.com/token",
            "redirect_uris": ["http://localhost"],
        }
    }

    flow  = InstalledAppFlow.from_client_config(client_config, SCOPES)
    creds = flow.run_local_server(port=0, open_browser=True,
                                   success_message="Authenticated! You can close this tab.")

    token_data = {
        'token':         creds.token,
        'refresh_token': creds.refresh_token,
        'token_uri':     creds.token_uri,
        'client_id':     creds.client_id,
        'client_secret': creds.client_secret,
        'scopes':        list(creds.scopes),
    }
    with open(TOK_FILE, 'w') as f:
        json.dump(token_data, f, indent=2)

    print(f"  ✓  Token saved to {TOK_FILE}")

    # Print which account they signed into
    try:
        service = build('oauth2', 'v2', credentials=creds)
        info    = service.userinfo().get().execute()
        print(f"  ✓  Signed in as: {info.get('email', 'unknown')}")
    except Exception:
        pass

    # ── Step 3: config.yaml ─────────────────────────────────────────────────

    print("\nStep 3 of 3 — Configure your booking page\n")

    if not os.path.exists(CFG_FILE):
        shutil.copy('config.example.yaml', CFG_FILE)

    with open(CFG_FILE) as f:
        cfg = yaml.safe_load(f)

    # Print their calendar list to help them fill in calendar_id
    try:
        cal_service = build('calendar', 'v3', credentials=creds)
        calendars   = cal_service.calendarList().list().execute().get('items', [])
        print("  Your calendars:\n")
        for c in sorted(calendars, key=lambda x: (not x.get('primary'), x['summary'])):
            tag = "  ← primary" if c.get('primary') else ""
            print(f"    {c['summary']}{tag}")
            print(f"      {c['id']}")
        print()
    except Exception:
        pass

    name        = ask("Your name",       cfg.get('owner_name', ''))
    email       = ask("Your email",      cfg.get('owner_email', ''))
    calendar_id = ask("Calendar ID to use",
                       cfg.get('calendar_id', email))
    timezone    = ask("Timezone (IANA)", cfg.get('timezone', 'America/New_York'))
    duration    = ask("Meeting length (minutes)", str(cfg.get('meeting_duration_minutes', 30)))
    admin_secret = ask("Admin secret (protects /auth/login)", "changeme")

    # Patch config.yaml
    cfg.update({
        'owner_name':               name,
        'owner_email':              email,
        'calendar_id':              calendar_id,
        'timezone':                 timezone,
        'meeting_duration_minutes': int(duration),
    })
    with open(CFG_FILE, 'w') as f:
        yaml.dump(cfg, f, default_flow_style=False, allow_unicode=True)
    print(f"\n  ✓  Config saved to {CFG_FILE}")

    # Write .env
    token_json = json.dumps(token_data)
    write_env({
        'GOOGLE_TOKEN':      token_json,
        'GOOGLE_CLIENT_ID':  client_id,
        'GOOGLE_CLIENT_SECRET': client_secret,
        'REDIRECT_URI':      'http://localhost:8080/auth/callback',
        'ADMIN_SECRET':      admin_secret,
    })
    print(f"  ✓  Secrets saved to {ENV_FILE}  (never commit this)")

    # ── Done ────────────────────────────────────────────────────────────────

    print("\n" + "─" * 50)
    print("  Setup complete!\n")
    print("  Run locally:")
    print("    python run.py\n")
    print("  Deploy to Fly.io:")
    print("    See README → Deployment\n")
    print("─" * 50 + "\n")


if __name__ == '__main__':
    main()
