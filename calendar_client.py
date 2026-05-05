"""
Google Calendar helpers — availability + event creation.
Credentials come from the database (stored after OAuth flow).
"""

import json
from datetime import datetime, timedelta, date
from zoneinfo import ZoneInfo

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

SCOPES = [
    'https://www.googleapis.com/auth/calendar',
    'openid',
    'https://www.googleapis.com/auth/userinfo.email',
]


def creds_from_db(db) -> Credentials:
    # Prefer DB (set after web OAuth flow); fall back to GOOGLE_TOKEN env var
    # (set by setup.py via .env or Fly secrets).
    row = db.execute("SELECT token_json FROM google_tokens LIMIT 1").fetchone()
    token_json = row[0] if row else os.environ.get('GOOGLE_TOKEN')
    if not token_json:
        raise RuntimeError("Not authenticated — run setup.py or visit /auth/login")
    creds = Credentials.from_authorized_user_info(json.loads(token_json), SCOPES)
    if creds.expired and creds.refresh_token:
        creds.refresh(Request())
        refreshed = creds_to_json(creds)
        if row:
            db.execute("UPDATE google_tokens SET token_json=?", (refreshed,))
        else:
            db.execute(
                "INSERT INTO google_tokens (token_json, updated_at) VALUES (?,?)",
                (refreshed, __import__('datetime').datetime.utcnow().isoformat())
            )
        db.commit()
    return creds


def creds_to_json(creds: Credentials) -> str:
    return json.dumps({
        'token':         creds.token,
        'refresh_token': creds.refresh_token,
        'token_uri':     creds.token_uri,
        'client_id':     creds.client_id,
        'client_secret': creds.client_secret,
        'scopes':        creds.scopes,
    })


def get_service(db):
    return build('calendar', 'v3', credentials=creds_from_db(db))


def get_busy(service, calendar_id: str, day: date, tz: ZoneInfo) -> list:
    day_start = datetime(day.year, day.month, day.day, 0, 0, tzinfo=tz)
    day_end   = day_start + timedelta(days=1)
    resp = service.freebusy().query(body={
        'timeMin': day_start.isoformat(),
        'timeMax': day_end.isoformat(),
        'items':   [{'id': calendar_id}],
    }).execute()
    blocks = resp.get('calendars', {}).get(calendar_id, {}).get('busy', [])
    result = []
    for b in blocks:
        s = datetime.fromisoformat(b['start'].replace('Z', '+00:00')).astimezone(tz)
        e = datetime.fromisoformat(b['end'].replace('Z',   '+00:00')).astimezone(tz)
        result.append((s, e))
    return result


def get_available_slots(service, config: dict) -> dict:
    tz        = ZoneInfo(config['timezone'])
    duration  = timedelta(minutes=config['meeting_duration_minutes'])
    buffer    = timedelta(minutes=config['buffer_minutes'])
    interval  = timedelta(minutes=config.get('slot_interval_minutes',
                                              config['meeting_duration_minutes']))
    today     = datetime.now(tz).date()
    day_names = ['monday','tuesday','wednesday','thursday',
                 'friday','saturday','sunday']
    result    = {}

    for offset in range(1, config['lookahead_days'] + 1):
        day     = today + timedelta(days=offset)
        day_key = day_names[day.weekday()]
        if day_key not in config['working_hours']:
            continue
        wh = config['working_hours'][day_key]
        work_start = datetime.strptime(wh['start'], '%H:%M').replace(
            year=day.year, month=day.month, day=day.day, tzinfo=tz)
        work_end = datetime.strptime(wh['end'], '%H:%M').replace(
            year=day.year, month=day.month, day=day.day, tzinfo=tz)

        busy   = get_busy(service, config['calendar_id'], day, tz)
        slots  = []
        cursor = work_start

        while cursor + duration <= work_end:
            slot_end = cursor + duration
            blocked  = any(
                cursor < (be + buffer) and (slot_end + buffer) > bs
                for bs, be in busy
            )
            if not blocked:
                slots.append(cursor)
            cursor += interval

        if slots:
            result[day] = slots

    return result


def create_event(service, config: dict, slot: datetime,
                 guest_name: str, guest_email: str) -> str:
    tz      = ZoneInfo(config['timezone'])
    end     = slot + timedelta(minutes=config['meeting_duration_minutes'])
    title   = config.get('meeting_title', 'Meeting with {name}').format(name=guest_name)
    created = service.events().insert(
        calendarId=config['calendar_id'],
        sendNotifications=True,
        body={
            'summary':     title,
            'description': config.get('confirmation_message', ''),
            'start': {'dateTime': slot.isoformat(), 'timeZone': config['timezone']},
            'end':   {'dateTime': end.isoformat(),  'timeZone': config['timezone']},
            'attendees': [
                {'email': config['owner_email'], 'responseStatus': 'accepted'},
                {'email': guest_email},
            ],
            'sendUpdates': 'all',
        },
    ).execute()
    return created.get('htmlLink', '')
