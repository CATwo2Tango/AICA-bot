# AICA Batch Tracker — Telegram Bot
### Monitors https://ai.icai.org/aica.php for new ICAI AICA Level-1 batches

---

## What This Bot Does

| Feature | Details |
|---|---|
| 🔔 New batch alerts | Notifies subscribers whenever a new batch appears |
| 🟢 Registration status | Shows Open / Closed for each batch |
| 🪑 Seat availability | Batch limit & available seats |
| 📅 Dates & location | For every listed batch |
| 🔍 City filter | Search batches by city name |
| ⏱️ Auto-refresh | Checks ICAI site every 30 minutes |

---

## Step 1 — Create Your Bot on Telegram

1. Open Telegram → search **@BotFather**
2. Send `/newbot`
3. Name: `AICA Batch Tracker`
4. Username: `AICAbatchbot` (must end in `bot`)
5. Copy the **API Token** — you will need it below

---

## Step 2 — Install Python Dependencies

```bash
# Python 3.10+ required
pip install -r requirements.txt
```

---

## Step 3 — Set Your Bot Token

**Option A — Environment variable (recommended):**
```bash
export TELEGRAM_BOT_TOKEN="your_token_here"
```

**Option B — Edit bot.py directly:**
```python
BOT_TOKEN = "your_token_here"    # line ~30 in bot.py
```

---

## Step 4 — Run the Bot

```bash
python bot.py
```

The bot will:
- Start polling Telegram for messages
- Immediately check ICAI site for current batches
- Check every 30 minutes thereafter
- Alert all subscribers when new batches appear

---

## Bot Commands

| Command | What it does |
|---|---|
| `/start` | Welcome message & command list |
| `/batches` | Show ALL current batches |
| `/open` | Only batches with registration OPEN |
| `/location Pune` | Batches in a specific city |
| `/seats` | Batches that still have seats available |
| `/alert` | Subscribe to new batch notifications |
| `/unalert` | Unsubscribe from notifications |
| `/refresh` | Force re-check of ICAI website |
| `/help` | Show command list |

**Tip:** You can also just type a city name (e.g. *Pune*, *Mumbai*) without the `/location` command.

---

## Deployment Options (24×7)

### Option A — Railway.app (Free, easiest)
1. Push files to GitHub
2. Connect GitHub repo to [railway.app](https://railway.app)
3. Add environment variable `TELEGRAM_BOT_TOKEN`
4. Deploy

### Option B — Render.com (Free)
1. Push to GitHub
2. Create a new "Background Worker" on Render
3. Build command: `pip install -r requirements.txt`
4. Start command: `python bot.py`
5. Add env variable `TELEGRAM_BOT_TOKEN`

### Option C — VPS / AWS EC2
```bash
# Run as background service
nohup python bot.py &

# Or use screen
screen -S aicabot
python bot.py
# Ctrl+A, D to detach
```

### Option D — Systemd Service (Linux server)
```ini
# /etc/systemd/system/aicabot.service
[Unit]
Description=AICA Telegram Bot
After=network.target

[Service]
WorkingDirectory=/path/to/aica_bot
ExecStart=/usr/bin/python3 bot.py
Restart=always
Environment=TELEGRAM_BOT_TOKEN=your_token_here

[Install]
WantedBy=multi-user.target
```
```bash
sudo systemctl enable aicabot
sudo systemctl start aicabot
```

---

## File Structure

```
aica_bot/
├── bot.py              # Main bot — commands & scheduler
├── scraper.py          # Web scraper for ai.icai.org
├── requirements.txt    # Python dependencies
├── subscribers.json    # Auto-created — list of subscriber chat IDs
├── known_batches.json  # Auto-created — tracks known batch IDs
└── README.md           # This file
```

---

## How the New-Batch Alert Works

```
Every 30 min:
  Scrape ai.icai.org/aica.php
        ↓
  Compare batch IDs vs. known_batches.json
        ↓
  New ID found? → Send alert to all subscribers
        ↓
  Update known_batches.json
```

---

## Customisation

| What | Where | Default |
|---|---|---|
| Check interval | `CHECK_INTERVAL_MINUTES` in bot.py | 30 min |
| Cities list (text detection) | `handle_text()` in bot.py | 15 cities |
| Cache TTL | `CACHE_TTL` in scraper.py | 25 min |

---

## Notes
- The bot is **read-only** — it does NOT register anyone.  
  Registration must be done on the ICAI website.
- Seat counts require fetching individual detail pages (slower) — only used for `/seats` command.
- If ICAI changes their website layout, scraper.py may need updating.

---

*Built for monitoring ICAI AICA Level-1 course batches. Not affiliated with ICAI.*
