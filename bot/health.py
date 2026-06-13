"""Menambahkan endpoint GET /health (HTTP 200) ke server webhook PTB.

python-telegram-bot hanya mendaftarkan route untuk path webhook Telegram,
sehingga ping health-check eksternal (mis. UptimeRobot/cron-job.org) ke
`/health` mendapat HTTP 404 dan dianggap DOWN. Modul ini menambal
``WebhookAppClass`` agar `/health` membalas 200 "ok".
"""
import logging

import tornado.web
from telegram.ext._utils import webhookhandler

log = logging.getLogger(__name__)


class _HealthHandler(tornado.web.RequestHandler):
    """Balas 200 untuk GET/HEAD. Dipakai sebagai target keep-alive/monitoring."""

    def get(self) -> None:
        self.set_header("Content-Type", "text/plain; charset=utf-8")
        self.write("ok")


_installed = False


def install_health_endpoint(path: str = "/health") -> None:
    """Pasang route health di server webhook PTB. Idempoten (aman dipanggil 1x)."""
    global _installed
    if _installed:
        return

    orig_init = webhookhandler.WebhookAppClass.__init__

    def patched_init(self, *args, **kwargs):
        orig_init(self, *args, **kwargs)
        # Tambahkan route /health di samping route webhook Telegram bawaan PTB.
        self.add_handlers(r".*$", [(rf"{path}/?", _HealthHandler)])

    webhookhandler.WebhookAppClass.__init__ = patched_init
    _installed = True
    log.info("Health endpoint terpasang di %s", path)
