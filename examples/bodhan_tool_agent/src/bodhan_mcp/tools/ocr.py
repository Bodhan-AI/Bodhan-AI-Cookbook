"""OCR tools for single images and multi-page PDFs using Indic-OCR."""

from pathlib import Path

from bodhan_mcp import config, errors
from bodhan_mcp.clients.bodhan import BodhanClient
from bodhan_mcp.schemas import OcrDocOut, OcrPageOut
from bodhan_mcp.utils import pdf as pdf_utils


async def ocr_page(
    image_path: str,
    page_num: int = 1,
    client: BodhanClient | None = None,
) -> OcrPageOut | errors.ToolErrorPayload:
    """Extract Markdown text from a single image or page."""
    file_path = Path(image_path)
    if not file_path.exists():
        return errors.make_tool_error(
            code=errors.FILE_NOT_FOUND,
            message=f"Image file not found: {file_path}",
            hint="Verify image_path exists.",
            details={"image_path": str(file_path)},
        )

    bodhan = client or BodhanClient()
    try:
        image_bytes = file_path.read_bytes()
        res = await bodhan.ocr(image_bytes=image_bytes, filename=file_path.name)
        raw_data = res.get("data", {})
        markdown = raw_data.get("markdown") or raw_data.get("text", "")
        cost = float(res.get("cost_inr", config.PRICES["ocr_per_image"]))
        return OcrPageOut(markdown=markdown, page=page_num, cost_inr=round(cost, 4))
    except Exception as exc:
        return errors.make_tool_error(
            code=errors.INTERNAL,
            message=f"OCR failed on image: {exc}",
            hint="Check image format and upstream status.",
            details={"error": str(exc)},
        )


async def ocr_document(
    pdf_path: str,
    client: BodhanClient | None = None,
) -> OcrDocOut | errors.ToolErrorPayload:
    """Extract Markdown text from a multi-page PDF document."""
    file_path = Path(pdf_path)
    if not file_path.exists():
        return errors.make_tool_error(
            code=errors.FILE_NOT_FOUND,
            message=f"PDF document not found: {file_path}",
            hint="Verify pdf_path exists.",
            details={"pdf_path": str(file_path)},
        )

    try:
        rendered_pages = pdf_utils.render_pages(file_path)
    except Exception as exc:
        return errors.make_tool_error(
            code=errors.UNREADABLE_MEDIA,
            message=f"Failed to render PDF pages: {exc}",
            hint="Ensure PDF is not corrupt or password-protected.",
            details={"pdf_path": str(file_path), "error": str(exc)},
        )

    markdown_sections: list[str] = []
    total_cost = 0.0

    for idx, page_img_path in enumerate(rendered_pages, start=1):
        page_res = await ocr_page(image_path=str(page_img_path), page_num=idx, client=client)
        if isinstance(page_res, errors.ToolErrorPayload):
            return page_res
        markdown_sections.append(page_res.markdown)
        total_cost += page_res.cost_inr

    full_markdown = "\n\n---\n\n".join(markdown_sections)
    return OcrDocOut(
        markdown=full_markdown,
        pages=len(rendered_pages),
        cost_inr=round(total_cost, 4),
    )
