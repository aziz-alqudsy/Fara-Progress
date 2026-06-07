"""Penjadwalan reminder dengan APScheduler + job nag harian."""
import logging
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.date import DateTrigger
from apscheduler.triggers.interval import IntervalTrigger

from . import db, texts

log = logging.getLogger(__name__)

_MISFIRE_GRACE = 3600  # toleransi 1 jam jika job terlewat (mis. bot sempat mati)


def create_scheduler(tz_name: str) -> AsyncIOScheduler:
    try:
        tz = ZoneInfo(tz_name)
    except Exception:
        tz = ZoneInfo("UTC")
    return AsyncIOScheduler(timezone=tz)


def _tz(name):
    try:
        return ZoneInfo(name)
    except Exception:
        return timezone.utc


def _build_trigger(reminder):
    start = datetime.fromtimestamp(reminder["start_utc"], tz=timezone.utc)
    freq = reminder["freq"]
    interval = max(1, int(reminder["interval"]))

    if freq == "once":
        return DateTrigger(run_date=start)
    if freq == "hourly":
        return IntervalTrigger(hours=interval, start_date=start)
    if freq == "daily":
        return IntervalTrigger(days=interval, start_date=start)
    if freq == "weekly":
        return IntervalTrigger(weeks=interval, start_date=start)

    # bulanan / tahunan -> pakai cron pada tanggal & jam lokal awal
    tz = _tz(reminder["tz"])
    local = start.astimezone(tz)
    if freq == "monthly":
        month = "*" if interval == 1 else f"*/{interval}"
        return CronTrigger(month=month, day=local.day, hour=local.hour,
                           minute=local.minute, timezone=tz)
    if freq == "yearly":
        return CronTrigger(month=local.month, day=local.day, hour=local.hour,
                           minute=local.minute, timezone=tz)
    # fallback
    return DateTrigger(run_date=start)


def job_id(reminder_id) -> str:
    return f"rem-{reminder_id}"


def schedule_reminder(scheduler: AsyncIOScheduler, application, reminder) -> None:
    """Daftarkan/segarkan job untuk satu reminder."""
    # Lewati reminder "once" yang waktunya sudah lewat.
    if reminder["freq"] == "once":
        start = datetime.fromtimestamp(reminder["start_utc"], tz=timezone.utc)
        if start < datetime.now(timezone.utc):
            return

    scheduler.add_job(
        fire_reminder,
        trigger=_build_trigger(reminder),
        args=[application, reminder["id"]],
        id=job_id(reminder["id"]),
        replace_existing=True,
        misfire_grace_time=_MISFIRE_GRACE,
        coalesce=True,
    )


def remove_reminder(scheduler: AsyncIOScheduler, reminder_id) -> None:
    try:
        scheduler.remove_job(job_id(reminder_id))
    except Exception:
        pass


def rebuild_jobs(scheduler: AsyncIOScheduler, application) -> None:
    """Bangun ulang seluruh job dari DB saat startup."""
    count = 0
    for r in db.list_active_reminders():
        try:
            schedule_reminder(scheduler, application, r)
            count += 1
        except Exception:
            log.exception("Gagal menjadwalkan reminder %s", r["id"])
    log.info("Menjadwalkan ulang %s reminder.", count)


async def fire_reminder(application, reminder_id) -> None:
    """Kirim pesan reminder dan buat run baru."""
    reminder = db.get_reminder(reminder_id)
    if not reminder or not reminder["active"]:
        return

    assignees = db.get_assignees(reminder_id)
    text = texts.build_reminder_text(reminder, assignees)
    try:
        msg = await application.bot.send_message(
            chat_id=reminder["chat_id"],
            message_thread_id=reminder["thread_id"] or None,
            text=text,
            parse_mode="HTML",
        )
    except Exception:
        log.exception("Gagal mengirim reminder %s", reminder_id)
        return

    db.create_run(reminder_id, msg.message_id)

    if reminder["freq"] == "once":
        db.deactivate_reminder(reminder_id)


async def run_nags(application) -> None:
    """Pengingat harian: mention pengguna yang belum update pada run yang masih open."""
    for run in db.get_open_runs():
        reminder = db.get_reminder(run["reminder_id"])
        if not reminder:
            continue
        assignees = db.get_assignees(run["reminder_id"])
        pending = [a for a in assignees if not db.has_replied(run["id"], a)]
        if not pending:
            continue

        text = texts.build_nag_text(reminder, pending)
        reply_to = db.get_run_reminder_message(run["id"])
        try:
            msg = await application.bot.send_message(
                chat_id=reminder["chat_id"],
                message_thread_id=reminder["thread_id"] or None,
                text=text,
                parse_mode="HTML",
                reply_to_message_id=reply_to,
                allow_sending_without_reply=True,
            )
            db.add_run_message(run["id"], msg.message_id, "nag")
        except Exception:
            log.exception("Gagal mengirim nag untuk run %s", run["id"])
