"""Konfigurasi yang dibaca dari environment / file .env."""
import os
from dataclasses import dataclass

from dotenv import load_dotenv

load_dotenv()


@dataclass
class Config:
    bot_token: str
    db_path: str
    database_url: str
    default_tz: str
    nag_hour: int
    run_mode: str  # "webhook" | "polling"
    webhook_url: str
    webhook_listen: str
    webhook_port: int
    webhook_path: str
    webhook_secret: str


def load_config() -> Config:
    token = os.getenv("BOT_TOKEN", "").strip()
    if not token:
        raise SystemExit(
            "BOT_TOKEN belum diset. Salin .env.example menjadi .env lalu isi token dari @BotFather."
        )

    run_mode = os.getenv("RUN_MODE", "polling").strip().lower()
    if run_mode not in ("webhook", "polling"):
        run_mode = "polling"

    try:
        nag_hour = int(os.getenv("NAG_HOUR", "9"))
    except ValueError:
        nag_hour = 9
    nag_hour = max(0, min(23, nag_hour))

    # Render (dan PaaS lain) menyuntik env var PORT yang WAJIB dipakai.
    # Prioritaskan PORT bila ada, jatuh ke WEBHOOK_PORT, lalu default 8443.
    try:
        webhook_port = int(os.getenv("PORT") or os.getenv("WEBHOOK_PORT", "8443"))
    except ValueError:
        webhook_port = 8443

    # PostgreSQL bila DATABASE_URL diisi (data persisten); selain itu SQLite.
    database_url = os.getenv("DATABASE_URL", "").strip()
    # Beberapa penyedia memberi skema "postgres://"; psycopg/libpq menerima
    # keduanya, tapi normalkan ke "postgresql://" agar konsisten.
    if database_url.startswith("postgres://"):
        database_url = "postgresql://" + database_url[len("postgres://"):]

    return Config(
        bot_token=token,
        db_path=os.getenv("DB_PATH", "reminder.db").strip(),
        database_url=database_url,
        default_tz=os.getenv("DEFAULT_TZ", "Asia/Jakarta").strip(),
        nag_hour=nag_hour,
        run_mode=run_mode,
        webhook_url=os.getenv("WEBHOOK_URL", "").strip().rstrip("/"),
        webhook_listen=os.getenv("WEBHOOK_LISTEN", "0.0.0.0").strip(),
        webhook_port=webhook_port,
        webhook_path=os.getenv("WEBHOOK_PATH", "telegram").strip().strip("/"),
        webhook_secret=os.getenv("WEBHOOK_SECRET", "").strip(),
    )
