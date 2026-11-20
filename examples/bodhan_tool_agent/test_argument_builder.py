
from bodhan_agent.argument_builder import prepare_tool_arguments


previous_results = [
    {
        "tool": "indic_ocr",
        "result": {
            "text": "নমস্কার, আমার নাম জয়দীপ।"
        }
    }
]

arguments = prepare_tool_arguments(
    task="Extract the Bengali text from the image and translate it into English.",
    step={
        "description": "Translate the extracted Bengali text into English.",
        "tool": "indic_translate"
    },
    files=["sample_data/test_document.png"],
    previous_results=previous_results,
    tool_schema={
        "type": "object",
        "properties": {
            "text": {"type": "string"},
            "target_language": {"type": "string"},
            "script": {"type": "string"}
        },
        "required": ["text", "target_language"]
    }
)

print("\nPrepared translation arguments:")
print(arguments)
