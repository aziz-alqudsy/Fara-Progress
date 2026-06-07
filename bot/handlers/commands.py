"""Perintah umum: /start, /help, /list, /delete."""
from datetime import datetime
from html import escape
from zoneinfo import ZoneInfo

from telegram import Update
from telegram.ext import ContextTypes

from .. import db, scheduler
from ..texts import FREQ_LABEL

HELP = (
    "🤖 <b>Bot Reminder</b>\n\n"
    "Bisa dipakai secara <b>pribadi</b> maupun di dalam <b>grup/topic</b>.\n\n"
    "<b>Perintah:</b>\n"
    "• /new — buat reminder baru\n"
    "• /list — lihat reminder aktif di chat ini\n"
    "• /delete &lt;id&gt; — hapus/nonaktifkan reminder\n"
    "• /cancel — batalkan pembuatan reminder\n"
    "• /help — bantuan\n\n"
    "<b>Cara kerja:</b>\n"
    "1. Tulis task dengan numbering (1. 2. 3.) dan sub-abjad (a. b. c.).\n"
    "2. Di grup, mention (@) orang untuk menugaskan task.\n"
    "3. Atur tanggal/jam mulai dan frekuensi (sekali/jam/harian/mingguan/bulanan/tahunan).\n"
    "4. Saat reminder datang, <b>reply/quote</b> pesan bot untuk update progress.\n"
    "5. Jika semua yang ditugaskan sudah update, bot mengirim <b>ringkasan</b>.\n"
    "6. Yang belum update akan terus diingatkan setiap hari."
)


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.effective_message.reply_text(HELP, parse_mode="HTML")


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.effective_message.reply_text(HELP, parse_mode="HTML")


async def cmd_list(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    rows = db.list_reminders_in_chat(update.effective_chat.id, msg.message_thread_id)
    if not rows:
        await msg.reply_text("Belum ada reminder aktif di chat ini. Buat dengan /new.")
        return

    lines = ["📋 <b>Reminder aktif:</b>", ""]
    for r in rows:
        tz = ZoneInfo(r["tz"])
        start = datetime.fromtimestamp(r["start_utc"], tz)
        freq = FREQ_LABEL.get(r["freq"], r["freq"])
        if r["interval"] > 1:
            freq += f" (tiap {r['interval']})"
        lines.append(
            f"<b>#{r['id']}</b> — {escape(r['title'] or 'Reminder')}\n"
            f"   🗓️ {start:%Y-%m-%d %H:%M} · 🔁 {freq}"
        )
    lines.append("\nHapus dengan /delete &lt;id&gt;.")
    await msg.reply_text("\n".join(lines), parse_mode="HTML")


async def cmd_delete(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    if not context.args:
        await msg.reply_text("Pakai: /delete <id>. Lihat id dengan /list.")
        return
    try:
        reminder_id = int(context.args[0])
    except ValueError:
        await msg.reply_text("ID harus berupa angka. Contoh: /delete 3")
        return

    reminder = db.get_reminder(reminder_id)
    if not reminder or reminder["chat_id"] != update.effective_chat.id:
        await msg.reply_text(f"Reminder #{reminder_id} tidak ditemukan di chat ini.")
        return

    db.deactivate_reminder(reminder_id)
    scheduler.remove_reminder(context.application.bot_data["scheduler"], reminder_id)
    await msg.reply_text(f"🗑️ Reminder #{reminder_id} dihapus.")
