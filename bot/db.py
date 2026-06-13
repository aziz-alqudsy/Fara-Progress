"""Lapisan penyimpanan: SQLite (lokal) atau PostgreSQL (produksi).

Backend dipilih otomatis: bila ``DATABASE_URL`` diisi -> PostgreSQL (mis. Neon/
Supabase, data persisten walau Render redeploy); selain itu -> SQLite di
``DB_PATH``. Signature fungsi publik sama untuk kedua backend.

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

_conn = None
_backend = "sqlite"          # "sqlite" | "postgres"
_database_url: Optional[str] = None
_lock = threading.Lock()

# Diisi saat init_db memilih backend postgres.
_psycopg = None
_dict_row = None
_RETRY_ERRORS: tuple = ()    # exception koneksi yang memicu reconnect+retry


_SCHEMA_SQLITE = """
CREATE TABLE IF NOT EXISTS reminders (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    chat_id      INTEGER NOT NULL,
    thread_id    INTEGER,
    creator_id   INTEGER NOT NULL,
    creator_name TEXT,
    title        TEXT,
    body         TEXT NOT NULL,
    freq         TEXT NOT NULL,
    interval     INTEGER NOT NULL DEFAULT 1,
    start_utc    REAL NOT NULL,
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
    status      TEXT NOT NULL DEFAULT 'open',
    run_at      REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS run_messages (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id     INTEGER NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
    message_id INTEGER NOT NULL,
    kind       TEXT NOT NULL
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

_SCHEMA_PG = """
CREATE TABLE IF NOT EXISTS reminders (
    id           BIGSERIAL PRIMARY KEY,
    chat_id      BIGINT NOT NULL,
    thread_id    BIGINT,
    creator_id   BIGINT NOT NULL,
    creator_name TEXT,
    title        TEXT,
    body         TEXT NOT NULL,
    freq         TEXT NOT NULL,
    interval     INTEGER NOT NULL DEFAULT 1,
    start_utc    DOUBLE PRECISION NOT NULL,
    tz           TEXT NOT NULL,
    active       INTEGER NOT NULL DEFAULT 1,
    created_at   DOUBLE PRECISION NOT NULL
);
CREATE TABLE IF NOT EXISTS assignees (
    id           BIGSERIAL PRIMARY KEY,
    reminder_id  BIGINT NOT NULL REFERENCES reminders(id) ON DELETE CASCADE,
    user_id      BIGINT,
    username     TEXT,
    display_name TEXT
);
CREATE TABLE IF NOT EXISTS runs (
    id          BIGSERIAL PRIMARY KEY,
    reminder_id BIGINT NOT NULL REFERENCES reminders(id) ON DELETE CASCADE,
    status      TEXT NOT NULL DEFAULT 'open',
    run_at      DOUBLE PRECISION NOT NULL
);
CREATE TABLE IF NOT EXISTS run_messages (
    id         BIGSERIAL PRIMARY KEY,
    run_id     BIGINT NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
    message_id BIGINT NOT NULL,
    kind       TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS progress (
    id           BIGSERIAL PRIMARY KEY,
    run_id       BIGINT NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
    user_id      BIGINT,
    username     TEXT,
    display_name TEXT,
    text         TEXT,
    created_at   DOUBLE PRECISION NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_runmsg_msg ON run_messages(message_id);
CREATE INDEX IF NOT EXISTS idx_runs_status ON runs(status);
"""


def init_db(path: str, database_url: Optional[str] = None) -> None:
    global _conn, _backend, _database_url

    if database_url:
        _backend = "postgres"
        _database_url = database_url
        _connect_postgres()
        schema = _SCHEMA_PG
    else:
        _backend = "sqlite"
        _conn = sqlite3.connect(path, check_same_thread=False)
        _conn.row_factory = sqlite3.Row
        _conn.execute("PRAGMA journal_mode=WAL;")
        _conn.execute("PRAGMA foreign_keys=ON;")
        schema = _SCHEMA_SQLITE

    # Jalankan tiap statement DDL terpisah (psycopg tidak menjalankan banyak
    # statement dalam satu execute; SQLite pun aman dengan cara ini).
    for stmt in schema.split(";"):
        s = stmt.strip()
        if s:
            _conn.cursor().execute(s)
    if _backend == "sqlite":
        _conn.commit()


def _connect_postgres() -> None:
    global _conn, _psycopg, _dict_row, _RETRY_ERRORS
    import psycopg
    from psycopg.rows import dict_row

    _psycopg = psycopg
    _dict_row = dict_row
    _RETRY_ERRORS = (psycopg.OperationalError, psycopg.InterfaceError)
    _conn = psycopg.connect(_database_url, row_factory=dict_row, autocommit=True)


def _reconnect() -> None:
    _connect_postgres()


def _now() -> float:
    return time.time()


# --------------------------------------------------------------------------- #
# Helper eksekusi (abstraksi placeholder, RETURNING id, & reconnect)
# --------------------------------------------------------------------------- #
def _q(sql: str) -> str:
    """Terjemahkan placeholder gaya SQLite (?) ke gaya psycopg (%s)."""
    return sql.replace("?", "%s") if _backend == "postgres" else sql


def _cursor(sql, params):
    cur = _conn.cursor()
    cur.execute(_q(sql), params)
    return cur


def _with_retry(fn):
    """Jalankan fn; bila koneksi postgres putus (autosuspend Neon), reconnect
    lalu coba sekali lagi."""
    try:
        return fn()
    except _RETRY_ERRORS:
        _reconnect()
        return fn()


def _fetchone(sql, params=()):
    return _with_retry(lambda: _cursor(sql, params).fetchone())


def _fetchall(sql, params=()):
    return _with_retry(lambda: _cursor(sql, params).fetchall())


def _execute(sql, params=()):
    """INSERT/UPDATE/DELETE tanpa butuh id balik."""
    def run():
        cur = _cursor(sql, params)
        if _backend == "sqlite":
            _conn.commit()
        return cur
    with _lock:
        return _with_retry(run)


def _insert(sql, params=()):
    """INSERT yang mengembalikan id baris baru."""
    def run():
        if _backend == "postgres":
            cur = _cursor(sql + " RETURNING id", params)
            return cur.fetchone()["id"]
        cur = _cursor(sql, params)
        _conn.commit()
        return cur.lastrowid
    with _lock:
        return _with_retry(run)


# --------------------------------------------------------------------------- #
# Reminders
# --------------------------------------------------------------------------- #
def create_reminder(chat_id, thread_id, creator_id, creator_name, title, body,
                    freq, interval, start_utc, tz) -> int:
    return _insert(
        """INSERT INTO reminders
           (chat_id, thread_id, creator_id, creator_name, title, body,
            freq, interval, start_utc, tz, active, created_at)
           VALUES (?,?,?,?,?,?,?,?,?,?,1,?)""",
        (chat_id, thread_id, creator_id, creator_name, title, body,
         freq, interval, start_utc, tz, _now()),
    )


def add_assignee(reminder_id, user_id, username, display_name) -> None:
    _execute(
        "INSERT INTO assignees (reminder_id, user_id, username, display_name) VALUES (?,?,?,?)",
        (reminder_id, user_id, username, display_name),
    )


def get_reminder(reminder_id):
    return _fetchone("SELECT * FROM reminders WHERE id=?", (reminder_id,))


def get_assignees(reminder_id):
    return _fetchall("SELECT * FROM assignees WHERE reminder_id=?", (reminder_id,))


def list_active_reminders():
    return _fetchall("SELECT * FROM reminders WHERE active=1")


def list_reminders_in_chat(chat_id, thread_id):
    if thread_id is None:
        return _fetchall(
            "SELECT * FROM reminders WHERE chat_id=? AND thread_id IS NULL AND active=1 ORDER BY id",
            (chat_id,),
        )
    return _fetchall(
        "SELECT * FROM reminders WHERE chat_id=? AND thread_id=? AND active=1 ORDER BY id",
        (chat_id, thread_id),
    )


def deactivate_reminder(reminder_id) -> bool:
    cur = _execute("UPDATE reminders SET active=0 WHERE id=?", (reminder_id,))
    return cur.rowcount > 0


# --------------------------------------------------------------------------- #
# Runs & messages
# --------------------------------------------------------------------------- #
def create_run(reminder_id, message_id) -> int:
    """Buat run + simpan pesan reminder secara atomik."""
    def run():
        if _backend == "postgres":
            with _conn.transaction():
                cur = _conn.cursor()
                cur.execute(
                    _q("INSERT INTO runs (reminder_id, status, run_at) VALUES (?, 'open', ?)")
                    + " RETURNING id",
                    (reminder_id, _now()),
                )
                run_id = cur.fetchone()["id"]
                cur.execute(
                    _q("INSERT INTO run_messages (run_id, message_id, kind) VALUES (?,?, 'reminder')"),
                    (run_id, message_id),
                )
            return run_id
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

    with _lock:
        return _with_retry(run)


def add_run_message(run_id, message_id, kind) -> None:
    _execute(
        "INSERT INTO run_messages (run_id, message_id, kind) VALUES (?,?,?)",
        (run_id, message_id, kind),
    )


def get_run_by_message(chat_id, message_id):
    """Cari run berdasarkan pesan bot yang di-reply. Mengembalikan (run_row, kind) atau None."""
    row = _fetchone(
        """SELECT r.*, m.kind AS msg_kind
           FROM run_messages m
           JOIN runs r       ON r.id = m.run_id
           JOIN reminders rem ON rem.id = r.reminder_id
           WHERE m.message_id=? AND rem.chat_id=?
           ORDER BY m.id DESC LIMIT 1""",
        (message_id, chat_id),
    )
    if not row:
        return None
    return row, row["msg_kind"]


def get_open_runs():
    return _fetchall("SELECT * FROM runs WHERE status='open'")


def get_last_run_at(reminder_id):
    """Epoch run terakhir untuk reminder, atau None jika belum pernah fire."""
    row = _fetchone("SELECT MAX(run_at) AS m FROM runs WHERE reminder_id=?", (reminder_id,))
    return row["m"] if row and row["m"] is not None else None


def get_run_reminder_message(run_id):
    row = _fetchone(
        "SELECT message_id FROM run_messages WHERE run_id=? AND kind='reminder' ORDER BY id LIMIT 1",
        (run_id,),
    )
    return row["message_id"] if row else None


def set_run_status(run_id, status) -> None:
    _execute("UPDATE runs SET status=? WHERE id=?", (status, run_id))


# --------------------------------------------------------------------------- #
# Progress
# --------------------------------------------------------------------------- #
def add_progress(run_id, user_id, username, display_name, text) -> None:
    _execute(
        """INSERT INTO progress (run_id, user_id, username, display_name, text, created_at)
           VALUES (?,?,?,?,?,?)""",
        (run_id, user_id, username, display_name, text, _now()),
    )


def _assignee_clause(assignee):
    """Bangun klausa WHERE untuk mencocokkan progress dengan assignee."""
    if assignee["user_id"]:
        return "user_id=?", (assignee["user_id"],)
    if assignee["username"]:
        return "LOWER(username)=LOWER(?)", (assignee["username"],)
    return "0=1", ()  # tidak bisa dicocokkan


def has_replied(run_id, assignee) -> bool:
    clause, params = _assignee_clause(assignee)
    row = _fetchone(
        f"SELECT 1 FROM progress WHERE run_id=? AND {clause} LIMIT 1",
        (run_id, *params),
    )
    return row is not None


def get_latest_progress(run_id, assignee):
    clause, params = _assignee_clause(assignee)
    row = _fetchone(
        f"""SELECT text FROM progress
            WHERE run_id=? AND {clause}
            ORDER BY id DESC LIMIT 1""",
        (run_id, *params),
    )
    return row["text"] if row else None
