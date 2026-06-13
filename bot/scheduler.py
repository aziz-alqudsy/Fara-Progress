"""Penjadwalan reminder dengan APScheduler + job nag harian."""
import logging
from datetime import datetime, timedelta, timezone
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


def _cron_prev(reminder, start, now):
    """Occurrence terakhir <= now untuk freq monthly/yearly (pakai CronTrigger)."""
    trig = _build_trigger(reminder)
    fire = trig.get_next_fire_time(None, start)
    prev = None
    guard = 0
    while fire is not None and fire <= now and guard < 100_000:
        prev = fire
        fire = trig.get_next_fire_time(fire, fire + timedelta(seconds=1))
        guard += 1
    return prev


def _missed_fire_time(reminder, now, last_run_at):
    """Occurrence terjadwal terbaru yang <= now dan BELUM pernah dikirim.

    Mengembalikan datetime occurrence terlewat, atau None jika tidak ada yang
    perlu di-catch-up (jadwal masih di masa depan, atau occurrence terakhir
    sudah punya run)."""
    start = datetime.fromtimestamp(reminder["start_utc"], tz=timezone.utc)
    if start > now:
        return None

    freq = reminder["freq"]
    interval = max(1, int(reminder["interval"]))

    if freq == "once":
        prev = start
    elif freq in ("hourly", "daily", "weekly"):
        unit = {"hourly": 3600, "daily": 86400, "weekly": 604800}[freq]
        step = unit * interval
        n = int((now - start).total_seconds() // step)
        prev = start + timedelta(seconds=step * n)
    else:  # monthly / yearly
        prev = _cron_prev(reminder, start, now)

    if prev is None or prev > now:
        return None

    # Sudah ada run pada/ setelah occurrence ini -> tidak terlewat.
    if last_run_at is not None and last_run_at >= prev.timestamp() - 1:
        return None
    return prev


async def run_catchup(application) -> None:
    """Saat startup/wake: fire reminder yang occurrence terjadwalnya terlewat
    selagi bot mati. Hanya occurrence TERAKHIR yang dikirim (tidak menumpuk),
    sehingga reminder tetap masuk walau telat alih-alih hilang sampai siklus
    berikutnya."""
    now = datetime.now(timezone.utc)
    fired = 0
    for reminder in db.list_active_reminders():
        try:
            last_run_at = db.get_last_run_at(reminder["id"])
            missed = _missed_fire_time(reminder, now, last_run_at)
            if missed is None:
                continue
            log.info(
                "Catch-up reminder %s: occurrence %s terlewat, fire sekarang.",
                reminder["id"], missed.isoformat(),
            )
            await fire_reminder(application, reminder["id"])
            fired += 1
        except Exception:
            log.exception("Gagal catch-up reminder %s", reminder["id"])
    if fired:
        log.info("Catch-up: %s reminder terlewat di-fire.", fired)


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


def _run_age_days(run_at_epoch, tz_name) -> int:
    """Selisih hari kalender (zona reminder) antara tanggal run dibuat dan hari ini.

    Run dibuat di hari H -> 0; keesokan harinya -> 1 (H+1); lusa -> 2 (H+2)."""
    tz = _tz(tz_name)
    run_date = datetime.fromtimestamp(run_at_epoch, tz=tz).date()
    today = datetime.now(tz).date()
    return (today - run_date).days


async def _send_nag(application, run, reminder, pending) -> None:
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


async def _send_final_summary(application, run, reminder, assignees) -> None:
    """Ringkasan final di H+2 — dikirim apa pun kondisinya, lalu run ditutup."""
    rows = [
        (texts.assignee_mention(a), db.get_latest_progress(run["id"], a))
        for a in assignees
    ]
    all_done = bool(assignees) and all(
        db.has_replied(run["id"], a) for a in assignees
    )
    text = texts.build_summary_text(reminder, rows, all_done=all_done)
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
        db.add_run_message(run["id"], msg.message_id, "summary")
    except Exception:
        log.exception("Gagal mengirim ringkasan final untuk run %s", run["id"])
    finally:
        db.set_run_status(run["id"], "summarized")


async def run_daily_followups(application) -> None:
    """Tindak lanjut harian per run terbuka, sesuai umur run (zona reminder):

      H+1 -> pengingat harian ke pengguna yang BELUM update (hanya jika ada).
      H+2 -> ringkasan final dikirim apa pun kondisinya, lalu run ditutup.

    Jika semua peserta sudah update lebih awal, run sudah ditutup oleh handler
    reply (replies.py) sehingga tidak ikut diproses di sini lagi.
    """
    for run in db.get_open_runs():
        reminder = db.get_reminder(run["reminder_id"])
        if not reminder:
            continue

        age = _run_age_days(run["run_at"], reminder["tz"])
        assignees = db.get_assignees(run["reminder_id"])

        # H+2 atau lebih (mis. bot sempat mati saat H+2) -> tutup dengan ringkasan.
        if age >= 2:
            await _send_final_summary(application, run, reminder, assignees)
            continue

        # H+1 -> pengingat harian hanya untuk yang belum update.
        if age == 1:
            pending = [a for a in assignees if not db.has_replied(run["id"], a)]
            if pending:
                await _send_nag(application, run, reminder, pending)
