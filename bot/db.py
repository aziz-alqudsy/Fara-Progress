"""Lapisan penyimpanan SQLite.

Skema:
  reminders     : definisi reminder + jadwal
  assignees     : pengguna yang di-assign / di-mention pada sebuah reminder
  runs          : satu "siklus" pengiriman reminder (open -> summarized)
  run_messages  : pesan-pesan bot milik sebuah run (reminder / nag / summary)
  progress      : update progress yang dikirim pengguna via reply
"""
import sqlite3
import threading
import time
from typing import Optional

_conn: Optional[sqlite3.Connection] = None
_lock = threading.Lock()

SCHEMA = """
CREATE TABLE IF NOT EXISTS reminders (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    chat_id      INTEGER NOT NULL,
    thread_id    INTEGER,
    creator_id   INTEGER NOT NULL,
    creator_name TEXT,
    title        TEXT,
    body         TEXT NOT NULL,
    freq         TEXT NOT NULL,         -- once|hourly|daily|weekly|monthly|yearly
    interval     INTEGER NOT NULL DEFAULT 1,
    start_utc    REAL NOT NULL,         -- epoch detik (UTC)
    tz           TEXT NOT NULL,
    active       INTEGER NOT NULL DEFAULT 1,
    created_at   REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS assignees (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    reminder_id  INTEGER NOT NULL REFERENCES reminders(id) ON DELETE CASCADE,
    user_id      INTEGER,
    username     TEXT,
    display_name TEXT
);

CREATE TABLE IF NOT EXISTS runs (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    reminder_id INTEGER NOT NULL REFERENCES reminders(id) ON DELETE CASCADE,
    status      TEXT NOT NULL DEFAULT 'open',   -- open|summarized
    run_at      REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS run_messages (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id     INTEGER NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
    message_id INTEGER NOT NULL,
    kind       TEXT NOT NULL                    -- reminder|nag|summary
);

CREATE TABLE IF NOT EXISTS progress (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id       INTEGER NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
    user_id      INTEGER,
    username     TEXT,
    display_name TEXT,
    text         TEXT,
    created_at   REAL NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_runmsg_msg ON run_messages(message_id);
CREATE INDEX IF NOT EXISTS idx_runs_status ON runs(status);
"""


def init_db(path: str) -> None:
    global _conn
    _conn = sqlite3.connect(path, check_same_thread=False)
    _conn.row_factory = sqlite3.Row
    _conn.execute("PRAGMA journal_mode=WAL;")
    _conn.execute("PRAGMA foreign_keys=ON;")
    _conn.executescript(SCHEMA)
    _conn.commit()


def _now() -> float:
    return time.time()


# --------------------------------------------------------------------------- #
# Reminders
# --------------------------------------------------------------------------- #
def create_reminder(chat_id, thread_id, creator_id, creator_name, title, body,
                    freq, interval, start_utc, tz) -> int:
    with _lock:
        cur = _conn.execute(
            """INSERT INTO reminders
               (chat_id, thread_id, creator_id, creator_name, title, body,
                freq, interval, start_utc, tz, active, created_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,1,?)""",
            (chat_id, thread_id, creator_id, creator_name, title, body,
             freq, interval, start_utc, tz, _now()),
        )
        _conn.commit()
        return cur.lastrowid


def add_assignee(reminder_id, user_id, username, display_name) -> None:
    with _lock:
        _conn.execute(
            "INSERT INTO assignees (reminder_id, user_id, username, display_name) VALUES (?,?,?,?)",
            (reminder_id, user_id, username, display_name),
        )
        _conn.commit()


def get_reminder(reminder_id):
    cur = _conn.execute("SELECT * FROM reminders WHERE id=?", (reminder_id,))
    return cur.fetchone()


def get_assignees(reminder_id):
    cur = _conn.execute("SELECT * FROM assignees WHERE reminder_id=?", (reminder_id,))
    return cur.fetchall()


def list_active_reminders():
    cur = _conn.execute("SELECT * FROM reminders WHERE active=1")
    return cur.fetchall()


def list_reminders_in_chat(chat_id, thread_id):
    if thread_id is None:
        cur = _conn.execute(
            "SELECT * FROM reminders WHERE chat_id=? AND thread_id IS NULL AND active=1 ORDER BY id",
            (chat_id,),
        )
    else:
        cur = _conn.execute(
            "SELECT * FROM reminders WHERE chat_id=? AND thread_id=? AND active=1 ORDER BY id",
            (chat_id, thread_id),
        )
    return cur.fetchall()


def deactivate_reminder(reminder_id) -> bool:
    with _lock:
        cur = _conn.execute("UPDATE reminders SET active=0 WHERE id=?", (reminder_id,))
        _conn.commit()
        return cur.rowcount > 0


# --------------------------------------------------------------------------- #
# Runs & messages
# --------------------------------------------------------------------------- #
def create_run(reminder_id, message_id) -> int:
    with _lock:
        cur = _conn.execute(
            "INSERT INTO runs (reminder_id, status, run_at) VALUES (?, 'open', ?)",
            (reminder_id, _now()),
        )
        run_id = cur.lastrowid
        _conn.execute(
            "INSERT INTO run_messages (run_id, message_id, kind) VALUES (?,?, 'reminder')",
            (run_id, message_id),
        )
        _conn.commit()
        return run_id


def add_run_message(run_id, message_id, kind) -> None:
    with _lock:
        _conn.execute(
            "INSERT INTO run_messages (run_id, message_id, kind) VALUES (?,?,?)",
            (run_id, message_id, kind),
        )
        _conn.commit()


def get_run_by_message(chat_id, message_id):
    """Cari run berdasarkan pesan bot yang di-reply. Mengembalikan (run_row, kind) atau None."""
    cur = _conn.execute(
        """SELECT r.*, m.kind AS msg_kind
           FROM run_messages m
           JOIN runs r       ON r.id = m.run_id
           JOIN reminders rem ON rem.id = r.reminder_id
           WHERE m.message_id=? AND rem.chat_id=?
           ORDER BY m.id DESC LIMIT 1""",
        (message_id, chat_id),
    )
    row = cur.fetchone()
    if not row:
        return None
    return row, row["msg_kind"]


def get_open_runs():
    cur = _conn.execute("SELECT * FROM runs WHERE status='open'")
    return cur.fetchall()


def get_run_reminder_message(run_id):
    cur = _conn.execute(
        "SELECT message_id FROM run_messages WHERE run_id=? AND kind='reminder' ORDER BY id LIMIT 1",
        (run_id,),
    )
    row = cur.fetchone()
    return row["message_id"] if row else None


def set_run_status(run_id, status) -> None:
    with _lock:
        _conn.execute("UPDATE runs SET status=? WHERE id=?", (status, run_id))
        _conn.commit()


# --------------------------------------------------------------------------- #
# Progress
# --------------------------------------------------------------------------- #
def add_progress(run_id, user_id, username, display_name, text) -> None:
    with _lock:
        _conn.execute(
            """INSERT INTO progress (run_id, user_id, username, display_name, text, created_at)
               VALUES (?,?,?,?,?,?)""",
            (run_id, user_id, username, display_name, text, _now()),
        )
        _conn.commit()


def _assignee_clause(assignee):
    """Bangun klausa WHERE untuk mencocokkan progress dengan assignee."""
    if assignee["user_id"]:
        return "user_id=?", (assignee["user_id"],)
    if assignee["username"]:
        return "LOWER(username)=LOWER(?)", (assignee["username"],)
    return "0", ()  # tidak bisa dicocokkan


def has_replied(run_id, assignee) -> bool:
    clause, params = _assignee_clause(assignee)
    cur = _conn.execute(
        f"SELECT 1 FROM progress WHERE run_id=? AND {clause} LIMIT 1",
        (run_id, *params),
    )
    return cur.fetchone() is not None


def get_latest_progress(run_id, assignee):
    clause, params = _assignee_clause(assignee)
    cur = _conn.execute(
        f"""SELECT text FROM progress
            WHERE run_id=? AND {clause}
            ORDER BY id DESC LIMIT 1""",
        (run_id, *params),
    )
    row = cur.fetchone()
    return row["text"] if row else None
