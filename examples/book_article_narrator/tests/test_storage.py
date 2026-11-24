from narrator import storage
from narrator.pipeline import NarrationResult


def make_result(text="Hello.", wav=b"RIFFdata"):
    return NarrationResult(
        original_text=text,
        target_language="hi",
        voice="Kavya",
        style=None,
        paragraphs=[text],
        translated_paragraphs=["नमस्ते।"],
        chunks=["नमस्ते।"],
        wav_bytes=wav,
    )


def test_save_and_load_round_trip(tmp_path):
    db = tmp_path / "test.db"
    new_id = storage.save_narration(make_result(), db_path=db)
    row = storage.load_narration(new_id, db_path=db)
    assert row["original_text"] == "Hello."
    assert row["translated_text"] == "नमस्ते।"
    assert row["language"] == "hi"
    assert row["voice"] == "Kavya"
    assert row["audio"] == b"RIFFdata"


def test_list_is_newest_first_and_has_no_audio(tmp_path):
    db = tmp_path / "test.db"
    ids = [storage.save_narration(make_result(f"T{i}."), db_path=db) for i in range(3)]
    items = storage.list_narrations(db_path=db)
    assert [item["id"] for item in items] == ids[::-1]
    assert "audio" not in items[0]


def test_history_limit_keeps_the_newest(tmp_path):
    db = tmp_path / "test.db"
    for i in range(5):
        storage.save_narration(make_result(f"T{i}."), db_path=db, limit=3)
    items = storage.list_narrations(db_path=db)
    assert len(items) == 3
    assert items[0]["original_text"] == "T4."


def test_delete_removes_only_that_row(tmp_path):
    db = tmp_path / "test.db"
    first = storage.save_narration(make_result("A."), db_path=db)
    second = storage.save_narration(make_result("B."), db_path=db)
    storage.delete_narration(first, db_path=db)
    assert storage.load_narration(first, db_path=db) is None
    assert storage.load_narration(second, db_path=db) is not None


def test_missing_id_returns_none(tmp_path):
    assert storage.load_narration(999, db_path=tmp_path / "test.db") is None


def test_awkward_text_is_stored_literally(tmp_path):
    db = tmp_path / "test.db"
    nasty = "x'); DROP TABLE narrations;--"
    new_id = storage.save_narration(make_result(nasty), db_path=db)
    assert storage.load_narration(new_id, db_path=db)["original_text"] == nasty
    assert len(storage.list_narrations(db_path=db)) == 1


def test_clear_history_removes_everything(tmp_path):
    db = tmp_path / "test.db"
    storage.save_narration(make_result("A."), db_path=db)
    storage.save_narration(make_result("B."), db_path=db)
    storage.clear_history(db_path=db)
    assert storage.list_narrations(db_path=db) == []