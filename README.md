# free-cal

Self-hosted Calendly alternative. Free forever.

Share a booking link. People pick a time. It lands on your Google Calendar and they get an invite — no Calendly account, no monthly fee.

---

## Setup

### 1. Install

```bash
git clone https://github.com/YOUR_USERNAME/free-cal
cd free-cal
python3 -m venv venv
source venv/bin/activate      # Windows: venv\Scripts\activate
pip install -r requirements.txt
```

### 2. Get Google credentials

You need a free Google Cloud project — takes about 3 minutes.

1. Go to [console.cloud.google.com](https://console.cloud.google.com/)
2. Create a project (any name)
3. **APIs & Services → Enable APIs → Google Calendar API → Enable**
4. **Credentials → Create Credentials → OAuth client ID → Desktop app**
5. Copy the **Client ID** and **Client Secret** (two strings — no file download needed)

### 3. Run setup

```bash
python setup.py
```

This will:
- Ask for your Client ID and Client Secret
- Open a browser so you can sign in with Google
- Show your calendar list so you can pick the right one
- Walk you through the rest of the config
- Save everything to `.env` and `config.yaml`

### 4. Run

```bash
python run.py
# → http://localhost:8080
```

---

## Sharing your booking link

`localhost:8080` is only reachable on your machine. To give people a real URL:

**Free tunnel (easiest):**
```bash
# Cloudflare — no account needed
brew install cloudflared
cloudflared tunnel --url http://localhost:8080

# or ngrok
ngrok http 8080
```

Both give you a public HTTPS URL you can share. Run it alongside `python run.py`.

**Self-host permanently:** see [Deployment](#deployment) below.

---

## Configuration

Edit `config.yaml` to control your booking page:

```yaml
owner_name:  Your Name
owner_email: you@gmail.com
calendar_id: you@gmail.com

timezone: America/New_York     # IANA timezone

description: "30-minute intro call."   # optional subtitle on booking page

meeting_duration_minutes: 30
buffer_minutes: 15             # gap blocked around each meeting
lookahead_days: 14

working_hours:
  monday:    { start: "09:00", end: "18:00" }
  tuesday:   { start: "09:00", end: "18:00" }
  wednesday: { start: "09:00", end: "18:00" }
  thursday:  { start: "09:00", end: "18:00" }
  friday:    { start: "09:00", end: "17:00" }
  # omit saturday/sunday to block them

meeting_title: "Meeting with {name}"
confirmation_message: "Looking forward to connecting."
```

Restart `run.py` after changes.

---

## Deployment

### Fly.io (free, always on)

```bash
brew install flyctl
flyctl auth login

flyctl launch --name free-cal --region ewr --no-deploy

# Push your secrets (these come from .env after running setup.py)
flyctl secrets set \
  GOOGLE_TOKEN="$(grep GOOGLE_TOKEN .env | cut -d= -f2-)" \
  ADMIN_SECRET="$(grep ADMIN_SECRET .env | cut -d= -f2-)"

flyctl deploy
```

Your booking page is live at `https://free-cal.fly.dev`.

### Railway / Render / any Docker host

```bash
docker build -t free-cal .
docker run -p 8080:8080 \
  -e GOOGLE_TOKEN="$(grep GOOGLE_TOKEN .env | cut -d= -f2-)" \
  -v $(pwd)/config.yaml:/app/config.yaml \
  free-cal
```

---

## How it works

1. Reads your Google Calendar to find free time within your working hours
2. Shows available slots on the booking page
3. When someone books, creates a Calendar event with them as an attendee
4. Google sends them the invite automatically — no email configuration needed
5. Sync state stored in `bookings.db` (SQLite) to prevent double-bookings

## Files

| File | Purpose |
|------|---------|
| `setup.py` | One-time setup wizard |
| `run.py` | Start the server locally |
| `config.yaml` | Your booking page settings |
| `.env` | Secrets — **never commit this** |
| `bookings.db` | Local booking state — safe to delete |

## License

MIT
