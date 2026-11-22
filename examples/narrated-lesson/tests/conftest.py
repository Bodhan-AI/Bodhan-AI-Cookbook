import pytest

from bodhan_lesson.models import Block, Page


@pytest.fixture
def make_block():
    def _make(text: str = "", *, type: str = "Text", label: str = "Paragraph", conf: float = 0.95,
              bbox=(100.0, 300.0, 700.0, 340.0), page: int = 1, order: int = 0) -> Block:
        return Block(id=f"p{page:03d}.b{order:02d}", page=page, order=order, label=label, type=type,
                     bbox_xyxy=list(bbox), conf=conf, text=text)
    return _make


@pytest.fixture
def make_page():
    def _make(number: int, blocks: list[Block], image_path: str = "", height: int = 1000) -> Page:
        return Page(number=number, width=800, height=height, image_path=image_path, blocks=blocks)
    return _make
