"""
cal-book — self-hosted Calendly alternative
"""

import asyncio
import os
import sqlite3
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import yaml
from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

import calendar_client as gc

# ── Setup ─────────────────────────────────────────────────────────────────────

app       = FastAPI()
templates = Jinja2Templates(directory="templates")
app.mount("/static", StaticFiles(directory="static"), name="static")

_book_lock = asyncio.Lock()   # prevents race-condition double-bookings

def load_config():
    path = os.environ.get('CONFIG_PATH', 'config.yaml')
    with open(path) as f:
        cfg = yaml.safe_load(f)
    cfg.setdefault('meeting_duration_minutes', 30)
    cfg.setdefault('buffer_minutes', 15)
    cfg.setdefault('lookahead_days', 14)
    cfg.setdefault('slot_interval_minutes', cfg['meeting_duration_minutes'])
    cfg.setdefault('timezone', 'America/New_York')
    cfg.setdefault('meeting_title', 'Meeting with {name}')
    cfg.setdefault('confirmation_message', 'Looking forward to connecting.')
    return cfg

def db_connect():
    db = sqlite3.connect('bookings.db')
    db.execute("""
        CREATE TABLE IF NOT EXISTS bookings (
            slot_iso  TEXT PRIMARY KEY,
            name      TEXT,
            email     TEXT,
            booked_at TEXT
        )
    """)
    db.commit()
    return db

def slot_is_taken(db, slot: datetime) -> bool:
    row = db.execute(
        "SELECT 1 FROM bookings WHERE slot_iso=?", (slot.isoformat(),)
    ).fetchone()
    return row is not None

def record_booking(db, slot: datetime, name: str, email: str):
    db.execute(
        "INSERT INTO bookings (slot_iso, name, email, booked_at) VALUES (?,?,?,?)",
        (slot.isoformat(), name, email, datetime.utcnow().isoformat())
    )
    db.commit()

# ── Routes ────────────────────────────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
async def booking_page(request: Request):
    config  = load_config()
    service = gc.get_service()
    slots   = gc.get_available_slots(service, config)
    tz      = ZoneInfo(config['timezone'])

    # Group slots by date for the template
    days = []
    for day in sorted(slots):
        days.append({
            'date':       day,
            'date_label': day.strftime('%A, %B %-d'),
            'slots': [
                {
                    'iso':   s.isoformat(),
                    'label': s.strftime('%-I:%M %p'),
                }
                for s in slots[day]
            ]
        })

    return templates.TemplateResponse("book.html", {
        "request":  request,
        "config":   config,
        "days":     days,
    })

@app.post("/book")
async def book_slot(
    request: Request,
    slot_iso: str  = Form(...),
    name:     str  = Form(...),
    email:    str  = Form(...),
):
    config = load_config()
    tz     = ZoneInfo(config['timezone'])

    try:
        slot = datetime.fromisoformat(slot_iso)
    except ValueError:
        raise HTTPException(400, "Invalid slot")

    # Enforce lookahead window
    now     = datetime.now(tz)
    max_dt  = now + timedelta(days=config['lookahead_days'])
    if slot < now or slot > max_dt:
        raise HTTPException(400, "Slot out of range")

    async with _book_lock:
        db = db_connect()

        # Double-check: local DB + live calendar
        if slot_is_taken(db, slot):
            return templates.TemplateResponse("book.html", {
                "request": request,
                "config":  config,
                "error":   "That slot was just booked — please pick another time.",
                "days":    [],
            }, status_code=409)

        service = gc.get_service()
        slots   = gc.get_available_slots(service, config)
        all_slots = [s for day_slots in slots.values() for s in day_slots]

        # Normalize for comparison (strip sub-second)
        slot_cmp = slot.replace(microsecond=0)
        if not any(s.replace(microsecond=0) == slot_cmp for s in all_slots):
            return templates.TemplateResponse("book.html", {
                "request": request,
                "config":  config,
                "error":   "That slot is no longer available — please pick another time.",
                "days":    [],
            }, status_code=409)

        # Lock it in DB before creating calendar event
        record_booking(db, slot, name.strip(), email.strip())

        try:
            gc.create_event(service, config, slot, name.strip(), email.strip())
        except Exception as e:
            # Roll back the DB record if GCal creation fails
            db.execute("DELETE FROM bookings WHERE slot_iso=?", (slot.isoformat(),))
            db.commit()
            raise HTTPException(500, f"Could not create calendar event: {e}")

    return RedirectResponse(
        url=f"/confirmed?name={name}&slot={slot_iso}",
        status_code=303,
    )

@app.get("/confirmed", response_class=HTMLResponse)
async def confirmed(request: Request, name: str, slot: str):
    config = load_config()
    tz     = ZoneInfo(config['timezone'])
    try:
        slot_dt = datetime.fromisoformat(slot)
        slot_label = slot_dt.astimezone(tz).strftime('%A, %B %-d at %-I:%M %p')
    except Exception:
        slot_label = slot

    return templates.TemplateResponse("confirmed.html", {
        "request":    request,
        "config":     config,
        "name":       name,
        "slot_label": slot_label,
    })

@app.get("/health")
async def health():
    return {"status": "ok"}
