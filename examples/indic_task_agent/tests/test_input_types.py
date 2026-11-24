"""Input types beyond the demos: HEIC phone photos are read; Word/Excel files and archives are refused with a hint."""

import zipfile

import pytest
from fakes import FakeBodhan
from PIL import Image

from bodhan_mcp.documents import classify
from bodhan_mcp.registry import invoke
from bodhan_mcp.tools import TOOL_BY_NAME


async def call(ctx, tool, **a):
    return await invoke(TOOL_BY_NAME[tool], ctx, a)


def heic(path):
    from pillow_heif import register_heif_opener

    register_heif_opener()
    Image.new("RGB", (320, 200), "white").save(path, format="HEIF")
    return path


async def test_a_heic_phone_photo_is_read_by_ocr(make_ctx, tmp_path):
    photo = heic(tmp_path / "notice.heic")
    fake = FakeBodhan()
    fake.ocr_pages = ["यह एक सूचना है।"]
    r, is_error = await call(make_ctx(fake), "ocr_document", path=str(photo))
    assert not is_error, r
    assert fake.calls["ocr"] == 1 and r["pages_ocr"] == 1
    r, is_error = await call(make_ctx(fake), "inspect_files", paths=[str(photo)])
    assert r["files"][0]["kind"] == "image" and r["files"][0]["width"] == 320 and r["files"][0]["suggested_tool"] == "ocr_document"


def test_a_heic_photo_without_its_extension_is_still_an_image(tmp_path):
    photo = heic(tmp_path / "IMG_0042.heic").rename(tmp_path / "IMG_0042")
    assert classify(photo) == "image"


def office(path):
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("[Content_Types].xml", "<Types/>")
    return path


@pytest.mark.parametrize("name,kind,word", [("letter.docx", "office", "export"), ("sheet.xlsx", "office", "export"),
                                            ("inputs.zip", "archive", "extract"), ("inputs_no_ext", "archive", "extract")])
async def test_office_files_and_archives_are_refused_with_a_hint(make_ctx, tmp_path, name, kind, word):
    f = office(tmp_path / name)
    assert classify(f) == kind
    fake = FakeBodhan()
    for tool in ("read_text", "ocr_document", "transcribe_audio"):
        r, is_error = await call(make_ctx(fake), tool, path=str(f))
        assert is_error and r["error"]["code"] == "unsupported_file_type", (tool, r)
        assert word in r["error"]["hint"], (tool, r["error"])
    r, _ = await call(make_ctx(fake), "inspect_files", paths=[str(f)])
    assert r["files"][0]["kind"] == kind and word in r["files"][0]["note"]
    assert sum(fake.calls.values()) == 0


async def test_inspect_results_for_every_kind_match_the_published_schema(make_ctx, tmp_path):
    import jsonschema

    from bodhan_mcp.tools.schemas import output_schema

    files = [office(tmp_path / "a.docx"), office(tmp_path / "b.zip"), heic(tmp_path / "c.heic")]
    r, is_error = await call(make_ctx(FakeBodhan()), "inspect_files", paths=[str(f) for f in files])
    assert not is_error and {f["kind"] for f in r["files"]} == {"office", "archive", "image"}
    jsonschema.validate(r, output_schema("inspect_files"))
