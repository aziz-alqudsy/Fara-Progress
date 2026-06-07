"""Smoke test ringan: validasi parser & alur DB (tanpa koneksi Telegram)."""
import os
import tempfile

from bot import db, parser, texts


def test_parser():
    # valid
    parser.validate_task_text("1. a\n   a. sub\n2. b")
    parser.validate_task_text("1) item\n2) item")
    # blank lines diperbolehkan
    parser.validate_task_text("1. a\n\n2. b")

    # sub tanpa main -> error
    try:
        parser.validate_task_text("a. sub dulu")
        assert False, "harus error"
    except parser.TaskFormatError:
        pass

    # baris tanpa numbering -> error
    try:
        parser.validate_task_text("1. ok\nbaris bebas")
        assert False, "harus error"
    except parser.TaskFormatError:
        pass

    # kosong -> error
    try:
        parser.validate_task_text("   ")
        assert False, "harus error"
    except parser.TaskFormatError:
        pass
    print("OK parser")


def test_db_flow():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    db.init_db(path)

    rid = db.create_reminder(
        chat_id=-100, thread_id=None, creator_id=1, creator_name="Boss",
        title="T", body="1. kerjakan", freq="daily", interval=1,
        start_utc=1_000_000.0, tz="Asia/Jakarta",
    )
    db.add_assignee(rid, 11, "budi", "Budi")
    db.add_assignee(rid, None, "sari", "Sari")  # username-only

    run_id = db.create_run(rid, message_id=555)

    # lookup by message
    found = db.get_run_by_message(-100, 555)
    assert found is not None and found[1] == "reminder"
    assert db.get_run_by_message(-100, 999) is None  # bukan pesan terlacak

    assignees = db.get_assignees(rid)
    assert len(assignees) == 2
    assert all(not db.has_replied(run_id, a) for a in assignees)

    # budi (user_id) update
    db.add_progress(run_id, 11, "budi", "Budi", "selesai 80%")
    # sari (username-only) update -> cocokkan via username
    db.add_progress(run_id, 22, "sari", "Sari", "done")

    done = [a for a in assignees if db.has_replied(run_id, a)]
    assert len(done) == 2, done
    assert db.get_latest_progress(run_id, assignees[0]) == "selesai 80%"

    # summary -> status berubah & reply berikutnya harus diabaikan
    db.add_run_message(run_id, 777, "summary")
    db.set_run_status(run_id, "summarized")
    run, kind = db.get_run_by_message(-100, 555)
    assert run["status"] == "summarized"
    _, kind_sum = db.get_run_by_message(-100, 777)
    assert kind_sum == "summary"

    # texts builder tidak error
    txt = texts.build_summary_text(db.get_reminder(rid),
                                   [(texts.assignee_mention(a),
                                     db.get_latest_progress(run_id, a)) for a in assignees])
    assert "Ringkasan" in txt

    db.deactivate_reminder(rid)
    assert db.get_reminder(rid)["active"] == 0
    print("OK db flow")


if __name__ == "__main__":
    test_parser()
    test_db_flow()
    print("\nSEMUA TEST LULUS")
