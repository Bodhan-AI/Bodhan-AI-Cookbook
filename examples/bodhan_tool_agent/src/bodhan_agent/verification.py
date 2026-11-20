
import json

from bodhan_agent.llm import get_llm_client, get_model_name


def verify_translation(
    source_text: str,
    translated_text: str,
) -> dict:
    """
    Evaluate whether a translation appears complete.

    The LLM only evaluates the result. It must not
    rewrite or generate either language.
    """

    if not source_text or not source_text.strip():
        return {
            "status": "failed",
            "verified": False,
            "reason": "Source text is empty.",
        }

    if not translated_text or not translated_text.strip():
        return {
            "status": "failed",
            "verified": False,
            "reason": "Translation is empty.",
        }

    client = get_llm_client()
    model = get_model_name()

    system_prompt = """
    You are a translation quality evaluator.

    Your task is ONLY to assess whether the translated
    text appears to cover every section of the source text.

    Check for:
    1. Missing paragraphs or sentences.
    2. Missing dialogue or quoted sections.
    3. Source sections that have no corresponding translation.
    4. Untranslated portions.

    Do not claim that the translation is linguistically
    accurate unless that can be established independently.

    Your result assesses coverage only, not translation accuracy.

    Rules:
    1. Do not translate either text.
    2. Do not rewrite or generate Indian-language text.
    3. Identify major missing sections or omissions.
    4. Do not require word-for-word translation.
    5. Return only valid JSON.

    Return this structure:
    {
        "verified": true,
        "coverage": "complete",
        "issues": [],
        "reason": "Brief explanation of coverage only, in English"
    }

    coverage must be one of:
    complete, partial, insufficient.
    """

    user_data = {
        "source_text": source_text,
        "translated_text": translated_text,
    }

    response = client.chat.completions.create(
        model=model,
        messages=[
            {
                "role": "system",
                "content": system_prompt,
            },
            {
                "role": "user",
                "content": json.dumps(
                    user_data,
                    ensure_ascii=False,
                ),
            },
        ],
        response_format={"type": "json_object"},
        temperature=0,
    )

    content = response.choices[0].message.content

    if not content:
        raise RuntimeError(
            "Translation verification returned an empty response."
        )

    try:
        result = json.loads(content)
    except json.JSONDecodeError as exc:
        raise RuntimeError(
            "Translation verification returned invalid JSON."
        ) from exc

    if not isinstance(result, dict):
        raise RuntimeError(
            "Verification result must be a JSON object."
        )

    coverage = result.get("coverage")

    if coverage not in (
        "complete",
        "partial",
        "insufficient",
    ):
        raise RuntimeError(
            "Verification returned an invalid coverage value."
        )

    verified = (
        result.get("verified") is True
        and coverage == "complete"
    )

    return {
        "status": "passed" if verified else "warning",
        "verified": verified,
        "coverage": coverage,
        "issues": result.get("issues", []),
        "reason": result.get("reason", ""),
    }
