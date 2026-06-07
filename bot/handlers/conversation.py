"""Alur pembuatan reminder via /new (ConversationHandler)."""
import logging
from datetime import datetime
from zoneinfo import ZoneInfo

from telegram import ForceReply, InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    ConversationHandler,
    MessageHandler,
    filters,
)

from .. import db, parser, scheduler
from ..config import Config

log = logging.getLogger(__name__)

# State
TASKS, WHEN, FREQ, INTERVAL = range(4)

_DATE_FORMATS = ["%Y-%m-%d %H:%M", "%Y-%m-%d %H:%M:%S", "%d-%m-%Y %H:%M", "%d/%m/%Y %H:%M"]

_FREQ_BUTTONS = [
    [InlineKeyboardButton("Sekali", callback_data="freq:once"),
     InlineKeyboardButton("Tiap jam", callback_data="freq:hourly")],
    [InlineKeyboardButton("Harian", callback_data="freq:daily"),
     InlineKeyboardButton("Mingguan", callback_data="freq:weekly")],
    [InlineKeyboardButton("Bulanan", callback_data="freq:monthly"),
     InlineKeyboardButton("Tahunan", callback_data="freq:yearly")],
]


def _cfg(context) -> Config:
    return context.application.bot_data["config"]


async def cmd_new(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    context.user_data["draft"] = {
        "chat_id": update.effective_chat.id,
        "thread_id": msg.message_thread_id,
        "creator_id": update.effective_user.id,
        "creator_name": update.effective_user.full_name,
    }
    await msg.reply_text(
        "📝 <b>Buat reminder baru</b>\n\n"
        "Kirim daftar task dengan <b>numbering</b>. Di grup, kamu bisa mention "
        "(@) orang untuk menugaskan.\n\n"
        "Contoh:\n"
        "1. Siapkan slide @budi\n"
        "   a. cover\n"
        "   b. ringkasan\n"
        "2. Kirim undangan @sari\n\n"
        "<i>Setiap baris wajib pakai nomor (1. 2.) atau sub-abjad (a. b.).</i>",
        parse_mode="HTML",
        reply_markup=ForceReply(selective=True),
    )
    return TASKS


async def got_tasks(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    text = msg.text or ""
    try:
        parser.validate_task_text(text)
    except parser.TaskFormatError as e:
        await msg.reply_text(
            f"⚠️ {e}\n\nSilakan kirim ulang daftar task-nya.",
            reply_markup=ForceReply(selective=True),
        )
        return TASKS

    draft = context.user_data["draft"]
    draft["body"] = text.strip()
    draft["assignees"] = parser.extract_assignees(msg)
    # judul = teks item pertama (dipotong)
    first = next((l.strip() for l in text.split("\n") if l.strip()), "Reminder")
    draft["title"] = (first[:60] + "…") if len(first) > 60 else first

    tz = _cfg(context).default_tz
    now = datetime.now(ZoneInfo(tz)).strftime("%Y-%m-%d %H:%M")
    await msg.reply_text(
        "🗓️ <b>Kapan mulai?</b>\n"
        f"Kirim tanggal & jam (zona {tz}).\n"
        "Format: <code>YYYY-MM-DD HH:MM</code>\n"
        f"Contoh: <code>{now}</code>",
        parse_mode="HTML",
        reply_markup=ForceReply(selective=True),
    )
    return WHEN


async def got_when(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    raw = (msg.text or "").strip()
    tz = ZoneInfo(_cfg(context).default_tz)

    dt = None
    for fmt in _DATE_FORMATS:
        try:
            dt = datetime.strptime(raw, fmt)
            break
        except ValueError:
            continue
    if dt is None:
        await msg.reply_text(
            "⚠️ Format tanggal tidak dikenali. Contoh: <code>2026-06-10 09:30</code>",
            parse_mode="HTML",
            reply_markup=ForceReply(selective=True),
        )
        return WHEN

    dt = dt.replace(tzinfo=tz)
    context.user_data["draft"]["start_utc"] = dt.timestamp()
    context.user_data["draft"]["tz"] = str(tz)

    await msg.reply_text(
        "🔁 <b>Seberapa sering reminder diulang?</b>",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(_FREQ_BUTTONS),
    )
    return FREQ


async def got_freq(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    freq = query.data.split(":", 1)[1]
    context.user_data["draft"]["freq"] = freq

    if freq == "once":
        context.user_data["draft"]["interval"] = 1
        await query.edit_message_text("🔁 Frekuensi: sekali.")
        return await _finalize(update, context)

    unit = {"hourly": "jam", "daily": "hari", "weekly": "minggu",
            "monthly": "bulan", "yearly": "tahun"}[freq]
    await query.edit_message_text(f"🔁 Frekuensi: {freq}.")
    await query.message.reply_text(
        f"🔢 Setiap berapa {unit} sekali? Kirim angka (mis. <code>1</code>) "
        "atau /skip untuk setiap 1.",
        parse_mode="HTML",
        reply_markup=ForceReply(selective=True),
    )
    return INTERVAL


async def got_interval(update: Update, context: ContextTypes.DEFAULT_TYPE):
    raw = (update.effective_message.text or "").strip()
    try:
        interval = max(1, int(raw))
    except ValueError:
        interval = 1
    context.user_data["draft"]["interval"] = interval
    return await _finalize(update, context)


async def skip_interval(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["draft"]["interval"] = 1
    return await _finalize(update, context)


async def _finalize(update: Update, context: ContextTypes.DEFAULT_TYPE):
    draft = context.user_data.get("draft", {})
    chat = update.effective_chat

    reminder_id = db.create_reminder(
        chat_id=draft["chat_id"],
        thread_id=draft["thread_id"],
        creator_id=draft["creator_id"],
        creator_name=draft["creator_name"],
        title=draft["title"],
        body=draft["body"],
        freq=draft["freq"],
        interval=draft["interval"],
        start_utc=draft["start_utc"],
        tz=draft["tz"],
    )

    # assignees; jika tidak ada mention -> pembuat dianggap assignee
    assignees = draft.get("assignees") or []
    if not assignees:
        assignees = [{
            "user_id": draft["creator_id"],
            "username": None,
            "display_name": draft["creator_name"],
        }]
    for a in assignees:
        db.add_assignee(reminder_id, a["user_id"], a["username"], a["display_name"])

    # jadwalkan
    sched = context.application.bot_data["scheduler"]
    reminder = db.get_reminder(reminder_id)
    scheduler.schedule_reminder(sched, context.application, reminder)

    start_local = datetime.fromtimestamp(draft["start_utc"], ZoneInfo(draft["tz"]))
    job = sched.get_job(scheduler.job_id(reminder_id))
    next_run = job.next_run_time.strftime("%Y-%m-%d %H:%M %Z") if job and job.next_run_time else "—"

    target = context.bot if update.callback_query else update.effective_message
    send = (context.bot.send_message if update.callback_query else update.effective_message.reply_text)
    kwargs = {"chat_id": chat.id, "message_thread_id": draft["thread_id"]} if update.callback_query else {}

    await send(
        text=(
            f"✅ <b>Reminder #{reminder_id} dibuat!</b>\n"
            f"🗓️ Mulai: {start_local:%Y-%m-%d %H:%M}\n"
            f"🔁 Frekuensi: {draft['freq']} (interval {draft['interval']})\n"
            f"⏭️ Jadwal berikutnya: {next_run}\n\n"
            "Gunakan /list untuk melihat reminder aktif, /delete &lt;id&gt; untuk menghapus."
        ),
        parse_mode="HTML",
        **kwargs,
    )
    context.user_data.pop("draft", None)
    return ConversationHandler.END


async def cmd_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.pop("draft", None)
    await update.effective_message.reply_text("❌ Pembuatan reminder dibatalkan.")
    return ConversationHandler.END


def build_conversation() -> ConversationHandler:
    return ConversationHandler(
        entry_points=[CommandHandler("new", cmd_new)],
        states={
            TASKS: [MessageHandler(filters.TEXT & ~filters.COMMAND, got_tasks)],
            WHEN: [MessageHandler(filters.TEXT & ~filters.COMMAND, got_when)],
            FREQ: [CallbackQueryHandler(got_freq, pattern=r"^freq:")],
            INTERVAL: [
                CommandHandler("skip", skip_interval),
                MessageHandler(filters.TEXT & ~filters.COMMAND, got_interval),
            ],
        },
        fallbacks=[CommandHandler("cancel", cmd_cancel)],
        per_chat=True,
        per_user=True,
        allow_reentry=True,
    )
