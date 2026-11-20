"""Tests for translation pagination, structural boundaries, and table preservation (Slice 7)."""

import httpx
import pytest

from bodhan_mcp import errors
from bodhan_mcp.clients.base import BaseBodhanClient
from bodhan_mcp.clients.bodhan import BodhanClient
from bodhan_mcp.schemas import TranslateOut
from bodhan_mcp.tools.translate import (
    split_on_structural_boundaries,
    translate_text,
)


def test_split_on_structural_boundaries_never_splits_table():
    """Verify 32K context pagination never splits across Markdown table rows."""
    header = "# Section 1\n\nSome introductory text before the table.\n\n"
    # Create a 20-row markdown table
    table_lines = [
        "| ID | Name | Role | Salary |",
        "|---|---|---|---|",
    ]
    for i in range(20):
        table_lines.append(f"| {i} | Person_{i} | Engineer | 100000 |")
    table_text = "\n".join(table_lines) + "\n\n"
    footer = "## Section 2\n\nConcluding remarks after the table."

    full_document = header + table_text + footer

    # Force a small token limit (e.g. 50 tokens) to trigger splitting
    chunks = split_on_structural_boundaries(full_document, max_tokens=50)
    assert len(chunks) > 1

    # Invariant: Every table line must be together in the same chunk
    for chunk in chunks:
        if "| ID | Name |" in chunk:
            for i in range(20):
                assert f"| {i} | Person_{i} |" in chunk, "Table rows were split across chunks!"


@pytest.mark.asyncio
async def test_translate_text_unsupported_language():
    """Verify translation rejects unsupported languages honestly."""
    # French is not supported
    err1 = await translate_text(text="Hello", source_lang="fr", target_lang="hi")
    assert isinstance(err1, errors.ToolErrorPayload)
    assert err1.code == errors.UNSUPPORTED_LANGUAGE

    err2 = await translate_text(text="Hello", source_lang="en", target_lang="de")
    assert isinstance(err2, errors.ToolErrorPayload)
    assert err2.code == errors.UNSUPPORTED_LANGUAGE


@pytest.mark.asyncio
async def test_translate_text_pagination_loop():
    """Verify translate_text paginates large text, calls model sequentially, and joins output."""
    calls = []

    def mock_handler(request: httpx.Request) -> httpx.Response:
        content = request.read().decode("utf-8")
        calls.append(content)
        return httpx.Response(
            200,
            json={
                "choices": [
                    {"message": {"content": f"[Trans_{len(calls)}]"}}
                ]
            },
        )

    transport = httpx.MockTransport(mock_handler)
    base_client = BaseBodhanClient(base_url="https://mock.bodhan.ai/v1")
    base_client._client = httpx.AsyncClient(transport=transport)
    bodhan = BodhanClient(base_client=base_client)

    # Text that will split into 2 chunks with max_chunk_tokens=20
    text = "Paragraph one with some text.\n\nParagraph two with more text.\n\nParagraph three."
    res = await translate_text(
        text=text,
        source_lang="en",
        target_lang="ta",
        max_chunk_tokens=10,
        client=bodhan,
    )

    assert isinstance(res, TranslateOut)
    assert len(calls) >= 2
    assert "[Trans_1]" in res.translated
    assert "[Trans_2]" in res.translated
    await base_client.close()
