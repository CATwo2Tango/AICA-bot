"""
AICA Batch Tracker Bot
======================
Monitors https://ai.icai.org/aica.php for new AICA Level-1 batches.

Requirements:
    pip install -r requirements.txt

Environment variables needed:
    TELEGRAM_BOT_TOKEN   — from @BotFather on Telegram
"""

import os
import json
import logging
from datetime import datetime
from telegram import Update, ReplyKeyboardRemove
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    ConversationHandler,
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
BOT_TOKEN              = os.environ.get("TELEGRAM_BOT_TOKEN", "YOUR_BOT_TOKEN_HERE")
CHECK_INTERVAL_MINUTES = 30
SUBSCRIBERS_FILE       = "subscribers.json"
KNOWN_BATCHES_FILE     = "known_batches.json"
USER_LOCATIONS_FILE    = "user_locations.json"

# ConversationHandler state
WAITING_FOR_CITY = 1


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

def get_user_location(chat_id: str) -> str | None:
    return load_json(USER_LOCATIONS_FILE, {}).get(chat_id)

def set_user_location(chat_id: str, city: str):
    locations = load_json(USER_LOCATIONS_FILE, {})
    locations[chat_id] = city.strip().lower()
    save_json(USER_LOCATIONS_FILE, locations)


# ─────────────────────────────────────────────
# Formatting helpers
# ─────────────────────────────────────────────

def _seat_bar(available: int, total: int) -> str:
    """Visual bar e.g. ████░░░░"""
    if total == 0:
        return ""
    filled = round((available / total) * 8)
    return "█" * filled + "░" * (8 - filled)

def format_batch(b: dict) -> str:
    reg_icon = "🟢 OPEN" if b["registration_open"] else "🔴 CLOSED"

    seats = b.get("available_seats")
    total = b.get("batch_limit")

    if seats is not None and total is not None:
        bar      = _seat_bar(seats, total)
        seat_str = f"🪑 Seats : {seats} / {total}  {bar}"
    elif seats is not None:
        seat_str = f"🪑 Seats available : {seats}"
    else:
        seat_str = "🪑 Seats : use /seats for count"

    venue     = b.get("venue", "")
    venue_line = f"📍 Venue    : {venue}" if venue else f"📍 Location : {b['location']}"

    return (
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"📋 *{b['batch_name']}*\n"
        f"📅 Dates        : {b['dates']}\n"
        f"{venue_line}\n"
        f"{seat_str}\n"
        f"Registration : {reg_icon}\n"
        f"💰 Fee          : ₹5,000 + GST\n"
        f"🔗 [More Details]({b['detail_url']})\n"
    )

def format_batch_list(batches: list, title: str) -> list[str]:
    """Split into Telegram-safe chunks (≤ 4000 chars)."""
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


# ─────────────────────────────────────────────
# Command: /start  &  /help
# ─────────────────────────────────────────────

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id    = str(update.effective_chat.id)
    saved_city = get_user_location(chat_id)
    city_line  = f"📌 Your saved location : *{saved_city.title()}*\n" if saved_city else ""

    text = (
        "🎓 *AICA Batch Tracker Bot*\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        "Monitors ICAI's *Certificate Course on AI for CAs (AICA Level-1)*\n\n"
        f"{city_line}"
        "*Commands:*\n"
        "/location — Search batches by city with seat counts\n"
        "/setlocation — Save your preferred city\n"
        "/mylocation — Show batches for your saved city\n"
        "/batches — All current batches\n"
        "/open — Open registration batches only\n"
        "/seats — All batches that still have seats\n"
        "/alert — Subscribe to new batch alerts\n"
        "/unalert — Unsubscribe from alerts\n"
        "/refresh — Force re-check ICAI website\n"
        "/help — Show this menu\n"
    )
    await update.message.reply_text(
        text, parse_mode="Markdown", disable_web_page_preview=True
    )

async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await cmd_start(update, context)


# ─────────────────────────────────────────────
# Command: /location  (conversation flow)
# ─────────────────────────────────────────────

async def cmd_location_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """If city given inline use it, otherwise ask."""
    if context.args:
        city = " ".join(context.args).strip()
        return await _show_location_batches(update, context, city)

    await update.message.reply_text(
        "📍 *Which city are you looking for?*\n\n"
        "Type the city name — I will show all batches with live seat counts.\n"
        "_Examples: Pune, Mumbai, Bengaluru, Hyderabad, Chennai, Delhi…_\n\n"
        "Send /cancel to go back.",
        parse_mode="Markdown",
        reply_markup=ReplyKeyboardRemove(),
    )
    return WAITING_FOR_CITY

async def cmd_location_received(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """User typed the city name."""
    city = update.message.text.strip()
    if city.lower() in ("/cancel", "cancel"):
        await update.message.reply_text(
            "Cancelled. Use /location to try again.",
            reply_markup=ReplyKeyboardRemove(),
        )
        return ConversationHandler.END
    return await _show_location_batches(update, context, city)

async def _show_location_batches(
    update: Update, context: ContextTypes.DEFAULT_TYPE, city: str
):
    """Fetch seat-accurate data and display batches for the given city."""
    city_lower = city.strip().lower()

    await update.message.reply_text(
        f"⏳ Fetching batches for *{city.strip().title()}* with live seat counts…\n"
        "_(Checks each batch detail page — may take 30–60 sec)_",
        parse_mode="Markdown",
        reply_markup=ReplyKeyboardRemove(),
    )

    all_batches  = scraper.get_all_batches_with_seats()
    city_batches = [
        b for b in all_batches
        if city_lower in b["location"].lower()
        or city_lower in b["batch_name"].lower()
    ]

    if not city_batches:
        all_cities = sorted(set(b["location"].title() for b in all_batches))
        await update.message.reply_text(
            f"😔 No batches found for *{city.strip().title()}*.\n\n"
            f"*Cities with batches currently listed:*\n"
            + ", ".join(all_cities) + "\n\n"
            "Try /location again with one of the above.",
            parse_mode="Markdown",
        )
        return ConversationHandler.END

    open_batches   = [b for b in city_batches if b["registration_open"]]
    closed_batches = [b for b in city_batches if not b["registration_open"]]

    title = (
        f"📍 Batches in {city.strip().title()} "
        f"({len(city_batches)} found — "
        f"🟢 {len(open_batches)} open, 🔴 {len(closed_batches)} closed)"
    )

    # Open batches first, then closed
    chunks = format_batch_list(open_batches + closed_batches, title)
    for chunk in chunks:
        await update.message.reply_text(
            chunk, parse_mode="Markdown", disable_web_page_preview=True
        )

    if open_batches:
        await update.message.reply_text(
            f"💾 To save *{city.strip().title()}* as your preferred city:\n"
            f"`/setlocation {city.strip().title()}`\n"
            "Then use /mylocation anytime for instant results.",
            parse_mode="Markdown",
        )

    return ConversationHandler.END

async def cmd_location_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Cancelled.", reply_markup=ReplyKeyboardRemove())
    return ConversationHandler.END


# ─────────────────────────────────────────────
# Command: /setlocation
# ─────────────────────────────────────────────

async def cmd_setlocation(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = str(update.effective_chat.id)

    if not context.args:
        saved = get_user_location(chat_id)
        if saved:
            await update.message.reply_text(
                f"📌 Your saved location is *{saved.title()}*.\n\n"
                "To change: `/setlocation CityName`",
                parse_mode="Markdown",
            )
        else:
            await update.message.reply_text(
                "No location saved yet.\n"
                "Usage: `/setlocation CityName`\n"
                "Example: `/setlocation Pune`",
                parse_mode="Markdown",
            )
        return

    city = " ".join(context.args).strip()
    set_user_location(chat_id, city)
    await update.message.reply_text(
        f"✅ Saved! Your preferred location is now *{city.title()}*.\n\n"
        "Use /mylocation anytime to see batches with live seat counts.",
        parse_mode="Markdown",
    )


# ─────────────────────────────────────────────
# Command: /mylocation
# ─────────────────────────────────────────────

async def cmd_mylocation(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = str(update.effective_chat.id)
    city    = get_user_location(chat_id)

    if not city:
        await update.message.reply_text(
            "No preferred location saved yet.\n"
            "Use `/setlocation CityName` to save one.\n"
            "Example: `/setlocation Pune`",
            parse_mode="Markdown",
        )
        return

    await _show_location_batches(update, context, city)


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
        await update.message.reply_text(
            chunk, parse_mode="Markdown", disable_web_page_preview=True
        )


# ─────────────────────────────────────────────
# Command: /open
# ─────────────────────────────────────────────

async def cmd_open(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "⏳ Checking registration status and seat availability…\n"
        "_(Fetches each batch detail page — may take 30–60 sec)_",
        parse_mode="Markdown",
    )
    batches      = scraper.get_all_batches_with_seats()
    open_batches = [b for b in batches if b["registration_open"]]
    if not open_batches:
        await update.message.reply_text(
            "😔 No batches with open registration right now.\n"
            "Use /alert to get notified when new batches open."
        )
        return
    chunks = format_batch_list(
        open_batches, f"🟢 Open Registration Batches ({len(open_batches)} found)"
    )
    for chunk in chunks:
        await update.message.reply_text(
            chunk, parse_mode="Markdown", disable_web_page_preview=True
        )


# ─────────────────────────────────────────────
# Command: /seats
# ─────────────────────────────────────────────

async def cmd_seats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "⏳ Fetching seat availability for all batches…\n"
        "_(May take 30–60 sec)_",
        parse_mode="Markdown",
    )
    batches    = scraper.get_all_batches_with_seats()
    with_seats = [
        b for b in batches
        if b.get("available_seats", 0) > 0 and b["registration_open"]
    ]
    if not with_seats:
        await update.message.reply_text(
            "😔 No batches with open seats found right now.\n"
            "Use /alert to get notified when new batches open."
        )
        return
    chunks = format_batch_list(
        with_seats, f"🪑 Batches with Available Seats ({len(with_seats)} found)"
    )
    for chunk in chunks:
        await update.message.reply_text(
            chunk, parse_mode="Markdown", disable_web_page_preview=True
        )


# ─────────────────────────────────────────────
# Command: /alert
# ─────────────────────────────────────────────

async def cmd_alert(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id     = str(update.effective_chat.id)
    subscribers = load_json(SUBSCRIBERS_FILE, [])
    if chat_id not in subscribers:
        subscribers.append(chat_id)
        save_json(SUBSCRIBERS_FILE, subscribers)
        await update.message.reply_text(
            f"✅ Subscribed! I check every {CHECK_INTERVAL_MINUTES} minutes\n"
            "and will alert you the moment a new batch appears.\n\n"
            "Use /unalert to unsubscribe."
        )
    else:
        await update.message.reply_text("ℹ️ You are already subscribed to alerts.")


# ─────────────────────────────────────────────
# Command: /unalert
# ─────────────────────────────────────────────

async def cmd_unalert(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id     = str(update.effective_chat.id)
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
        "Use /batches, /open, /seats, or /location to browse.",
        parse_mode="Markdown",
    )


# ─────────────────────────────────────────────
# Handle unrecognised plain text
# ─────────────────────────────────────────────

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "I only respond to commands. Use /help to see the full list.\n\n"
        "Quick picks:\n"
        "/location — find batches by city\n"
        "/open — open registration only\n"
        "/seats — batches with seats available",
    )


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

    known       = load_json(KNOWN_BATCHES_FILE, {})
    current_ids = {b["batch_id"]: b for b in current_batches}
    new_batches = [b for bid, b in current_ids.items() if bid not in known]

    if new_batches:
        logger.info(f"Found {len(new_batches)} new batch(es)!")
        subscribers    = load_json(SUBSCRIBERS_FILE, [])
        user_locations = load_json(USER_LOCATIONS_FILE, {})

        base_alert = (
            f"🔔 *NEW AICA BATCH ALERT!*\n"
            f"_{len(new_batches)} new batch(es) just released!_\n\n"
        )
        for b in new_batches:
            base_alert += format_batch(b)

        for chat_id in subscribers:
            try:
                msg        = base_alert
                saved_city = user_locations.get(chat_id, "")
                if saved_city:
                    city_matches = [
                        b for b in new_batches
                        if saved_city in b["location"].lower()
                    ]
                    if city_matches:
                        msg += (
                            f"\n📌 *A new batch is in your saved city: "
                            f"{saved_city.title()}!*\n"
                            "Use /mylocation to see full details with seat counts."
                        )
                await app.bot.send_message(
                    chat_id=int(chat_id),
                    text=msg,
                    parse_mode="Markdown",
                    disable_web_page_preview=True,
                )
            except Exception as e:
                logger.warning(f"Could not send alert to {chat_id}: {e}")

    save_json(KNOWN_BATCHES_FILE, current_ids)
    logger.info(f"Known batches updated: {len(current_ids)} total")


# ─────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────

def main():
    app = ApplicationBuilder().token(BOT_TOKEN).build()

    # /location uses a ConversationHandler to ask for city if not provided
    location_conv = ConversationHandler(
        entry_points=[CommandHandler("location", cmd_location_start)],
        states={
            WAITING_FOR_CITY: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, cmd_location_received),
            ],
        },
        fallbacks=[CommandHandler("cancel", cmd_location_cancel)],
    )

    app.add_handler(CommandHandler("start",       cmd_start))
    app.add_handler(CommandHandler("help",        cmd_help))
    app.add_handler(CommandHandler("batches",     cmd_batches))
    app.add_handler(CommandHandler("open",        cmd_open))
    app.add_handler(CommandHandler("seats",       cmd_seats))
    app.add_handler(CommandHandler("setlocation", cmd_setlocation))
    app.add_handler(CommandHandler("mylocation",  cmd_mylocation))
    app.add_handler(CommandHandler("alert",       cmd_alert))
    app.add_handler(CommandHandler("unalert",     cmd_unalert))
    app.add_handler(CommandHandler("refresh",     cmd_refresh))
    app.add_handler(location_conv)
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

    logger.info("AICA Bot is running…")
    app.run_polling()

if __name__ == "__main__":
    main()
