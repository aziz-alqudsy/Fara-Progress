"""Bootstrap aplikasi: registrasi handler, scheduler, dan mode jalan."""
import logging

from telegram import BotCommand
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from . import db, scheduler
from .config import load_config
from .handlers import commands, replies
from .handlers.conversation import build_conversation

logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    level=logging.INFO,
)
log = logging.getLogger("reminder-bot")


async def _post_init(application) -> None:
    cfg = application.bot_data["config"]

    # Scheduler dimulai di dalam event loop yang sama dengan bot.
    sched = scheduler.create_scheduler(cfg.default_tz)
    application.bot_data["scheduler"] = sched
    sched.start()
    scheduler.rebuild_jobs(sched, application)

    # Catch-up: kirim reminder yang occurrence-nya terlewat selagi bot mati
    # (mis. service tidur saat jam jadwal). Tanpa ini, occurrence yang terlewat
    # hilang karena trigger melompat ke jadwal berikutnya saat dijadwalkan ulang.
    await scheduler.run_catchup(application)

    # Job tindak lanjut harian: nag (H+1) & ringkasan final (H+2).
    # PENTING: beri timezone eksplisit. CronTrigger instance TIDAK mewarisi
    # timezone scheduler — tanpa ini ia memakai tz lokal server (UTC di Render),
    # sehingga jam 9 jadi 09:00 UTC (= 16:00 WIB), bukan 09:00 WIB.
    from apscheduler.triggers.cron import CronTrigger
    sched.add_job(
        scheduler.run_daily_followups,
        trigger=CronTrigger(hour=cfg.nag_hour, minute=0, timezone=sched.timezone),
        args=[application],
        id="daily-followups",
        replace_existing=True,
    )

    await application.bot.set_my_commands([
        BotCommand("new", "Buat reminder baru"),
        BotCommand("list", "Lihat reminder aktif"),
        BotCommand("delete", "Hapus reminder (pakai id)"),
        BotCommand("cancel", "Batalkan pembuatan reminder"),
        BotCommand("help", "Bantuan"),
    ])
    log.info("Bot siap.")


async def _post_shutdown(application) -> None:
    sched = application.bot_data.get("scheduler")
    if sched and sched.running:
        sched.shutdown(wait=False)


def main() -> None:
    cfg = load_config()
    db.init_db(cfg.db_path, cfg.database_url or None)
    log.info("Backend DB: %s", "PostgreSQL" if cfg.database_url else f"SQLite ({cfg.db_path})")

    application = (
        ApplicationBuilder()
        .token(cfg.bot_token)
        .post_init(_post_init)
        .post_shutdown(_post_shutdown)
        .build()
    )
    application.bot_data["config"] = cfg

    # Urutan penting: conversation lebih dulu agar jawaban ForceReply tidak
    # tertangkap sebagai update progress.
    application.add_handler(build_conversation())
    application.add_handler(CommandHandler("start", commands.cmd_start))
    application.add_handler(CommandHandler("help", commands.cmd_help))
    application.add_handler(CommandHandler("list", commands.cmd_list))
    application.add_handler(CommandHandler("delete", commands.cmd_delete))
    application.add_handler(
        MessageHandler(filters.REPLY & ~filters.COMMAND, replies.handle_reply)
    )

    if cfg.run_mode == "webhook":
        if not cfg.webhook_url:
            raise SystemExit("RUN_MODE=webhook tetapi WEBHOOK_URL kosong. Isi di .env.")
        # Endpoint /health untuk keep-alive / monitoring (UptimeRobot dsb.).
        from .health import install_health_endpoint
        install_health_endpoint("/health")
        log.info("Menjalankan mode webhook di %s/%s", cfg.webhook_url, cfg.webhook_path)
        application.run_webhook(
            listen=cfg.webhook_listen,
            port=cfg.webhook_port,
            url_path=cfg.webhook_path,
            secret_token=cfg.webhook_secret or None,
            webhook_url=f"{cfg.webhook_url}/{cfg.webhook_path}",
            allowed_updates=["message", "callback_query"],
        )
    else:
        log.info("Menjalankan mode polling.")
        application.run_polling(allowed_updates=["message", "callback_query"])


if __name__ == "__main__":
    main()
