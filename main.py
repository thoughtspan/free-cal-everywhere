"""
cal-book — self-hosted Calendly alternative
"""

import asyncio
import os
import secrets
import sqlite3
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import yaml
from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from google_auth_oauthlib.flow import Flow

import calendar_client as gc

# ── App setup ─────────────────────────────────────────────────────────────────

app       = FastAPI()
templates = Jinja2Templates(directory="templates")
app.mount("/static", StaticFiles(directory="static"), name="static")

_book_lock = asyncio.Lock()

# ── Config ────────────────────────────────────────────────────────────────────

def load_config():
    # CONFIG_YAML env var takes precedence (used on Fly.io)
    raw = os.environ.get('CONFIG_YAML')
    if raw:
        cfg = yaml.safe_load(raw)
    else:
        with open(os.environ.get('CONFIG_PATH', 'config.yaml')) as f:
            cfg = yaml.safe_load(f)
    cfg.setdefault('meeting_duration_minutes', 30)
    cfg.setdefault('buffer_minutes', 15)
    cfg.setdefault('lookahead_days', 14)
    cfg.setdefault('slot_interval_minutes', cfg['meeting_duration_minutes'])
    cfg.setdefault('timezone', 'America/New_York')
    cfg.setdefault('meeting_title', 'Meeting with {name}')
    cfg.setdefault('confirmation_message', 'Looking forward to connecting.')
    return cfg

# ── Database ──────────────────────────────────────────────────────────────────

def get_db():
    db = sqlite3.connect('bookings.db')
    db.execute("""
        CREATE TABLE IF NOT EXISTS google_tokens (
            id         INTEGER PRIMARY KEY,
            token_json TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
    """)
    db.execute("""
        CREATE TABLE IF NOT EXISTS bookings (
            slot_iso  TEXT PRIMARY KEY,
            name      TEXT,
            email     TEXT,
            booked_at TEXT
        )
    """)
    db.execute("""
        CREATE TABLE IF NOT EXISTS oauth_states (
            state      TEXT PRIMARY KEY,
            created_at TEXT NOT NULL
        )
    """)
    db.commit()
    return db

def is_authenticated(db) -> bool:
    has_db  = db.execute("SELECT 1 FROM google_tokens LIMIT 1").fetchone() is not None
    has_env = bool(os.environ.get('GOOGLE_TOKEN'))
    return has_db or has_env

def slot_taken(db, slot: datetime) -> bool:
    return db.execute(
        "SELECT 1 FROM bookings WHERE slot_iso=?", (slot.isoformat(),)
    ).fetchone() is not None

def record_booking(db, slot: datetime, name: str, email: str):
    db.execute(
        "INSERT INTO bookings (slot_iso, name, email, booked_at) VALUES (?,?,?,?)",
        (slot.isoformat(), name, email, datetime.utcnow().isoformat())
    )
    db.commit()

# ── OAuth helpers ─────────────────────────────────────────────────────────────

def make_flow() -> Flow:
    return Flow.from_client_config(
        {
            "web": {
                "client_id":     os.environ["GOOGLE_CLIENT_ID"],
                "client_secret": os.environ["GOOGLE_CLIENT_SECRET"],
                "auth_uri":      "https://accounts.google.com/o/oauth2/auth",
                "token_uri":     "https://oauth2.googleapis.com/token",
            }
        },
        scopes=gc.SCOPES,
        redirect_uri=os.environ["REDIRECT_URI"],
    )

# ── Auth routes ───────────────────────────────────────────────────────────────

@app.get("/auth/login")
async def auth_login(secret: str = ""):
    """
    Visit /auth/login?secret=YOUR_ADMIN_SECRET to kick off the Google OAuth flow.
    Protects against strangers overwriting your stored credentials.
    """
    if secret != os.environ.get("ADMIN_SECRET", ""):
        raise HTTPException(403, "Invalid secret")

    flow  = make_flow()
    state = secrets.token_urlsafe(16)
    auth_url, _ = flow.authorization_url(
        state=state,
        access_type='offline',
        prompt='consent',           # ensures refresh_token is always returned
        include_granted_scopes='true',
    )

    db = get_db()
    db.execute("INSERT OR REPLACE INTO oauth_states (state, created_at) VALUES (?,?)",
               (state, datetime.utcnow().isoformat()))
    db.commit()

    return RedirectResponse(auth_url)


@app.get("/auth/callback")
async def auth_callback(code: str, state: str):
    db = get_db()

    # Validate state to prevent CSRF
    row = db.execute("SELECT 1 FROM oauth_states WHERE state=?", (state,)).fetchone()
    if not row:
        raise HTTPException(400, "Invalid OAuth state")
    db.execute("DELETE FROM oauth_states WHERE state=?", (state,))

    flow = make_flow()
    flow.fetch_token(code=code)
    creds = flow.credentials

    token_json = gc.creds_to_json(creds)
    db.execute("DELETE FROM google_tokens")
    db.execute(
        "INSERT INTO google_tokens (token_json, updated_at) VALUES (?,?)",
        (token_json, datetime.utcnow().isoformat())
    )
    db.commit()

    return RedirectResponse("/")

# ── Booking routes ────────────────────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
async def booking_page(request: Request):
    db = get_db()
    if not is_authenticated(db):
        return HTMLResponse(
            "<h2 style='font-family:sans-serif;padding:40px'>Not set up yet. "
            "Visit <code>/auth/login?secret=YOUR_ADMIN_SECRET</code> to connect Google Calendar.</h2>",
            status_code=503,
        )

    config  = load_config()
    service = gc.get_service(db)
    slots   = gc.get_available_slots(service, config)

    days = [
        {
            'date':       day,
            'date_label': day.strftime('%A, %B %-d'),
            'slots': [{'iso': s.isoformat(), 'label': s.strftime('%-I:%M %p')}
                      for s in slots[day]],
        }
        for day in sorted(slots)
    ]

    return templates.TemplateResponse("book.html", {
        "request": request,
        "config":  config,
        "days":    days,
    })


@app.post("/book")
async def book_slot(
    request:  Request,
    slot_iso: str = Form(...),
    name:     str = Form(...),
    email:    str = Form(...),
):
    config = load_config()
    tz     = ZoneInfo(config['timezone'])

    try:
        slot = datetime.fromisoformat(slot_iso)
    except ValueError:
        raise HTTPException(400, "Invalid slot")

    now    = datetime.now(tz)
    max_dt = now + timedelta(days=config['lookahead_days'])
    if slot < now or slot > max_dt:
        raise HTTPException(400, "Slot out of range")

    async with _book_lock:
        db      = get_db()
        service = gc.get_service(db)

        if slot_taken(db, slot):
            return templates.TemplateResponse("book.html", {
                "request": request, "config": config, "days": [],
                "error": "That slot was just booked — please pick another time.",
            }, status_code=409)

        # Confirm slot still open on live calendar
        slots     = gc.get_available_slots(service, config)
        all_slots = [s for day_slots in slots.values() for s in day_slots]
        if not any(s.replace(microsecond=0) == slot.replace(microsecond=0)
                   for s in all_slots):
            return templates.TemplateResponse("book.html", {
                "request": request, "config": config, "days": [],
                "error": "That slot is no longer available — please pick another time.",
            }, status_code=409)

        record_booking(db, slot, name.strip(), email.strip())
        try:
            gc.create_event(service, config, slot, name.strip(), email.strip())
        except Exception as e:
            db.execute("DELETE FROM bookings WHERE slot_iso=?", (slot.isoformat(),))
            db.commit()
            raise HTTPException(500, f"Could not create calendar event: {e}")

    return RedirectResponse(
        f"/confirmed?name={name}&slot={slot_iso}", status_code=303
    )


@app.get("/confirmed", response_class=HTMLResponse)
async def confirmed(request: Request, name: str, slot: str):
    config = load_config()
    tz     = ZoneInfo(config['timezone'])
    try:
        slot_label = datetime.fromisoformat(slot).astimezone(tz).strftime(
            '%A, %B %-d at %-I:%M %p')
    except Exception:
        slot_label = slot

    return templates.TemplateResponse("confirmed.html", {
        "request": request, "config": config,
        "name": name, "slot_label": slot_label,
    })


@app.get("/health")
async def health():
    return {"status": "ok"}
