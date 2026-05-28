"""
AICA Batch Tracker Bot  —  AI-Powered Edition
==============================================
Monitors https://ai.icai.org/aica.php for new AICA Level-1 batches.

Requirements:
    pip install -r requirements.txt

Environment variables needed:
    TELEGRAM_BOT_TOKEN   — from @BotFather on Telegram
"""

import os
import json
import logging
import httpx
from datetime import datetime
from telegram import Update
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    filters,
    ContextTypes,
)
from apscheduler.schedulers.asyncio import AsyncIOScheduler
import scraper

# ─────────────────────────────────────────────
# Logging
# ─────────────────────────────────────────────
logging.basicConfig(
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────
# Config
# ─────────────────────────────────────────────
BOT_TOKEN         = os.environ.get("TELEGRAM_BOT_TOKEN", "YOUR_BOT_TOKEN_HERE")
CHECK_INTERVAL_MINUTES = 30
SUBSCRIBERS_FILE   = "subscribers.json"
KNOWN_BATCHES_FILE = "known_batches.json"


# ─────────────────────────────────────────────
# Persistence helpers
# ─────────────────────────────────────────────

def load_json(filepath: str, default):
    if os.path.exists(filepath):
        with open(filepath) as f:
            return json.load(f)
    return default

def save_json(filepath: str, data):
    with open(filepath, "w") as f:
        json.dump(data, f, indent=2)

# ─────────────────────────────────────────────
# Formatting helpers
# ─────────────────────────────────────────────

def format_batch(b: dict) -> str:
    reg_icon = "🟢 OPEN" if b["registration_open"] else "🔴 CLOSED"
    seats = b.get("available_seats")
    total = b.get("batch_limit")
    seat_str = f"🪑 Seats: {seats} / {total} available" if seats is not None else "🪑 Seats: (use /seats for count)"
    venue = b.get("venue", "")
    venue_line = f"📍 Venue: {venue}" if venue else f"📍 Location: {b['location']}"
    return (
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"📋 *{b['batch_name']}*\n"
        f"📅 Dates : {b['dates']}\n"
        f"{venue_line}\n"
        f"{seat_str}\n"
        f"Registration : {reg_icon}\n"
        f"💰 Fee : ₹5,000 + GST\n"
        f"🔗 [More Details]({b['detail_url']})\n"
    )

def format_batch_list(batches: list, title: str) -> list[str]:
    header = f"*{title}*\n_(Source: ai.icai.org/aica.php)_\n\n"
    chunks, current = [], header
    for b in batches:
        block = format_batch(b)
        if len(current) + len(block) > 4000:
            chunks.append(current)
            current = block
        else:
            current += block
    if current.strip():
        chunks.append(current)
    return chunks if chunks else [header + "_No batches found._"]

def batches_to_text_summary(batches: list) -> str:
    """Convert batch list to a compact text for the AI system prompt."""
    lines = []
    for b in batches:
        reg = "OPEN" if b["registration_open"] else "CLOSED"
        seats = b.get("available_seats")
        seat_info = f", Seats available: {seats}" if seats is not None else ""
        lines.append(
            f"- {b['batch_name']} | Location: {b['location']} | "
            f"Dates: {b['dates']} | Registration: {reg}{seat_info} | "
            f"URL: {b['detail_url']}"
        )
    return "\n".join(lines)

# ─────────────────────────────────────────────
# AI handler — Claude answers plain-text queries
# ─────────────────────────────────────────────

async def ask_claude(user_question: str, batches: list) -> str:
    """
    Send user question + live batch data to Claude API.
    Returns Claude's answer as plain text (Telegram Markdown safe).
    """
    batch_summary = batches_to_text_summary(batches)
    today = datetime.now().strftime("%d %B %Y")

    system_prompt = f"""You are a helpful assistant for the ICAI AICA Level-1 Certificate Course on AI for Chartered Accountants.
Today's date is {today}.

You have access to the LIVE batch data scraped from https://ai.icai.org/aica.php right now.
Here is the complete current batch list:

{batch_summary}

Answer the user's question using ONLY this data. Be concise and helpful.
Rules:
- registration_open=True means the main ICAI page did NOT show a closed banner.
  However seats may still be 0. If available_seats=0 or available_seats is not shown,
  treat that batch as CLOSED / full — do NOT call it open.
- A batch is truly OPEN only if registration_open=True AND available_seats is either
  unknown (None) or greater than 0.
- If user asks about a city, search case-insensitively and match partial names:
  "pimpri" matches "Pimpri Chinchwad", "gbn" matches "GBN(Noida)", etc.
- If registration is CLOSED or seats are 0, clearly say so and suggest /alert
- If no batches match, say so and list cities that DO have open batches
- Always mention the registration link when relevant
- Format reply using plain text with emojis — do NOT use markdown bold (**text**)
- Keep replies under 300 words unless listing many batches
- If asked something unrelated to AICA batches, politely redirect
"""

    try:
        message = ai_client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=1024,
            system=system_prompt,
            messages=[{"role": "user", "content": user_question}],
        )
        return message.content[0].text
    except anthropic.APIError as e:
        logger.error(f"Anthropic API error: {e}")
        return "Sorry, AI assistant is temporarily unavailable. Please use /batches, /open, or /location commands instead."
    except Exception as e:
        logger.error(f"Unexpected AI error: {e}")
        return "Something went wrong. Please try again or use /help for commands."

# ─────────────────────────────────────────────
# Command: /start
# ─────────────────────────────────────────────

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (
        "🎓 *AICA Batch Tracker Bot* _(AI-Powered)_\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        "Monitors ICAI's *Certificate Course on AI for CAs (AICA Level-1)*\n\n"
        "💬 *Just ask me anything in plain English!*\n"
        "_Examples:_\n"
        "• Is there a batch in Pune?\n"
        "• Which cities have open registration?\n"
        "• How many seats are left in Mumbai?\n"
        "• Any batch in June near Delhi?\n"
        "• Is Pimpri Chinchwad batch open?\n\n"
        "*Or use commands:*\n"
        "/batches — All current batches\n"
        "/open — Open registration only\n"
        "/location <city> — Batches by city\n"
        "/seats — Batches with seats available\n"
        "/alert — Subscribe to new batch alerts\n"
        "/unalert — Unsubscribe from alerts\n"
        "/refresh — Force re-check ICAI website\n"
        "/help — Show this menu\n"
    )
    await update.message.reply_text(text, parse_mode="Markdown", disable_web_page_preview=True)

# ─────────────────────────────────────────────
# Command: /batches
# ─────────────────────────────────────────────

async def cmd_batches(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("⏳ Fetching batch list from ICAI website…")
    batches = scraper.get_all_batches()
    if not batches:
        await update.message.reply_text("❌ Could not fetch batches. Try again later.")
        return
    chunks = format_batch_list(batches, f"All AICA Batches ({len(batches)} found)")
    for chunk in chunks:
        await update.message.reply_text(chunk, parse_mode="Markdown", disable_web_page_preview=True)

# ─────────────────────────────────────────────
# Command: /open
# ─────────────────────────────────────────────

async def cmd_open(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "⏳ Checking registration status and seat availability…\n"
        "_(This fetches each batch's detail page — may take 30–60 sec)_",
        parse_mode="Markdown",
    )
    batches = scraper.get_all_batches_with_seats()   # accurate: checks seats too
    open_batches = [b for b in batches if b["registration_open"]]
    if not open_batches:
        await update.message.reply_text(
            "😔 No batches with open registration right now.\n"
            "Use /alert to get notified when new batches open."
        )
        return
    chunks = format_batch_list(open_batches, f"🟢 Open Registration Batches ({len(open_batches)} found)")
    for chunk in chunks:
        await update.message.reply_text(chunk, parse_mode="Markdown", disable_web_page_preview=True)

# ─────────────────────────────────────────────
# Command: /location <city>
# ─────────────────────────────────────────────

async def cmd_location(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text(
            "Please provide a city name.\nExample: `/location Pune`",
            parse_mode="Markdown",
        )
        return
    city = " ".join(context.args).strip().lower()
    await update.message.reply_text(f"⏳ Searching for batches in *{city.title()}*…", parse_mode="Markdown")
    batches = scraper.get_all_batches()
    city_batches = [b for b in batches if city in b["location"].lower()]
    if not city_batches:
        await update.message.reply_text(
            f"No batches found for *{city.title()}*.\n"
            "Try a different spelling or use /batches for the full list.",
            parse_mode="Markdown",
        )
        return
    chunks = format_batch_list(city_batches, f"Batches in {city.title()} ({len(city_batches)} found)")
    for chunk in chunks:
        await update.message.reply_text(chunk, parse_mode="Markdown", disable_web_page_preview=True)

# ─────────────────────────────────────────────
# Command: /seats
# ─────────────────────────────────────────────

async def cmd_seats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("⏳ Fetching seat availability (may take a moment)…")
    batches = scraper.get_all_batches_with_seats()
    with_seats = [b for b in batches if b.get("available_seats", 0) > 0 and b["registration_open"]]
    if not with_seats:
        await update.message.reply_text("😔 No batches with open seats found right now.")
        return
    chunks = format_batch_list(with_seats, f"🪑 Batches with Available Seats ({len(with_seats)} found)")
    for chunk in chunks:
        await update.message.reply_text(chunk, parse_mode="Markdown", disable_web_page_preview=True)

# ─────────────────────────────────────────────
# Command: /alert
# ─────────────────────────────────────────────

async def cmd_alert(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = str(update.effective_chat.id)
    subscribers = load_json(SUBSCRIBERS_FILE, [])
    if chat_id not in subscribers:
        subscribers.append(chat_id)
        save_json(SUBSCRIBERS_FILE, subscribers)
        await update.message.reply_text(
            f"✅ Subscribed! I check every {CHECK_INTERVAL_MINUTES} minutes "
            "and will alert you the moment a new batch appears.\n\n"
            "Use /unalert to unsubscribe."
        )
    else:
        await update.message.reply_text("ℹ️ You are already subscribed to alerts.")

# ─────────────────────────────────────────────
# Command: /unalert
# ─────────────────────────────────────────────

async def cmd_unalert(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = str(update.effective_chat.id)
    subscribers = load_json(SUBSCRIBERS_FILE, [])
    if chat_id in subscribers:
        subscribers.remove(chat_id)
        save_json(SUBSCRIBERS_FILE, subscribers)
        await update.message.reply_text("✅ Unsubscribed from alerts.")
    else:
        await update.message.reply_text("ℹ️ You were not subscribed.")

# ─────────────────────────────────────────────
# Command: /refresh
# ─────────────────────────────────────────────

async def cmd_refresh(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("⏳ Refreshing data from ICAI website…")
    batches = scraper.get_all_batches(force=True)
    await update.message.reply_text(
        f"✅ Refreshed! Found *{len(batches)}* batches.\n"
        "Ask me anything or use /batches to see all.",
        parse_mode="Markdown",
    )

# ─────────────────────────────────────────────
# Command: /help
# ─────────────────────────────────────────────

async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await cmd_start(update, context)

# ─────────────────────────────────────────────
# Scheduler: auto-check for new batches
# ─────────────────────────────────────────────

async def check_new_batches(app):
    logger.info("Scheduled check: fetching batches…")
    try:
        current_batches = scraper.get_all_batches(force=True)
    except Exception as e:
        logger.error(f"Scrape failed: {e}")
        return

    known = load_json(KNOWN_BATCHES_FILE, {})
    current_ids = {b["batch_id"]: b for b in current_batches}
    new_batches = [b for bid, b in current_ids.items() if bid not in known]

    if new_batches:
        logger.info(f"Found {len(new_batches)} new batch(es)!")
        subscribers = load_json(SUBSCRIBERS_FILE, [])
        alert_text = (
            f"🔔 *NEW AICA BATCH ALERT!*\n"
            f"_{len(new_batches)} new batch(es) just released!_\n\n"
        )
        for b in new_batches:
            alert_text += format_batch(b)
        for chat_id in subscribers:
            try:
                await app.bot.send_message(
                    chat_id=int(chat_id),
                    text=alert_text,
                    parse_mode="Markdown",
                    disable_web_page_preview=True,
                )
            except Exception as e:
                logger.warning(f"Could not send alert to {chat_id}: {e}")

    save_json(KNOWN_BATCHES_FILE, current_ids)
    logger.info(f"Known batches updated: {len(current_ids)} total")

# ─────────────────────────────────────────────
# Handle ALL plain-text messages → Claude AI
# ─────────────────────────────────────────────

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_text = update.message.text.strip()
    if not user_text:
        return

    # Show typing indicator
    await update.message.reply_text("🤖 Thinking…")

    # Get fresh batch data with accurate seat counts
    # Use basic list for speed; AI will note if seats unknown
    batches = scraper.get_all_batches()

    if not batches:
        await update.message.reply_text(
            "❌ Could not fetch batch data from ICAI website right now.\n"
            "Please try again in a moment or use /refresh."
        )
        return

    # Ask Claude
    answer = await ask_claude(user_text, batches)
    await update.message.reply_text(answer, disable_web_page_preview=True)

# ─────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────

def main():
    app = ApplicationBuilder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start",    cmd_start))
    app.add_handler(CommandHandler("help",     cmd_help))
    app.add_handler(CommandHandler("batches",  cmd_batches))
    app.add_handler(CommandHandler("open",     cmd_open))
    app.add_handler(CommandHandler("location", cmd_location))
    app.add_handler(CommandHandler("seats",    cmd_seats))
    app.add_handler(CommandHandler("alert",    cmd_alert))
    app.add_handler(CommandHandler("unalert",  cmd_unalert))
    app.add_handler(CommandHandler("refresh",  cmd_refresh))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))

    scheduler = AsyncIOScheduler()
    scheduler.add_job(
        check_new_batches,
        "interval",
        minutes=CHECK_INTERVAL_MINUTES,
        args=[app],
        next_run_time=datetime.now(),
    )
    scheduler.start()

    logger.info("AICA AI Bot is running…")
    app.run_polling()

if __name__ == "__main__":
    main()
