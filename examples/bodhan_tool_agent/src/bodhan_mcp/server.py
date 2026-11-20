
from bodhan_mcp.ocr import extract_text
from bodhan_mcp.speak import speak_text
from mcp.server.fastmcp import FastMCP

from bodhan_mcp.transcribe import transcribe_audio
from bodhan_mcp.translate import translate_text


mcp = FastMCP("Bodhan Tool Server")


@mcp.tool()
def indic_transcribe(
    file_path: str,
    language: str = "",
) -> dict:
    """
    Convert an audio file into text using Bodhan Indic-Transcribe.

    Args:
        file_path: Local path to an audio file.
        language: Optional language code, such as bn, hi or ta.
    """
    return transcribe_audio(
        file_path=file_path,
        language=language or None,
    )


@mcp.tool()
def indic_translate(
    text: str,
    target_language: str,
    script: str = "native",
) -> dict:
    """
    Translate text using Bodhan Indic-Translate.

    Args:
        text: Original text to translate.
        target_language: Target language code, e.g. bn, hi, ta.
        script: Output script: native, roman or codemix.
    """
    return translate_text(
        text=text,
        target_language=target_language,
        script=script,
    )

@mcp.tool()
def indic_speak(
    text: str,
    language: str,
    voice: str,
    output_path: str,
    style: str = "",
) -> dict:
    """
    Convert text into speech using Bodhan Indic-Speak.

    Args:
        text: Text to convert into speech.
        language: Language code, such as bn, hi or ta.
        voice: Supported Bodhan voice name.
        output_path: Destination WAV file path.
        style: Optional speaking style.
    """
    return speak_text(
        text=text,
        language=language,
        voice=voice,
        output_path=output_path,
        style=style,
    )
@mcp.tool()
def indic_ocr(
    file_path: str,
    table_format: str = "html",
    max_tokens: int = 2048,
) -> dict:
    """
    Extract text and document layout from an image
    using Bodhan Indic-OCR.

    Args:
        file_path: Local PNG or JPEG image path.
        table_format: Table output format, html or markdown.
        max_tokens: Maximum transcription tokens per block.
    """
    return extract_text(
        file_path=file_path,
        table_format=table_format,
        max_tokens=max_tokens,
    )


if __name__ == "__main__":
    mcp.run(transport="stdio")
