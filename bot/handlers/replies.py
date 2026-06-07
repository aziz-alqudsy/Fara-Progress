"""Menangani update progress: pengguna reply/quote pesan bot."""
import logging

from telegram import Update
from telegram.ext import ContextTypes

from .. import db, texts

log = logging.getLogger(__name__)


async def handle_reply(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    replied = msg.reply_to_message

    # hanya proses reply terhadap pesan bot sendiri
    if not replied or not replied.from_user or replied.from_user.id != context.bot.id:
        return

    found = db.get_run_by_message(update.effective_chat.id, replied.message_id)
    if not found:
        return  # bukan pesan reminder/summary yang dilacak -> abaikan
    run, kind = found

    # Req: setelah summary dikirim (run summarized) atau reply ke pesan summary,
    # jangan direspons sama sekali.
    if kind == "summary" or run["status"] != "open":
        return

    user = update.effective_user
    progress_text = msg.text or msg.caption or ""
    db.add_progress(run["id"], user.id, user.username, user.full_name, progress_text)

    assignees = db.get_assignees(run["reminder_id"])
    done = [a for a in assignees if db.has_replied(run["id"], a)]
    total = len(assignees)

    await msg.reply_text(
        f"✅ Progress dari {user.full_name} tercatat ({len(done)}/{total})."
    )

    # Semua assignee sudah update -> kirim ringkasan & tutup run.
    if total > 0 and len(done) >= total:
        reminder = db.get_reminder(run["reminder_id"])
        rows = [
            (texts.assignee_mention(a), db.get_latest_progress(run["id"], a))
            for a in assignees
        ]
        summary = texts.build_summary_text(reminder, rows)
        try:
            smsg = await context.bot.send_message(
                chat_id=reminder["chat_id"],
                message_thread_id=reminder["thread_id"] or None,
                text=summary,
                parse_mode="HTML",
            )
            db.add_run_message(run["id"], smsg.message_id, "summary")
        except Exception:
            log.exception("Gagal mengirim summary untuk run %s", run["id"])
        finally:
            db.set_run_status(run["id"], "summarized")
