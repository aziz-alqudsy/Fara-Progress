"""Penyusunan teks pesan (HTML) untuk reminder, nag, dan summary."""
from html import escape

FREQ_LABEL = {
    "once": "sekali",
    "hourly": "tiap jam",
    "daily": "harian",
    "weekly": "mingguan",
    "monthly": "bulanan",
    "yearly": "tahunan",
}


def assignee_mention(assignee) -> str:
    """Bangun mention HTML dari sebuah baris assignee."""
    display = assignee["display_name"] or assignee["username"] or "pengguna"
    if assignee["user_id"]:
        return f'<a href="tg://user?id={assignee["user_id"]}">{escape(display)}</a>'
    if assignee["username"]:
        return f"@{escape(assignee['username'])}"
    return escape(display)


def _freq_text(freq, interval) -> str:
    label = FREQ_LABEL.get(freq, freq)
    if freq == "once" or interval <= 1:
        return label
    unit = {
        "hourly": "jam",
        "daily": "hari",
        "weekly": "minggu",
        "monthly": "bulan",
        "yearly": "tahun",
    }.get(freq, "")
    return f"tiap {interval} {unit}"


def build_reminder_text(reminder, assignees) -> str:
    title = escape(reminder["title"] or "Reminder")
    body = escape(reminder["body"])
    lines = [f"🔔 <b>{title}</b>", "", body, ""]
    if assignees:
        mentions = ", ".join(assignee_mention(a) for a in assignees)
        lines.append(f"👥 <b>Ditugaskan ke:</b> {mentions}")
    lines.append(f"🔁 <i>Jadwal: {_freq_text(reminder['freq'], reminder['interval'])}</i>")
    lines.append("")
    lines.append(
        "💬 <b>Cara update:</b> balas (reply/quote) pesan ini dengan <b>nomor task</b>, contoh:"
    )
    lines.append("<code>1. selesai\n2. masih proses\n3. belum mulai</code>")
    return "\n".join(lines)


def build_nag_text(reminder, pending_assignees) -> str:
    title = escape(reminder["title"] or "Reminder")
    mentions = ", ".join(assignee_mention(a) for a in pending_assignees)
    return (
        "⏰ <b>Pengingat harian</b>\n"
        f"Belum update progress untuk: <b>{title}</b>\n\n"
        f"{mentions}\n\n"
        "💬 Mohon balas (reply/quote) pesan reminder dengan <b>nomor task</b>, contoh:\n"
        "<code>1. selesai\n2. masih proses</code>"
    )


def build_summary_text(reminder, rows) -> str:
    """rows = list of (mention_html, progress_text)."""
    title = escape(reminder["title"] or "Reminder")
    out = [
        "✅ <b>Semua peserta sudah update progress!</b>",
        f"📋 Ringkasan: <b>{title}</b>",
        "",
    ]
    for mention_html, text in rows:
        text = (text or "-").strip() or "-"
        indented = "\n".join("  " + line for line in escape(text).split("\n"))
        out.append(f"• {mention_html}:\n{indented}")
    return "\n".join(out)
