#!/usr/bin/env python3
"""
cal-book setup — run once, get a live booking page.

  python setup.py

That's it.
"""

import json
import os
import platform
import shutil
import subprocess
import sys
import time
from pathlib import Path

# ── Dependency bootstrap ──────────────────────────────────────────────────────
# Install requirements silently if missing so the user never has to.

def _ensure_deps():
    try:
        import yaml, google.auth, googleapiclient, google_auth_oauthlib  # noqa
    except ImportError:
        print("  Installing dependencies...")
        subprocess.check_call(
            [sys.executable, "-m", "pip", "install", "-q", "-r", "requirements.txt"]
        )

_ensure_deps()

import yaml
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

# ── OAuth client — register once at console.cloud.google.com ─────────────────
# Desktop app credentials are not secret (Google's own docs acknowledge this).
# Fork this repo? Replace with your own Client ID + Secret.
# See README → "Using your own credentials"

BUNDLED_CLIENT_ID     = os.environ.get("GOOGLE_CLIENT_ID",     "YOUR_CLIENT_ID.apps.googleusercontent.com")
BUNDLED_CLIENT_SECRET = os.environ.get("GOOGLE_CLIENT_SECRET", "YOUR_CLIENT_SECRET")

SCOPES = [
    "https://www.googleapis.com/auth/calendar",
    "openid",
    "https://www.googleapis.com/auth/userinfo.email",
    "https://www.googleapis.com/auth/userinfo.profile",
]

# ── Terminal helpers ──────────────────────────────────────────────────────────

BOLD  = "\033[1m"
DIM   = "\033[2m"
GREEN = "\033[32m"
CYAN  = "\033[36m"
RED   = "\033[31m"
RST   = "\033[0m"

def hdr(n, text):
    print(f"\n{BOLD}  {n}  {text}{RST}")
    print(f"  {'─' * (len(text) + 5)}")

def ok(msg):   print(f"  {GREEN}✓{RST}  {msg}")
def err(msg):  print(f"  {RED}✗{RST}  {msg}")
def info(msg): print(f"  {DIM}{msg}{RST}")

def ask(prompt, default=None):
    suffix = f" {DIM}[{default}]{RST}" if default is not None else ""
    try:
        val = input(f"  {prompt}{suffix}: ").strip()
    except (EOFError, KeyboardInterrupt):
        print(); sys.exit(0)
    return val if val else default

def ask_choice(prompt, options, default=1):
    for i, (label, sub) in enumerate(options, 1):
        marker = f"{BOLD}←{RST}" if i == default else " "
        print(f"    {i}.  {label}  {DIM}{sub}{RST}  {marker}")
    try:
        raw = input(f"\n  Choice [{default}]: ").strip()
        return int(raw) if raw else default
    except (ValueError, EOFError, KeyboardInterrupt):
        return default

# ── System helpers ────────────────────────────────────────────────────────────

def detect_timezone():
    try:
        ltime = Path("/etc/localtime")
        if ltime.is_symlink():
            target = str(ltime.resolve())
            if "zoneinfo/" in target:
                return target.split("zoneinfo/")[-1]
    except Exception:
        pass
    try:
        result = subprocess.run(
            ["timedatectl", "show", "--property=Timezone", "--value"],
            capture_output=True, text=True
        )
        if result.stdout.strip():
            return result.stdout.strip()
    except Exception:
        pass
    return "America/New_York"

def cmd_exists(name):
    return shutil.which(name) is not None

def run(args, **kwargs):
    return subprocess.run(args, **kwargs)

def run_or_die(args, msg="Command failed"):
    result = run(args)
    if result.returncode != 0:
        err(msg); sys.exit(1)

# ── Step 1: Google sign-in ────────────────────────────────────────────────────

def do_google_auth():
    hdr("①", "Sign in with Google")
    print()

    if BUNDLED_CLIENT_ID.startswith("YOUR_"):
        err("No OAuth credentials configured.")
        info("Set GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET, or see README.")
        sys.exit(1)

    client_config = {
        "installed": {
            "client_id":     BUNDLED_CLIENT_ID,
            "client_secret": BUNDLED_CLIENT_SECRET,
            "auth_uri":      "https://accounts.google.com/o/oauth2/auth",
            "token_uri":     "https://oauth2.googleapis.com/token",
            "redirect_uris": ["http://localhost"],
        }
    }

    print("  Opening your browser — sign in and allow Calendar access.")
    print("  The tab will close automatically.\n")
    flow  = InstalledAppFlow.from_client_config(client_config, SCOPES)
    creds = flow.run_local_server(port=0, open_browser=True,
                                   success_message="Done! You can close this tab.")

    # Fetch profile for smart defaults
    profile = {}
    try:
        svc     = build("oauth2", "v2", credentials=creds)
        profile = svc.userinfo().get().execute()
    except Exception:
        pass

    ok(f"Signed in as {profile.get('email', 'your account')}")

    token_data = {
        "token":         creds.token,
        "refresh_token": creds.refresh_token,
        "token_uri":     creds.token_uri,
        "client_id":     creds.client_id,
        "client_secret": creds.client_secret,
        "scopes":        list(creds.scopes),
    }
    with open("token.json", "w") as f:
        json.dump(token_data, f, indent=2)

    return creds, profile

# ── Step 2: Config ────────────────────────────────────────────────────────────

def do_config(creds, profile):
    hdr("②", "Your booking page")
    print()

    # Detect primary calendar
    primary_cal = profile.get("email", "")
    try:
        svc  = build("calendar", "v3", credentials=creds)
        cals = svc.calendarList().list().execute().get("items", [])
        for c in cals:
            if c.get("primary"):
                primary_cal = c["id"]
                break
    except Exception:
        pass

    owner_name   = ask("Your name",             profile.get("name", ""))
    owner_email  = ask("Your email",            profile.get("email", ""))
    calendar_id  = ask("Calendar ID",           primary_cal)
    timezone     = ask("Timezone",              detect_timezone())
    duration     = ask("Meeting length (mins)", "30")
    description  = ask("Page subtitle (optional)", "")

    cfg = {
        "owner_name":               owner_name,
        "owner_email":              owner_email,
        "calendar_id":              calendar_id,
        "timezone":                 timezone,
        "meeting_duration_minutes": int(duration or 30),
        "buffer_minutes":           15,
        "lookahead_days":           14,
        "working_hours": {
            "monday":    {"start": "09:00", "end": "18:00"},
            "tuesday":   {"start": "09:00", "end": "18:00"},
            "wednesday": {"start": "09:00", "end": "18:00"},
            "thursday":  {"start": "09:00", "end": "18:00"},
            "friday":    {"start": "09:00", "end": "17:00"},
        },
        "meeting_title":        "Meeting with {name}",
        "confirmation_message": "Looking forward to connecting.",
    }
    if description:
        cfg["description"] = description

    with open("config.yaml", "w") as f:
        yaml.dump(cfg, f, default_flow_style=False, allow_unicode=True)

    ok("Config saved to config.yaml")
    return cfg

# ── Step 3: Host ──────────────────────────────────────────────────────────────

def do_host(token_data):
    hdr("③", "Where do you want to host it?")
    print()

    choice = ask_choice("", [
        ("Fly.io",       "free, always on, you get a real URL"),
        ("Run locally",  "starts on localhost — share via a free tunnel"),
    ], default=1)

    if choice == 1:
        deploy_fly(token_data)
    else:
        run_local()

# ── Fly.io deployment ─────────────────────────────────────────────────────────

def deploy_fly(token_data):
    print()

    # Install flyctl if missing
    if not cmd_exists("flyctl") and not cmd_exists("fly"):
        info("flyctl not found — installing...")
        if platform.system() == "Darwin":
            run_or_die(["brew", "install", "flyctl"], "Failed to install flyctl via brew")
        else:
            run_or_die(
                ["sh", "-c", "curl -L https://fly.io/install.sh | sh"],
                "Failed to install flyctl"
            )
        ok("flyctl installed")

    fly = shutil.which("flyctl") or shutil.which("fly")

    # Auth check
    result = run([fly, "auth", "whoami"], capture_output=True, text=True)
    if result.returncode != 0:
        info("Opening Fly.io login...")
        run_or_die([fly, "auth", "login"], "Fly.io login failed")

    # App name
    print()
    app_name = ask("Fly app name (will become your URL)", "cal-book")
    app_name = app_name.replace(" ", "-").lower()

    # Write fly.toml
    fly_toml = f"""app = "{app_name}"
primary_region = "ewr"

[build]

[http_service]
  internal_port = 8080
  force_https   = true
  auto_stop_machines  = false
  auto_start_machines = true
  min_machines_running = 1

[[vm]]
  memory = "256mb"
  cpus   = 1
"""
    with open("fly.toml", "w") as f:
        f.write(fly_toml)

    print()
    info("Creating app on Fly.io...")
    run([fly, "apps", "create", app_name], capture_output=True)

    info("Uploading secrets...")
    admin_secret = os.urandom(12).hex()
    run_or_die([
        fly, "secrets", "set",
        f"GOOGLE_TOKEN={json.dumps(token_data)}",
        f"ADMIN_SECRET={admin_secret}",
        "--app", app_name,
    ], "Failed to set secrets")

    # Upload config.yaml as a secret too
    with open("config.yaml") as f:
        cfg_contents = f.read()
    run([fly, "secrets", "set", f"CONFIG_YAML={cfg_contents}", "--app", app_name],
        capture_output=True)

    info("Deploying (this takes ~1 minute)...")
    run_or_die([fly, "deploy", "--app", app_name, "--remote-only"],
               "Deployment failed — run `flyctl logs` for details")

    url = f"https://{app_name}.fly.dev"
    print()
    print(f"  {BOLD}{GREEN}🎉  Live at {url}{RST}")
    print()
    info(f"Share this link with anyone: {url}")
    info(f"Admin (re-auth if needed): {url}/auth/login?secret={admin_secret}")
    print()

# ── Local run ─────────────────────────────────────────────────────────────────

def run_local():
    print()

    # Load .env
    token_json = json.dumps(json.load(open("token.json")))
    with open(".env", "w") as f:
        f.write(f"GOOGLE_TOKEN={token_json}\n")
        f.write(f"ADMIN_SECRET={os.urandom(8).hex()}\n")

    # Ask about tunnel
    want_tunnel = ask("Share a public URL via Cloudflare Tunnel? (y/n)", "y")
    use_tunnel  = want_tunnel.lower().startswith("y")

    if use_tunnel and not cmd_exists("cloudflared"):
        info("Installing cloudflared...")
        if platform.system() == "Darwin":
            run(["brew", "install", "cloudflared"], capture_output=True)
        else:
            info("Install cloudflared: https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/downloads/")
            use_tunnel = False

    print()
    info("Starting server...")

    server = subprocess.Popen(
        [sys.executable, "run.py"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
    )
    time.sleep(2)

    if use_tunnel:
        tunnel = subprocess.Popen(
            ["cloudflared", "tunnel", "--url", "http://localhost:8080"],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True
        )
        public_url = None
        for line in tunnel.stdout:
            if "trycloudflare.com" in line or ".cloudflare.com" in line:
                for part in line.split():
                    if part.startswith("https://"):
                        public_url = part.strip()
                        break
            if public_url:
                break

        print()
        print(f"  {BOLD}{GREEN}🎉  Public URL: {public_url}{RST}")
        print( f"  {DIM}Local:          http://localhost:8080{RST}")
    else:
        print()
        print(f"  {BOLD}{GREEN}🎉  Running at http://localhost:8080{RST}")

    print()
    info("Press Ctrl+C to stop.")
    try:
        server.wait()
    except KeyboardInterrupt:
        server.terminate()
        print("\n  Stopped.\n")

# ── Entry point ───────────────────────────────────────────────────────────────

def main():
    print()
    print(f"  {BOLD}cal-book setup{RST}")
    print(f"  {DIM}─────────────{RST}")

    creds, profile = do_google_auth()
    cfg            = do_config(creds, profile)

    with open("token.json") as f:
        token_data = json.load(f)

    do_host(token_data)

if __name__ == "__main__":
    main()
