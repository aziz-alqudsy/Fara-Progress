"""Validasi penulisan task (numbering) dan ekstraksi mention."""
import re

from telegram import Message, MessageEntity

# "1. teks"  atau  "1) teks"  (harus ada isi setelah penanda)
_MAIN = re.compile(r"^\d+[.)]\s+\S")
# "a. teks" atau "a) teks"  (satu huruf a-z)
_SUB = re.compile(r"^[a-zA-Z][.)]\s+\S")


class TaskFormatError(ValueError):
    """Diangkat ketika format penulisan task tidak valid."""


def validate_task_text(text: str) -> None:
    """Pastikan setiap baris memakai numbering (atau sub-abjad).

    Aturan:
      * Baris utama harus diawali angka:  1.  2.  3.  (atau 1) 2) ...)
      * Sub-item diawali huruf:           a.  b.  c.  (harus di bawah item bernomor)
      * Baris kosong diabaikan.
      * Baris berisi teks tanpa penanda numbering -> ditolak.
    """
    if not text or not text.strip():
        raise TaskFormatError(
            "Belum ada task. Tulis minimal satu item bernomor, contoh:\n1. Selesaikan laporan"
        )

    seen_main = False
    has_item = False

    for raw in text.split("\n"):
        line = raw.strip()
        if not line:
            continue
        if _MAIN.match(line):
            seen_main = True
            has_item = True
        elif _SUB.match(line):
            if not seen_main:
                raise TaskFormatError(
                    "Sub-item (a. b. c.) harus berada di bawah item bernomor.\n"
                    f"Baris bermasalah:\n«{line}»"
                )
            has_item = True
        else:
            raise TaskFormatError(
                "Setiap baris harus memakai numbering.\n"
                f"Baris ini tidak diawali nomor/abjad:\n«{line}»\n\n"
                "Contoh format yang benar:\n"
                "1. Item pertama\n"
                "   a. sub item\n"
                "   b. sub item\n"
                "2. Item kedua"
            )

    if not has_item:
        raise TaskFormatError("Belum ada item task yang valid.")


def extract_assignees(message: Message):
    """Ambil daftar pengguna yang di-mention pada pesan task.

    Mengembalikan list dict unik: {user_id, username, display_name}.
    Menangani dua tipe entity Telegram:
      * MENTION       -> "@username" (user_id tidak diketahui)
      * TEXT_MENTION  -> berisi objek User (untuk pengguna tanpa username)
    """
    found = {}
    if not message:
        return []

    entities = message.parse_entities(
        types=[MessageEntity.MENTION, MessageEntity.TEXT_MENTION]
    )
    for entity, value in entities.items():
        if entity.type == MessageEntity.TEXT_MENTION and entity.user:
            user = entity.user
            key = f"id:{user.id}"
            found[key] = {
                "user_id": user.id,
                "username": user.username,
                "display_name": user.full_name,
            }
        elif entity.type == MessageEntity.MENTION:
            uname = value.lstrip("@")
            key = f"un:{uname.lower()}"
            found.setdefault(
                key, {"user_id": None, "username": uname, "display_name": uname}
            )

    return list(found.values())
