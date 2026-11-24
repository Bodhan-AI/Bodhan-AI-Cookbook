import pytest
from sqlalchemy.exc import IntegrityError

from app.database.connection import create_db_engine, create_session_factory, init_db
from app.models import (
    AudioKind, AudioRecording, HeritageStory, HistoricalDocument, TimeCapsule, Translation, User,
)


@pytest.fixture
def db():
    engine = create_db_engine("sqlite://")
    init_db(engine)
    with create_session_factory(engine)() as session:
        yield session


def _user(db, n="u"):
    u = User(username=n, email=f"{n}@x.com", password_hash="h")
    db.add(u)
    db.commit()
    return u


def test_timestamps_are_timezone_aware_utc(db):
    u = _user(db)
    db.refresh(u)
    assert u.created_at.tzinfo is not None and u.created_at.utcoffset().total_seconds() == 0


def test_unique_username_and_email(db):
    _user(db, "a")
    db.add(User(username="a", email="other@x.com", password_hash="h"))
    with pytest.raises(IntegrityError):
        db.commit()


def test_translation_is_separate_from_story_original(db):
    u = _user(db)
    s = HeritageStory(user_id=u.id, title="t", original_text="ORIGINAL")
    db.add(s)
    db.commit()
    db.add(Translation(user_id=u.id, story_id=s.id, target_language="en", original_text="ORIGINAL", translated_text="X"))
    db.commit()
    db.refresh(s)
    assert s.original_text == "ORIGINAL" and s.translations[0].translated_text == "X"


def test_deleting_story_cascades_derived_but_detaches_originals(db):
    u = _user(db)
    s = HeritageStory(user_id=u.id, title="t")
    db.add(s)
    db.commit()
    db.add_all([Translation(user_id=u.id, story_id=s.id, target_language="en", original_text="a", translated_text="b"),
                AudioRecording(user_id=u.id, story_id=s.id, original_audio_path="users/1/audio/a.wav"),
                HistoricalDocument(user_id=u.id, story_id=s.id, original_filename="d.png", stored_path="p",
                                   mime_type="image/png", size_bytes=1, sha256="0" * 64)])
    db.commit()
    db.delete(s)
    db.commit()
    assert db.query(Translation).count() == 0
    assert db.query(AudioRecording).one().story_id is None
    assert db.query(HistoricalDocument).one().story_id is None


def test_audio_needs_some_path(db):
    u = _user(db)
    db.add(AudioRecording(user_id=u.id, kind=AudioKind.original))
    with pytest.raises(IntegrityError):
        db.commit()


def test_foreign_keys_enforced(db):
    db.add(HeritageStory(user_id=999, title="orphan"))
    with pytest.raises(IntegrityError):
        db.commit()


def test_coordinate_check_constraint(db):
    u = _user(db)
    db.add(HeritageStory(user_id=u.id, title="t", latitude=123.0, longitude=1.0))
    with pytest.raises(IntegrityError):
        db.commit()


def test_deleting_user_removes_their_data(db):
    u = _user(db)
    db.add_all([HeritageStory(user_id=u.id, title="t"), TimeCapsule(user_id=u.id, title="c")])
    db.commit()
    db.delete(u)
    db.commit()
    assert db.query(HeritageStory).count() == 0 and db.query(TimeCapsule).count() == 0
