"""Unit tests for subtitle translation, numbered lines formatting, and unsupported languages."""

import pytest
from bodhan_mcp.tools.translate import validate_language, count_markdown_table_structure
from bodhan_mcp.types import ToolError


def test_validate_language():
    # Valid Indian languages and English
    assert validate_language("hi") == "hi"
    assert validate_language("Hindi") == "hi"
    assert validate_language("ta") == "ta"
    assert validate_language("en") == "en"
    assert validate_language("mr") == "mr"

    # Unsupported non-Indian languages must raise UNSUPPORTED_LANGUAGE
    with pytest.raises(ToolError) as exc_info:
        validate_language("french")
    assert exc_info.value.error_code == "UNSUPPORTED_LANGUAGE"

    with pytest.raises(ToolError) as exc_info:
        validate_language("es")
    assert exc_info.value.error_code == "UNSUPPORTED_LANGUAGE"


def test_count_markdown_table_structure():
    md = """
# Heading

Some text before table

| Roll No | Name | Marks |
| --- | --- | --- |
| 101 | Aarav | 95 |
| 102 | Priya | 88 |

End of section.
"""
    tables = count_markdown_table_structure(md)
    assert len(tables) == 1
    rows, max_cols = tables[0]
    assert rows == 4  # Header + separator + 2 data rows
    assert max_cols == 3

