from __future__ import annotations

import inspect
import io
from pathlib import Path

from ..cache import ComputeResult, DiskCache, cost_ocr, hash_bytes, make_key
from ..client import BodhanClient
from ..languages import normalize_lang_code
from ..models import ErrorCode, ToolError, Usage, failure, success
from ..workspace import Workspace

_IMAGE_SUFFIXES = {".bmp", ".jpeg", ".jpg", ".png", ".tif", ".tiff", ".webp"}


def _default_cache(workspace: Workspace, client: BodhanClient | None) -> DiskCache:
    if client is not None and type(client) is not BodhanClient:
        return DiskCache(workspace.out_dir / ".cache")
    return DiskCache.from_env()


def _normalize_image_bytes(path: Path, raw_bytes: bytes) -> tuple[str, bytes]:
    suffix = path.suffix.lower()
    if suffix in {".png", ".jpg", ".jpeg"}:
        return path.name, raw_bytes
    from PIL import Image

    try:
        with Image.open(io.BytesIO(raw_bytes)) as img:
            converted = img.convert("RGB") if img.mode not in {"RGB", "L", "RGBA"} else img
            buf = io.BytesIO()
            converted.save(buf, format="PNG")
            return f"{path.stem}.png", buf.getvalue()
    except Exception as exc:
        raise ToolError(
            ErrorCode.UNSUPPORTED_FORMAT,
            f"Could not convert image {path.name!r} to PNG for OCR: {exc}",
            hint="Provide a readable PNG, JPEG, TIFF, BMP, or WEBP image.",
        ) from exc


def _render_pdf_pages(path: Path, page_number: int | None) -> tuple[list[tuple[int, bytes]], int]:
    import pypdfium2 as pdfium

    try:
        pdf = pdfium.PdfDocument(str(path))
    except Exception as exc:
        raise ToolError(
            ErrorCode.UNSUPPORTED_FORMAT,
            f"Could not open {path.name!r} as a readable PDF: {exc}",
            hint="Check that the file is a valid, unencrypted PDF.",
        ) from exc

    try:
        total_pages = len(pdf)
        if total_pages < 1:
            raise ToolError(ErrorCode.INVALID_ARGUMENT, f"{path.name!r} contains no PDF pages.")
        if page_number is not None and not 1 <= page_number <= total_pages:
            raise ToolError(
                ErrorCode.INVALID_ARGUMENT,
                f"Page number {page_number} is out of range for PDF (total pages: {total_pages}).",
                hint=(
                    f"Provide a page number between 1 and {total_pages}, "
                    "or omit it to OCR all pages."
                ),
            )
        selected = range(total_pages) if page_number is None else (page_number - 1,)
        images: list[tuple[int, bytes]] = []
        for index in selected:
            try:
                page = pdf[index]
                image = page.render(scale=2.0).to_pil()
                buffer = io.BytesIO()
                image.save(buffer, format="PNG")
                images.append((index + 1, buffer.getvalue()))
                page.close()
            except Exception as exc:
                raise ToolError(
                    ErrorCode.UNSUPPORTED_FORMAT,
                    f"Failed to render PDF page {index + 1}: {exc}",
                    hint="Check the PDF page or try a lower-resolution source document.",
                ) from exc
        return images, total_pages
    finally:
        pdf.close()


def ocr_document(
    workspace: Workspace,
    file_path: str,
    page_number: int | None = None,
    language: str | None = None,
    client: BodhanClient | None = None,
    *,
    table_format: str = "markdown",
    cache: DiskCache | None = None,
) -> dict:
    try:
        norm_lang = normalize_lang_code(language) if language else None
        if table_format not in {"markdown", "html"}:
            raise ToolError(
                ErrorCode.INVALID_ARGUMENT,
                f"`table_format` must be 'markdown' or 'html', got {table_format!r}.",
            )
        path = workspace.resolve_input(file_path, suffixes=_IMAGE_SUFFIXES | {".pdf"})
        if page_number is not None and (
            isinstance(page_number, bool) or not isinstance(page_number, int) or page_number < 1
        ):
            raise ToolError(
                ErrorCode.INVALID_ARGUMENT,
                "`page_number` must be a positive integer or omitted to OCR the whole document.",
            )

        if path.suffix.lower() == ".pdf":
            images, total_pages = _render_pdf_pages(path, page_number)
        else:
            if page_number not in (None, 1):
                raise ToolError(
                    ErrorCode.INVALID_ARGUMENT,
                    "Image files contain only one page; use page_number=1 or omit it.",
                )
            images, total_pages = [(1, path.read_bytes())], 1

        api_client = client or BodhanClient()
        disk_cache = cache or _default_cache(workspace, client)
        ocr_params_accepted = set(inspect.signature(api_client.ocr).parameters)
        page_results: list[dict[str, object]] = []
        warnings: list[str] = []
        charged_cost = 0.0
        avoided_cost = 0.0
        total_latency_ms = 0
        all_cached = True

        for current_page, raw_bytes in images:
            if path.suffix.lower() == ".pdf":
                filename, image_bytes = f"{path.stem}_page_{current_page}.png", raw_bytes
            else:
                filename, image_bytes = _normalize_image_bytes(path, raw_bytes)

            cache_key = make_key(
                "ocr",
                "page",
                {
                    "model": "indic-ocr",
                    "language": norm_lang,
                    "table_format": table_format,
                },
                [hash_bytes(image_bytes)],
            )

            def _compute(
                img_bytes: bytes = image_bytes,
                fname: str = filename,
                page_num: int = current_page,
            ) -> ComputeResult:
                kwargs: dict[str, object] = {
                    "image_bytes": img_bytes,
                    "filename": fname,
                    "language": norm_lang,
                }
                if "table_format" in ocr_params_accepted:
                    kwargs["table_format"] = table_format
                if "max_tokens" in ocr_params_accepted:
                    kwargs["max_tokens"] = 4096
                resp = api_client.ocr(**kwargs)
                text_val = resp.get("text", "")
                if not isinstance(text_val, str):
                    raise ToolError(
                        ErrorCode.PARTIAL_FAILURE,
                        f"OCR returned invalid text for page {page_num}.",
                    )
                resp_usage = resp.get("usage")
                page_cost = (
                    resp_usage.cost_inr
                    if isinstance(resp_usage, Usage) and resp_usage.cost_inr > 0
                    else cost_ocr(1)
                )
                latency = resp_usage.latency_ms if isinstance(resp_usage, Usage) else 0
                return ComputeResult(
                    value={"text": text_val, "latency_ms": latency},
                    cost_inr=page_cost,
                )

            cached_res = disk_cache.get_or_compute_sync("ocr", cache_key, _compute)
            text = str(cached_res.entry.value["text"])
            if not text.strip():
                warnings.append(f"Page {current_page} returned empty OCR text (possibly blank).")
            page_results.append({"page_number": current_page, "text": text})
            charged_cost += cached_res.charged_inr
            avoided_cost += cached_res.avoided_inr
            total_latency_ms += (
                0 if cached_res.cached else int(cached_res.entry.value.get("latency_ms", 0))
            )
            if not cached_res.cached:
                all_cached = False

        if not any(str(page["text"]).strip() for page in page_results):
            raise ToolError(
                ErrorCode.PARTIAL_FAILURE,
                "OCR returned no readable text for any page.",
                hint="Check image quality or retry with a clearer scan.",
            )

        if path.suffix.lower() == ".pdf":
            document_text = "\n\n".join(
                f"--- Page {page['page_number']} ---\n{page['text']}" for page in page_results
            )
        else:
            document_text = str(page_results[0]["text"])

        artifact_suffix = ".md" if table_format == "markdown" else ".txt"
        artifact = workspace.write_text(
            f"{path.stem}-ocr",
            artifact_suffix,
            document_text,
            kind="ocr",
        )
        usage = Usage(
            model="indic-ocr",
            cost_inr=round(charged_cost, 6),
            latency_ms=total_latency_ms,
            cached=all_cached,
            avoided_inr=round(avoided_cost, 6),
            units={"pages": float(len(page_results))},
        )

        expected_pages = total_pages if page_number is None or path.suffix.lower() != ".pdf" else 1
        return success(
            {
                "source_path": str(path),
                "page_number": page_results[0]["page_number"] if len(page_results) == 1 else None,
                "pages_processed": len(page_results),
                "total_pages": total_pages,
                "language": norm_lang,
                "character_count": len(document_text),
                "text": document_text,
                "pages": page_results,
                "coverage_complete": len(page_results) == expected_pages,
            },
            artifacts=[artifact],
            usage=usage,
            warnings=warnings,
        )
    except ToolError as err:
        return failure(err)
    except OSError as exc:
        return failure(
            ToolError(
                ErrorCode.INTERNAL,
                f"OCR could not read {file_path!r}: {exc.strerror or exc}",
                details={"file_path": file_path},
            )
        )
    except Exception as exc:
        return failure(
            ToolError(
                ErrorCode.INTERNAL,
                f"OCR processing failed: {type(exc).__name__}: {exc}",
                details={"file_path": file_path, "page": page_number},
            )
        )