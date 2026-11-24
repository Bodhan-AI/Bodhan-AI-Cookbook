import json
from bodhan_eval.llm_judge import LLMJudge

print("=" * 60)
print("INDICEVAL - LIVE LLM JUDGE TEST")
print("=" * 60)

judge = LLMJudge()

source = "नमस्ते, आप कैसे हैं?"
translation = "Hello, how are you?"

print("\nSource:", source)
print("Translation:", translation)
print("\nCalling live LLM API...")

try:
    result = judge.evaluate(
        source=source,
        translation=translation,
        source_language="Hindi",
        target_language="English"
    )

    print("\nLIVE API RESPONSE:")
    print(json.dumps(result, ensure_ascii=False, indent=2))

    print("\n" + "=" * 60)
    print("LIVE LLM TEST PASSED")
    print("=" * 60)

except Exception as exc:
    print("\nLIVE LLM TEST FAILED")
    print(type(exc).__name__ + ":", str(exc))