"""
Google Calendar helpers — availability + event creation.
"""

import json
import os
import tempfile
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

SCOPES = ['https://www.googleapis.com/auth/calendar']


def get_service():
    """Build a Calendar service, handling both file and env-var token."""
    token_json = os.environ.get('GOOGLE_TOKEN')
    creds_json = os.environ.get('GOOGLE_CREDENTIALS')

    if token_json:
        # Running on Fly.io — write secrets to temp files
        tf = tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False)
        tf.write(token_json); tf.close()
        token_file = tf.name
    elif os.path.exists('token.json'):
        token_file = 'token.json'
    else:
        raise RuntimeError("No Google token found. Run setup.py or set GOOGLE_TOKEN secret.")

    creds = Credentials.from_authorized_user_file(token_file, SCOPES)

    if creds.expired and creds.refresh_token:
        if creds_json:
            cf = tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False)
            cf.write(creds_json); cf.close()
            from google_auth_oauthlib.flow import InstalledAppFlow
        creds.refresh(Request())
        # Persist refreshed token back to env isn't possible, but Fly secrets
        # don't expire often — user re-runs set-secret if needed.

    return build('calendar', 'v3', credentials=creds)


def get_busy_blocks(service, calendar_id, day: date, tz: ZoneInfo) -> list[tuple]:
    """
    Return list of (start, end) datetime pairs for events on `day`.
    Uses freebusy query — fast and doesn't expose event details.
    """
    day_start = datetime(day.year, day.month, day.day, 0, 0, tzinfo=tz)
    day_end   = day_start + timedelta(days=1)

    body = {
        'timeMin': day_start.isoformat(),
        'timeMax': day_end.isoformat(),
        'items':   [{'id': calendar_id}],
    }
    resp   = service.freebusy().query(body=body).execute()
    blocks = resp.get('calendars', {}).get(calendar_id, {}).get('busy', [])

    result = []
    for b in blocks:
        s = datetime.fromisoformat(b['start'].replace('Z', '+00:00')).astimezone(tz)
        e = datetime.fromisoformat(b['end'].replace('Z',   '+00:00')).astimezone(tz)
        result.append((s, e))
    return result


def get_available_slots(service, config: dict) -> dict[date, list[datetime]]:
    """
    Return {date: [slot_datetime, ...]} for the lookahead window.
    Slots are in the owner's timezone.
    """
    tz           = ZoneInfo(config['timezone'])
    duration     = timedelta(minutes=config['meeting_duration_minutes'])
    buffer       = timedelta(minutes=config['buffer_minutes'])
    lookahead    = config['lookahead_days']
    calendar_id  = config['calendar_id']
    working_hrs  = config['working_hours']

    day_names = ['monday','tuesday','wednesday','thursday','friday','saturday','sunday']
    today     = datetime.now(tz).date()
    result    = {}

    for offset in range(1, lookahead + 1):      # start tomorrow
        day     = today + timedelta(days=offset)
        day_key = day_names[day.weekday()]

        if day_key not in working_hrs:
            continue

        wh         = working_hrs[day_key]
        work_start = datetime.strptime(wh['start'], '%H:%M').replace(
                         year=day.year, month=day.month, day=day.day,
                         tzinfo=tz)
        work_end   = datetime.strptime(wh['end'], '%H:%M').replace(
                         year=day.year, month=day.month, day=day.day,
                         tzinfo=tz)

        busy   = get_busy_blocks(service, calendar_id, day, tz)
        slots  = []
        cursor = work_start

        while cursor + duration <= work_end:
            slot_end = cursor + duration
            # Check overlap with any busy block (including buffer)
            blocked = any(
                cursor < (be + buffer) and (slot_end + buffer) > bs
                for bs, be in busy
            )
            if not blocked:
                slots.append(cursor)
            cursor += timedelta(minutes=config.get('slot_interval_minutes',
                                                    config['meeting_duration_minutes']))

        if slots:
            result[day] = slots

    return result


def create_event(service, config: dict, slot: datetime,
                 guest_name: str, guest_email: str) -> str:
    """Create the calendar event and return its HTML link."""
    tz       = ZoneInfo(config['timezone'])
    duration = timedelta(minutes=config['meeting_duration_minutes'])
    end      = slot + duration

    title = config.get('meeting_title', 'Meeting with {name}').format(name=guest_name)

    event = {
        'summary':     title,
        'description': config.get('confirmation_message', ''),
        'start':       {'dateTime': slot.isoformat(), 'timeZone': config['timezone']},
        'end':         {'dateTime': end.isoformat(),  'timeZone': config['timezone']},
        'attendees':   [
            {'email': config['owner_email'], 'responseStatus': 'accepted'},
            {'email': guest_email},
        ],
        'sendUpdates': 'all',   # Google sends invite emails automatically
    }

    created = service.events().insert(
        calendarId=config['calendar_id'],
        body=event,
        sendNotifications=True,
    ).execute()

    return created.get('htmlLink', '')
