"""
Summarize English text using the LLM brain.
Per Bodhan guidelines: the LLM may write English summaries of source materials,
which are then passed to Indic-Translate for translation into Indian languages.
"""
from __future__ import annotations

from bodhan_agent.llm_client import get_llm_client, get_llm_model


async def summarize_english(text: str, target_duration_seconds: int = 120) -> dict:
    """Summarize English text into a spoken summary.

    Args:
        text: English source text (from OCR or documents).
        target_duration_seconds: Desired narration length in seconds (default 120s / 2 min).

    Returns:
        {"summary_text": str, "word_count": int}
    """
    max_words = int((target_duration_seconds / 60) * 130)
    client = get_llm_client()
    model = get_llm_model()
    resp = await client.chat.completions.create(
        model=model,
        messages=[
            {
                "role": "system",
                "content": (
                    f"You are a summarization assistant. Summarize the following text in English "
                    f"into a clear, educational spoken summary of approximately {max_words} words "
                    f"(targeted for a {target_duration_seconds}-second audio read). "
                    "Output ONLY the summary text, with no preamble or title."
                ),
            },
            {"role": "user", "content": text[:15000]},
        ],
    )
    summary = resp.choices[0].message.content.strip()
    return {"summary_text": summary, "word_count": len(summary.split())}
