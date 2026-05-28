"""
AICA Batch Tracker Bot
======================
Monitors https://ai.icai.org/aica.php for new batches and answers
queries about available seats, dates, locations and registration status.

Requirements:
    pip install python-telegram-bot requests beautifulsoup4 apscheduler

Usage:
    1. Set BOT_TOKEN in config.py (or as environment variable TELEGRAM_BOT_TOKEN)
    2. Run:  python bot.py
"""

import os
import json
import logging
import re
from datetime import datetime
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
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
BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "YOUR_BOT_TOKEN_HERE")
CHECK_INTERVAL_MINUTES = 30          # how often to check for new batches
SUBSCRIBERS_FILE = "subscribers.json"
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
    """Return a nicely formatted string for one batch."""
    reg_icon = "🟢 OPEN" if b["registration_open"] else "🔴 CLOSED"
    seats = b.get("available_seats")
    total = b.get("batch_limit")

    if seats is not None:
        seat_str = f"🪑 Seats: {seats} / {total} available"
    else:
        seat_str = "🪑 Seats: (tap More Details for count)"

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
    """Split long list into Telegram-safe chunks (max 4096 chars each)."""
    header = f"*{title}*\n_(Source: ai.icai.org/aica.php)_\n\n"
    chunks = []
    current = header
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


# ─────────────────────────────────────────────
# Command: /start
# ─────────────────────────────────────────────

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (
        "🎓 *AICA Batch Tracker Bot*\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        "This bot monitors ICAI's *Certificate Course on AI for CAs (AICA Level-1)*\n"
        "and alerts you whenever new batches are released.\n\n"
        "*Commands:*\n"
        "/batches — All current batches\n"
        "/open — Only batches with registration OPEN\n"
        "/location <city> — Batches for a specific city\n"
        "/seats — Batches that still have seats available\n"
        "/alert — Subscribe to new batch alerts\n"
        "/unalert — Unsubscribe from alerts\n"
        "/refresh — Force-refresh data from ICAI site\n"
        "/help — Show this menu\n"
    )
    await update.message.reply_text(text, parse_mode="Markdown", disable_web_page_preview=True)


# ─────────────────────────────────────────────
# Command: /batches  — show all batches
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
# Command: /open — registration open batches
# ─────────────────────────────────────────────

async def cmd_open(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("⏳ Checking registration status…")
    batches = scraper.get_all_batches()
    open_batches = [b for b in batches if b["registration_open"]]
    if not open_batches:
        await update.message.reply_text(
            "😔 No batches with open registration found right now.\n"
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
# Command: /seats — batches with seats available
# ─────────────────────────────────────────────

async def cmd_seats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("⏳ Fetching seat availability (this may take a moment)…")
    batches = scraper.get_all_batches_with_seats()   # fetches detail pages too
    with_seats = [b for b in batches if b.get("available_seats", 0) > 0 and b["registration_open"]]
    if not with_seats:
        await update.message.reply_text("😔 No batches with open seats found right now.")
        return
    chunks = format_batch_list(with_seats, f"🪑 Batches with Available Seats ({len(with_seats)} found)")
    for chunk in chunks:
        await update.message.reply_text(chunk, parse_mode="Markdown", disable_web_page_preview=True)


# ─────────────────────────────────────────────
# Command: /alert — subscribe
# ─────────────────────────────────────────────

async def cmd_alert(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = str(update.effective_chat.id)
    subscribers = load_json(SUBSCRIBERS_FILE, [])
    if chat_id not in subscribers:
        subscribers.append(chat_id)
        save_json(SUBSCRIBERS_FILE, subscribers)
        await update.message.reply_text(
            "✅ You are now subscribed!\n"
            f"I check for new batches every {CHECK_INTERVAL_MINUTES} minutes "
            "and will alert you immediately when a new batch is released.\n\n"
            "Use /unalert to unsubscribe."
        )
    else:
        await update.message.reply_text("ℹ️ You are already subscribed to alerts.")


# ─────────────────────────────────────────────
# Command: /unalert — unsubscribe
# ─────────────────────────────────────────────

async def cmd_unalert(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = str(update.effective_chat.id)
    subscribers = load_json(SUBSCRIBERS_FILE, [])
    if chat_id in subscribers:
        subscribers.remove(chat_id)
        save_json(SUBSCRIBERS_FILE, subscribers)
        await update.message.reply_text("✅ You have been unsubscribed from alerts.")
    else:
        await update.message.reply_text("ℹ️ You were not subscribed.")


# ─────────────────────────────────────────────
# Command: /refresh — manual re-scrape
# ─────────────────────────────────────────────

async def cmd_refresh(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("⏳ Refreshing data from ICAI website…")
    batches = scraper.get_all_batches(force=True)
    await update.message.reply_text(
        f"✅ Refreshed! Found *{len(batches)}* batches.\n"
        "Use /batches, /open, /seats, or /location to browse.",
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
    """Called by scheduler. Compares current batches vs. known ones and alerts subscribers."""
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
            f"_{len(new_batches)} new batch(es) just released on ICAI website_\n\n"
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

    # Update known batches store
    save_json(KNOWN_BATCHES_FILE, current_ids)
    logger.info(f"Known batches updated: {len(current_ids)} total")


# ─────────────────────────────────────────────
# Handle plain text messages
# ─────────────────────────────────────────────

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.lower()
    if any(w in text for w in ["pune", "mumbai", "delhi", "chennai", "bengaluru",
                                "hyderabad", "kolkata", "ahmedabad", "surat", "noida",
                                "thane", "vasai", "chandigarh", "indore", "jaipur"]):
        context.args = [update.message.text.strip()]
        await cmd_location(update, context)
    elif "open" in text or "available" in text:
        await cmd_open(update, context)
    elif "seat" in text:
        await cmd_seats(update, context)
    else:
        await update.message.reply_text(
            "I didn't understand that. Use /help to see available commands.\n"
            "Tip: You can also just type a city name like *Pune* or *Mumbai*!",
            parse_mode="Markdown",
        )


# ─────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────

def main():
    app = ApplicationBuilder().token(BOT_TOKEN).build()

    # Register handlers
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CommandHandler("batches", cmd_batches))
    app.add_handler(CommandHandler("open", cmd_open))
    app.add_handler(CommandHandler("location", cmd_location))
    app.add_handler(CommandHandler("seats", cmd_seats))
    app.add_handler(CommandHandler("alert", cmd_alert))
    app.add_handler(CommandHandler("unalert", cmd_unalert))
    app.add_handler(CommandHandler("refresh", cmd_refresh))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))

    # Scheduler for periodic checks
    scheduler = AsyncIOScheduler()
    scheduler.add_job(
        check_new_batches,
        "interval",
        minutes=CHECK_INTERVAL_MINUTES,
        args=[app],
        next_run_time=datetime.now(),   # run immediately on startup too
    )
    scheduler.start()

    logger.info("AICA Bot is running…")
    app.run_polling()


if __name__ == "__main__":
    main()
