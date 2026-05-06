#!/usr/bin/env python3
"""
free-cal setup — run once, get a live booking page.

  python setup.py

That's it.
"""

import json
import os
import platform
import re
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

# ── Dependency bootstrap ──────────────────────────────────────────────────────
# Install requirements silently if missing so the user never has to.

def _ensure_deps():
    try:
        import yaml, google.auth, googleapiclient, google_auth_oauthlib  # noqa
    except ImportError:
        # Create a venv and install there — avoids system Python restrictions
        venv_dir = Path(__file__).parent / "venv"
        if not venv_dir.exists():
            print("  Creating virtual environment...")
            subprocess.check_call([sys.executable, "-m", "venv", str(venv_dir)])
        venv_python = venv_dir / ("Scripts/python.exe" if platform.system() == "Windows"
                                   else "bin/python")
        print("  Installing dependencies...")
        subprocess.check_call([str(venv_python), "-m", "pip", "install", "-q",
                                "-r", "requirements.txt"])
        # Re-exec with the venv python so imports work
        os.execv(str(venv_python), [str(venv_python)] + sys.argv)

_ensure_deps()

import yaml
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

# ── OAuth client — register once at console.cloud.google.com ─────────────────
# Desktop app credentials are not secret (Google's own docs acknowledge this).
# Fork this repo? Replace with your own Client ID + Secret.
# See README → "Using your own credentials"

BUNDLED_CLIENT_ID     = os.environ.get("GOOGLE_CLIENT_ID",     "902695009996-jkro8pnpgqpd28abis88dbj9cedfj03l.apps.googleusercontent.com")
BUNDLED_CLIENT_SECRET = os.environ.get("GOOGLE_CLIENT_SECRET", "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")

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

    owner_name   = ask("Your name (press Enter to accept)", profile.get("name", ""))
    owner_email  = ask("Your email",            profile.get("email", ""))
    calendar_id  = ask("Calendar ID",           primary_cal)
    timezone     = ask("Timezone",              detect_timezone())
    duration     = ask("Meeting length (mins)", "30")
    description  = ask("Page subtitle (optional, press Enter to skip)", "")
    if description.lower() in ("no", "n", "none", "skip"):
        description = ""

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

def do_host(token_data, cfg):
    hdr("③", "Where do you want to host it?")
    print()

    choice = ask_choice("", [
        ("Railway",      "free $5/mo credit, always on, real URL — fully terminal"),
        ("Run locally",  "starts on localhost — share via a free tunnel"),
    ], default=1)

    if choice == 1:
        deploy_railway(token_data, cfg)
    else:
        run_local()

# ── Railway deployment ────────────────────────────────────────────────────────

def deploy_railway(token_data, cfg):
    print()

    # Install Railway CLI if missing
    if not cmd_exists("railway"):
        info("Installing Railway CLI...")
        if platform.system() == "Darwin":
            if cmd_exists("brew"):
                run_or_die(["brew", "install", "railway"], "Failed to install Railway via brew")
            else:
                run_or_die(["sh", "-c", "curl -fsSL https://railway.app/install.sh | sh"],
                           "Failed to install Railway CLI")
        else:
            run_or_die(["sh", "-c", "curl -fsSL https://railway.app/install.sh | sh"],
                       "Failed to install Railway CLI")
        ok("Railway CLI installed")

    # Write Procfile so Railway knows the start command
    with open("Procfile", "w") as f:
        f.write("web: python run.py\n")

    print()
    info("Opening Railway login (browser will open)...")
    run_or_die(["railway", "login"], "Railway login failed")
    print()

    # Create project
    info("Creating Railway project...")
    result = run(["railway", "init", "--name", "free-cal-everywhere"],
                 capture_output=True, text=True)
    if result.returncode != 0:
        # May already be linked; try linking to existing or continue
        run(["railway", "init"], capture_output=True)

    # Derive a personal service name from the owner's first name
    first_name   = cfg.get("owner_name", "").split()[0].lower() if cfg.get("owner_name") else ""
    service_name = f"freecal-booktime-{first_name}" if first_name else "free-cal-everywhere"

    # Create a service within the project — required before variables can be set
    info("Creating service...")
    run(["railway", "add", "--service", service_name], capture_output=True, text=True)

    # Set environment variables in one call
    admin_secret = os.urandom(12).hex()
    with open("config.yaml") as f:
        cfg_yaml = f.read()

    info("Uploading secrets...")
    run_or_die([
        "railway", "variables", "set",
        f"GOOGLE_TOKEN={json.dumps(token_data)}",
        f"ADMIN_SECRET={admin_secret}",
        f"CONFIG_YAML={cfg_yaml}",
    ], "Failed to set environment variables")

    # Deploy
    info("Deploying (this takes ~1 minute)...")
    run_or_die(["railway", "up", "--detach"], "Deployment failed — run `railway logs` for details")

    # Provision public domain (Railway doesn't create one automatically)
    info("Provisioning domain...")
    url_result = run(["railway", "domain"], capture_output=True, text=True)
    url = url_result.stdout.strip()
    if not url or not url.startswith("http"):
        run(["railway", "domain", "create"], capture_output=True, text=True)
        url_result = run(["railway", "domain"], capture_output=True, text=True)
        url = url_result.stdout.strip()
    if url and not url.startswith("http"):
        url = f"https://{url}"
    if not url:
        url = f"https://{service_name}.up.railway.app"

    # Write admin credentials to a local file — keep secret off the terminal
    admin_url = f"{url}/auth/login?secret={admin_secret}"
    Path("admin-secret.txt").write_text(
        f"Booking URL: {url}\nAdmin URL:   {admin_url}\n"
    )

    print()
    print(f"  {BOLD}{GREEN}🎉  Live at {url}{RST}")
    print()
    info(f"Share this link with anyone: {url}")
    info(f"Admin credentials saved to: admin-secret.txt")
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

    # Use the venv python if available, otherwise current interpreter
    venv_python = Path(__file__).parent / "venv" / "bin" / "python"
    py = str(venv_python) if venv_python.exists() else sys.executable

    log_file = open("server.log", "w")
    server = subprocess.Popen(
        [py, "run.py"],
        stdout=log_file, stderr=log_file
    )
    time.sleep(2)

    if use_tunnel:
        tunnel = subprocess.Popen(
            ["cloudflared", "tunnel", "--url", "http://localhost:8080"],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True
        )
        public_url = None
        for line in tunnel.stdout:
            m = re.search(r'https://[a-z0-9-]+\.trycloudflare\.com', line)
            if m:
                public_url = m.group(0)
                break

        # Drain remaining output so cloudflared doesn't block on a full pipe
        threading.Thread(target=lambda: [_ for _ in tunnel.stdout],
                         daemon=True).start()

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
    print(f"  {BOLD}Free Cal Everywhere — setup{RST}")
    print(f"  {DIM}──────────────────────────{RST}")

    creds, profile = do_google_auth()
    cfg            = do_config(creds, profile)

    with open("token.json") as f:
        token_data = json.load(f)

    do_host(token_data, cfg)

if __name__ == "__main__":
    main()
