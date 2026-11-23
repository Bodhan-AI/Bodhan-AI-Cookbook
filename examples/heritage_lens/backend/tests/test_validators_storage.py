import tempfile
from pathlib import Path

import pytest

from app.core.errors import NotFoundError, PayloadTooLarge, UnsupportedMediaType, ValidationFailed
from app.services.storage.files import FileStore
from app.services.storage.validators import (
    AUDIO, DOCUMENT, IMAGE, sanitize_filename, validate_upload,
)

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 64
PDF = b"%PDF-1.7\n" + b"x" * 64
WAV = b"RIFF\x24\x00\x00\x00WAVEfmt " + b"\x00" * 64
MB = 1024 * 1024


def test_valid_uploads():
    assert validate_upload(IMAGE, "a.PNG", "image/png", PNG, MB).extension == ".png"
    assert validate_upload(IMAGE, "a.jpeg", "image/jpeg", JPEG, MB).extension == ".jpg"
    assert validate_upload(DOCUMENT, "scan.pdf", "application/pdf", PDF, MB).sniffed_type == "pdf"
    v = validate_upload(AUDIO, "rec.wav", "audio/wav; codecs=1", WAV, MB)
    assert v.sniffed_type == "wav" and v.mime_type == "audio/wav"
    assert validate_upload(AUDIO, "rec.wav", "application/octet-stream", WAV, MB)  # generic MIME allowed


def test_extension_not_allowed():
    for name in ("evil.exe", "x.svg", "x.html", "noext", "x.gif"):
        with pytest.raises(UnsupportedMediaType):
            validate_upload(IMAGE, name, "image/png", PNG, MB)


def test_mime_not_allowed():
    with pytest.raises(UnsupportedMediaType):
        validate_upload(IMAGE, "a.png", "text/html", PNG, MB)


def test_signature_must_match_content():
    with pytest.raises(UnsupportedMediaType):
        validate_upload(IMAGE, "a.png", "image/png", b"<html><script>alert(1)</script></html>", MB)
    with pytest.raises(UnsupportedMediaType, match="does not match"):
        validate_upload(IMAGE, "a.png", "image/png", JPEG, MB)  # jpeg bytes named .png


def test_pdf_not_allowed_as_image_but_allowed_as_document():
    with pytest.raises(UnsupportedMediaType):
        validate_upload(IMAGE, "a.pdf", "application/pdf", PDF, MB)
    assert validate_upload(DOCUMENT, "a.pdf", "application/pdf", PDF, MB)


def test_audio_not_allowed_as_document():
    with pytest.raises(UnsupportedMediaType):
        validate_upload(DOCUMENT, "a.wav", "audio/wav", WAV, MB)


def test_size_and_empty():
    with pytest.raises(PayloadTooLarge):
        validate_upload(IMAGE, "a.png", "image/png", PNG, 10)
    with pytest.raises(ValidationFailed):
        validate_upload(IMAGE, "a.png", "image/png", b"", MB)


def test_sanitize_filename():
    assert sanitize_filename("../../etc/passwd") == "passwd"
    assert sanitize_filename("C:\\Users\\x\\scan.png") == "scan.png"
    assert sanitize_filename("a\x00b\x1f.png") == "ab.png"
    assert sanitize_filename("") == "upload"
    assert sanitize_filename("...") == "upload"
    assert len(sanitize_filename("a" * 500 + ".png")) <= 120
    assert sanitize_filename("తెలుగు కథ.png") == "తెలుగు కథ.png"


def test_store_saves_privately_with_random_names():
    with tempfile.TemporaryDirectory() as d:
        store = FileStore(d)
        f = store.save_bytes(user_id=7, category="audio", data=b"hello", extension=".wav")
        assert f.relative_path.startswith("users/7/audio/") and f.relative_path.endswith(".wav")
        assert store.read(f.relative_path) == b"hello"
        assert f.size == 5 and len(f.sha256) == 64
        g = store.save_bytes(user_id=7, category="audio", data=b"hello", extension=".wav")
        assert g.relative_path != f.relative_path
        assert store.delete(f.relative_path) is True
        assert store.delete(f.relative_path) is False
        assert not store.exists(f.relative_path)


def test_path_traversal_is_blocked():
    with tempfile.TemporaryDirectory() as d:
        store = FileStore(Path(d) / "root")
        (Path(d) / "secret.txt").write_text("top secret")
        for bad in ("../secret.txt", "users/../../secret.txt", "/etc/passwd", "..\\secret.txt",
                    "users/1/\x00x", "", "users/1/audio/../../../../secret.txt"):
            with pytest.raises(NotFoundError):
                store.path_for(bad)
            assert store.exists(bad) is False


def test_symlink_escape_is_blocked():
    with tempfile.TemporaryDirectory() as d:
        root = Path(d) / "root"
        store = FileStore(root)
        outside = Path(d) / "outside"
        outside.mkdir()
        (outside / "s.txt").write_text("secret")
        link = root / "users"
        try:
            link.symlink_to(outside, target_is_directory=True)
        except OSError:
            return  # symlinks unavailable (e.g. unprivileged Windows)
        with pytest.raises(NotFoundError):
            store.path_for("users/s.txt")


def test_store_rejects_bad_category_and_extension():
    with tempfile.TemporaryDirectory() as d:
        store = FileStore(d)
        with pytest.raises(ValueError):
            store.save_bytes(user_id=1, category="../x", data=b"a", extension=".png")
        with pytest.raises(ValueError):
            store.save_bytes(user_id=1, category="images", data=b"a", extension="/../x")
