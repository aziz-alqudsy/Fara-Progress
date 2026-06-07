"""Alur pembuatan reminder via /new (ConversationHandler)."""
import logging
from datetime import datetime
from zoneinfo import ZoneInfo

from telegram import ForceReply, InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ChatType
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


def _remember(context, *message_ids) -> None:
    """Catat message_id pesan setup agar bisa dihapus saat conversation selesai."""
    draft = context.user_data.get("draft")
    if draft is not None:
        draft.setdefault("trash", []).extend(m for m in message_ids if m)


async def _purge(context, draft) -> None:
    """Hapus seluruh pesan setup (prompt bot + jawaban user) di grup.

    Hanya berjalan di grup/supergroup. Pesan bot selalu bisa dihapus; pesan
    pengguna lain hanya bisa dihapus bila bot adalah admin dengan izin hapus
    pesan — kegagalan diabaikan diam-diam.
    """
    if not draft or not draft.get("cleanup"):
        return
    chat_id = draft["chat_id"]
    for mid in set(draft.get("trash", [])):
        try:
            await context.bot.delete_message(chat_id=chat_id, message_id=mid)
        except Exception:
            pass


async def cmd_new(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    in_group = update.effective_chat.type in (ChatType.GROUP, ChatType.SUPERGROUP)
    context.user_data["draft"] = {
        "chat_id": update.effective_chat.id,
        "thread_id": msg.message_thread_id,
        "creator_id": update.effective_user.id,
        "creator_name": update.effective_user.full_name,
        "cleanup": in_group,   # auto-hapus pesan setup hanya di grup
        "trash": [],
    }
    _remember(context, msg.message_id)  # perintah /new
    sent = await msg.reply_text(
        "📝 <b>Buat reminder baru</b>\n\n"
        "Kirim daftar task dengan <b>numbering</b>. Di grup, kamu bisa mention "
        "(@) orang untuk menugaskan.\n\n"
        "Contoh:\n"
        "1. Siapkan slide @budi\n"
        "   a. cover\n"
        "   b. ringkasan\n"
        "2. Kirim undangan @sari\n\n"
        "<i>Setiap baris wajib pakai nomor (1. 2.) atau sub-abjad (a. b.).</i>\n"
        + ("\n<i>🧹 Pesan setup ini akan dihapus otomatis setelah selesai.</i>"
           if in_group else ""),
        parse_mode="HTML",
        reply_markup=ForceReply(selective=True),
    )
    _remember(context, sent.message_id)
    return TASKS


async def got_tasks(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    _remember(context, msg.message_id)
    text = msg.text or ""
    try:
        parser.validate_task_text(text)
    except parser.TaskFormatError as e:
        err = await msg.reply_text(
            f"⚠️ {e}\n\nSilakan kirim ulang daftar task-nya.",
            reply_markup=ForceReply(selective=True),
        )
        _remember(context, err.message_id)
        return TASKS

    draft = context.user_data["draft"]
    draft["body"] = text.strip()
    draft["assignees"] = parser.extract_assignees(msg)
    # judul dibuat otomatis di _finalize (butuh tanggal mulai).

    tz = _cfg(context).default_tz
    now = datetime.now(ZoneInfo(tz)).strftime("%Y-%m-%d %H:%M")
    sent = await msg.reply_text(
        "🗓️ <b>Kapan mulai?</b>\n"
        f"Kirim tanggal & jam (zona {tz}).\n"
        "Format: <code>YYYY-MM-DD HH:MM</code>\n"
        f"Contoh: <code>{now}</code>",
        parse_mode="HTML",
        reply_markup=ForceReply(selective=True),
    )
    _remember(context, sent.message_id)
    return WHEN


async def got_when(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    _remember(context, msg.message_id)
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
        err = await msg.reply_text(
            "⚠️ Format tanggal tidak dikenali. Contoh: <code>2026-06-10 09:30</code>",
            parse_mode="HTML",
            reply_markup=ForceReply(selective=True),
        )
        _remember(context, err.message_id)
        return WHEN

    dt = dt.replace(tzinfo=tz)
    context.user_data["draft"]["start_utc"] = dt.timestamp()
    context.user_data["draft"]["tz"] = str(tz)

    sent = await msg.reply_text(
        "🔁 <b>Seberapa sering reminder diulang?</b>",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(_FREQ_BUTTONS),
    )
    _remember(context, sent.message_id)
    return FREQ


async def got_freq(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    _remember(context, query.message.message_id)  # pesan inline pilihan frekuensi
    freq = query.data.split(":", 1)[1]
    context.user_data["draft"]["freq"] = freq

    if freq == "once":
        context.user_data["draft"]["interval"] = 1
        await query.edit_message_text("🔁 Frekuensi: sekali.")
        return await _finalize(update, context)

    unit = {"hourly": "jam", "daily": "hari", "weekly": "minggu",
            "monthly": "bulan", "yearly": "tahun"}[freq]
    await query.edit_message_text(f"🔁 Frekuensi: {freq}.")
    sent = await query.message.reply_text(
        f"🔢 Setiap berapa {unit} sekali? Kirim angka (mis. <code>1</code>) "
        "atau /skip untuk setiap 1.",
        parse_mode="HTML",
        reply_markup=ForceReply(selective=True),
    )
    _remember(context, sent.message_id)
    return INTERVAL


async def got_interval(update: Update, context: ContextTypes.DEFAULT_TYPE):
    _remember(context, update.effective_message.message_id)
    raw = (update.effective_message.text or "").strip()
    try:
        interval = max(1, int(raw))
    except ValueError:
        interval = 1
    context.user_data["draft"]["interval"] = interval
    return await _finalize(update, context)


async def skip_interval(update: Update, context: ContextTypes.DEFAULT_TYPE):
    _remember(context, update.effective_message.message_id)
    context.user_data["draft"]["interval"] = 1
    return await _finalize(update, context)


async def _finalize(update: Update, context: ContextTypes.DEFAULT_TYPE):
    draft = context.user_data.get("draft", {})

    # Judul generik otomatis: "Daftar Task (N item) — YYYY-MM-DD" (tanggal mulai).
    start_local = datetime.fromtimestamp(draft["start_utc"], ZoneInfo(draft["tz"]))
    n_items = parser.count_main_items(draft["body"])
    draft["title"] = f"Daftar Task ({n_items} item) — {start_local:%Y-%m-%d}"

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

    job = sched.get_job(scheduler.job_id(reminder_id))
    next_run = job.next_run_time.strftime("%Y-%m-%d %H:%M %Z") if job and job.next_run_time else "—"

    # Hapus seluruh pesan setup, lalu kirim satu konfirmasi sebagai pesan baru
    # (tidak me-reply pesan yang sudah dihapus).
    await _purge(context, draft)
    await context.bot.send_message(
        chat_id=draft["chat_id"],
        message_thread_id=draft["thread_id"] or None,
        text=(
            f"✅ <b>Reminder #{reminder_id} dibuat!</b>\n"
            f"🗓️ Mulai: {start_local:%Y-%m-%d %H:%M}\n"
            f"🔁 Frekuensi: {draft['freq']} (interval {draft['interval']})\n"
            f"⏭️ Jadwal berikutnya: {next_run}\n\n"
            "Gunakan /list untuk melihat reminder aktif, /delete &lt;id&gt; untuk menghapus."
        ),
        parse_mode="HTML",
    )
    context.user_data.pop("draft", None)
    return ConversationHandler.END


async def cmd_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    draft = context.user_data.get("draft")
    _remember(context, update.effective_message.message_id)  # perintah /cancel
    text = "❌ Pembuatan reminder dibatalkan."
    if draft and draft.get("cleanup"):
        await _purge(context, draft)
        await context.bot.send_message(
            chat_id=draft["chat_id"],
            message_thread_id=draft["thread_id"] or None,
            text=text,
        )
    else:
        await update.effective_message.reply_text(text)
    context.user_data.pop("draft", None)
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
