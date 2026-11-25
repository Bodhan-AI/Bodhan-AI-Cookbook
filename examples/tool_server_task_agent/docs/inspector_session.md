# MCP Inspector session (official off-the-shelf client)

### tools/list (names only shown in README; full schemas returned)
```
$ inspector --cli python -m bodhan_mcp --method tools/list
{
  "tools": [
    {
      "name": "inspect_media",
      "description": "Inspect a file (or every file in a directory) WITHOUT using any model: real format sniffed from bytes, kind (audio/video/image/pdf/text), duration, page count, image size, whether a video has an audio track, and for text files the script-based language. Free and instant. Call this first on every input to choose the right tool and to catch missing, empty, mislabelled or unsupported files (e.g. a .jpg that is really a PDF). Pass a directory path to list up to 100 files.",
      "inputSchema": {
        "type": "object",
        "properties": {
          "path": {
            "description": "Path of an existing file on the machine running this server.",
            "title": "Path",
            "type": "string"
          }
        },
        "required": [
          "path"
        ],
        "title": "inspect_mediaArguments"
      },
      "outputSchema": {
        "type": "object",
        "properties": {
          "ok": {
            "default": true,
            "description": "False when the call failed; then `error` explains why and every other field is empty.",
            "title": "Ok",
            "type": "boolean"
          },
          "error": {
            "anyOf": [
              {
                "$ref": "#/$defs/ErrorInfo"
              },
              {
                "type": "null"
              }
            ],
            "default": null,
            "description": "Present only when ok is false."
          },
          "cost_inr": {
            "default": 0,
            "description": "Rupees spent on Bodhan models by this call (0 for cache hits and local tools).",
            "title": "Cost Inr",
            "type": "number"
          },
          "latency_s": {
            "defau
```

### Free tool: detect_script_language
```
$ inspector --cli python -m bodhan_mcp --method tools/call --tool-name detect_script_language --tool-arg text_or_path=வணக்கம் இன்று வகுப்பு
{
  "content": [
    {
      "type": "text",
      "text": "{\n  \"ok\": true,\n  \"error\": null,\n  \"cost_inr\": 0.0,\n  \"latency_s\": 0.004,\n  \"cache_hits\": 0,\n  \"warnings\": [],\n  \"script\": \"Tamil\",\n  \"language\": \"ta\",\n  \"candidates\": [\n    \"ta\"\n  ],\n  \"confidence\": \"high\",\n  \"script_counts\": {\n    \"Tamil\": 19\n  },\n  \"note\": \"Detected from Unicode script only (no model). \"\n}"
    }
  ],
  "structuredContent": {
    "ok": true,
    "error": null,
    "cost_inr": 0,
    "latency_s": 0.004,
    "cache_hits": 0,
    "warnings": [],
    "script": "Tamil",
    "language": "ta",
    "candidates": [
      "ta"
    ],
    "confidence": "high",
    "script_counts": {
      "Tamil": 19
    },
    "note": "Detected from Unicode script only (no model). "
  },
  "isError": false
}
```

### Free tool: list_voices
```
$ inspector --cli python -m bodhan_mcp --method tools/call --tool-name list_voices --tool-arg language=bn --tool-arg gender=female
{
  "content": [
    {
      "type": "text",
      "text": "{\n  \"ok\": true,\n  \"error\": null,\n  \"cost_inr\": 0.0,\n  \"latency_s\": 0.001,\n  \"cache_hits\": 0,\n  \"warnings\": [],\n  \"language\": \"bn\",\n  \"language_name\": \"Bengali\",\n  \"voices\": [\n    {\n      \"name\": \"Ishita\",\n      \"gender\": \"female\"\n    }\n  ],\n  \"styles\": [\n    \"AIR style news\",\n    \"Customer Care\",\n    \"TV style news\",\n    \"advertisements\",\n    \"anger\",\n    \"children's stories\",\n    \"disgust\",\n    \"educational lecture\",\n    \"fear\",\n    \"happy\",\n    \"news\",\n    \"sad\",\n    \"single person narration audiobook\",\n    \"surprise\"\n  ],\n  \"speak_languages\": [\n    \"as\",\n    \"bn\",\n    \"brx\",\n    \"doi\",\n    \"en\",\n    \"gu\",\n    \"hi\",\n    \"kn\",\n    \"kok\",\n    \"ks\",\n    \"mai\",\n    \"ml\",\n    \"mni\",\n    \"mr\",\n    \"ne\",\n    \"or\",\n    \"pa\",\n    \"sa\",\n    \"sat\",\n    \"sd\",\n    \"ta\",\n    \"te\",\n    \"ur\"\n  ]\n}"
    }
  ],
  "structuredContent": {
    "ok": true,
    "error": null,
    "cost_inr": 0,
    "latency_s": 0.001,
    "cache_hits": 0,
    "warnings": [],
    "language": "bn",
    "language_name": "Bengali",
    "voices": [
      {
        "name": "Ishita",
        "gender": "female"
      }
    ],
    "styles": [
      "AIR style news",
      "Customer Care",
      "TV style news",
      "advertisements",
      "anger",
      "children's stories",
      "disgust",
      "educational lecture",
      "fear",
      "happy",
      "news",
      "sad",
      "single person narration audiobook",
      "surprise"
    ],
    "speak_languages": [
      "as",
      "bn",
      "brx",
      "doi",
      "en",
      "gu",
      "hi",
      "kn",
      "kok",
      "ks",
      "ma
```

### Resource: voice library
```
$ inspector --cli python -m bodhan_mcp --method resources/read --uri bodhan://voices
{
  "contents": [
    {
      "uri": "bodhan://voices",
      "mimeType": "application/json",
      "text": "{\"voices\": {\"as\": {\"female\": [\"Prastuti\"], \"male\": [\"Ankur\"]}, \"bn\": {\"female\": [\"Ishita\"], \"male\": [\"Sourav\"]}, \"brx\": {\"female\": [\"Gwrbw\"], \"male\": [\"Sansuma\"]}, \"doi\": {\"female\": [\"Preeti\"], \"male\": [\"Sham\"]}, \"gu\": {\"female\": [\"Dhara\"], \"male\": [\"Parth\"]}, \"hi\": {\"female\": [\"Kavya\", \"Suhani\"], \"male\": [\"Amit\"]}, \"kn\": {\"female\": [\"Deepika\"], \"male\": [\"Adarsh\"]}, \"kok\": {\"female\": [\"Anjali\"], \"male\": [\"Sandeep\"]}, \"ks\": {\"female\": [\"Zoon\"], \"male\": [\"Ishfaq\"]}, \"mai\": {\"female\": [\"Vaidehi\"], \"male\": [\"Madhukar\"]}, \"ml\": {\"female\": [\"Lakshmi\"], \"male\": [\"Kiran\"]}, \"mni\": {\"female\": [\"Thoibi\"], \"male\": [\"Chaoba\"]}, \"mr\": {\"female\": [\"Anagha\"], \"male\": [\"Chinmay\"]}, \"ne\": {\"female\": [\"Srijana\"], \"male\": [\"Sagar\"]}, \"or\": {\"female\": [\"Itishree\"], \"male\": [\"Akash\"]}, \"pa\": {\"female\": [\"Kaur\"], \"male\": [\"Manpreet\"]}, \"sa\": {\"female\": [\"Bharati\"], \"male\": [\"Aryaman\"]}, \"sat\": {\"female\": [\"Phulmani\"], \"male\": [\"Sibu\"]}, \"sd\": {\"female\": [\"Moomal\"], \"male\": [\"Rano\"]}, \"ta\": {\"female\": [\"Anitha\"], \"male\": [\"Arun\"]}, \"te\": {\"female\": [\"Sravani\"], \"male\": [\"Vamsi\"]}, \"ur\": {\"female\": [\"Saba\"], \"male\": [\"Zaid\"]}}, \"styles\": [\"AIR style news\", \"Customer Care\", \"TV style news\", \"advertisements\", \"anger\", \"children's stories\", \"disgust\", \"educational lecture\", \"fear\", \"happy\", \"news\", \"sad\", \"single person narration audiobook\", \"surprise\"], \"languages\": {\"as\": \"Assamese\", \"bhb\": \"Bhili\", \"bho\": \"Bhojpuri\", \"bn\"
```

### BAD: unsupported language (French)
```
$ inspector --cli python -m bodhan_mcp --method tools/call --tool-name translate_text --tool-arg text_or_path=hello --tool-arg target_lang=fr
{
  "content": [
    {
      "type": "text",
      "text": "{\"ok\": false, \"error\": {\"error_code\": \"UNSUPPORTED_LANGUAGE\", \"message\": \"target_lang 'fr' is not supported by indic-translate.\", \"retryable\": false, \"hint\": \"Supported codes: as, bn, brx, doi, en, gu, hi, kn, kok, ks, ks-Deva, mai, ml, mni, mni-Beng, mr, ne, or, pa, sa, sat, sd, sd-Arab, ta, te, ur. Languages such as French, Spanish or Chinese are not supported by any Bodhan model; do not attempt a substitute - report the limitation instead.\"}, \"cost_inr\": 0.0, \"latency_s\": 0.001, \"warnings\": []}"
    }
  ],
  "structuredContent": {
    "ok": false,
    "error": {
      "error_code": "UNSUPPORTED_LANGUAGE",
      "message": "target_lang 'fr' is not supported by indic-translate.",
      "retryable": false,
      "hint": "Supported codes: as, bn, brx, doi, en, gu, hi, kn, kok, ks, ks-Deva, mai, ml, mni, mni-Beng, mr, ne, or, pa, sa, sat, sd, sd-Arab, ta, te, ur. Languages such as French, Spanish or Chinese are not supported by any Bodhan model; do not attempt a substitute - report the limitation instead."
    },
    "cost_inr": 0,
    "latency_s": 0.001,
    "warnings": []
  },
  "isError": true
}
```

### BAD: missing file
```
$ inspector --cli python -m bodhan_mcp --method tools/call --tool-name transcribe_audio --tool-arg audio_path=/no/such/file.wav
{
  "content": [
    {
      "type": "text",
      "text": "{\"ok\": false, \"error\": {\"error_code\": \"FILE_NOT_FOUND\", \"message\": \"No such file: C:/Program Files/Git/no/such/file.wav\", \"retryable\": false, \"hint\": \"Check the path; use inspect_media on the input directory to list files.\"}, \"cost_inr\": 0.0, \"latency_s\": 0.001, \"warnings\": []}"
    }
  ],
  "structuredContent": {
    "ok": false,
    "error": {
      "error_code": "FILE_NOT_FOUND",
      "message": "No such file: C:/Program Files/Git/no/such/file.wav",
      "retryable": false,
      "hint": "Check the path; use inspect_media on the input directory to list files."
    },
    "cost_inr": 0,
    "latency_s": 0.001,
    "warnings": []
  },
  "isError": true
}
```

### BAD: missing required argument
```
$ inspector --cli python -m bodhan_mcp --method tools/call --tool-name translate_text --tool-arg text_or_path=hello
{
  "content": [
    {
      "type": "text",
      "text": "{\"ok\": false, \"error\": {\"error_code\": \"INVALID_ARGUMENT\", \"message\": \"Invalid arguments for translate_text: target_lang.\", \"retryable\": false, \"hint\": \"Check argument names and types against the tool's input schema.\"}, \"cost_inr\": 0.0}"
    }
  ],
  "structuredContent": {
    "ok": false,
    "error": {
      "error_code": "INVALID_ARGUMENT",
      "message": "Invalid arguments for translate_text: target_lang.",
      "retryable": false,
      "hint": "Check argument names and types against the tool's input schema."
    },
    "cost_inr": 0
  },
  "isError": true
}
```

### BAD: wrong script for the language
```
$ inspector --cli python -m bodhan_mcp --method tools/call --tool-name synthesize_speech --tool-arg text_or_path=hello there --tool-arg language=ta
{
  "content": [
    {
      "type": "text",
      "text": "{\"ok\": false, \"error\": {\"error_code\": \"TEXT_LANGUAGE_MISMATCH\", \"message\": \"language=ta expects Tamil script but the text is mostly Latin.\", \"retryable\": false, \"hint\": \"Translate the text to the target language first (translate_text with script='native'), then pass its output_path here.\"}, \"cost_inr\": 0.0, \"latency_s\": 0.002, \"warnings\": []}"
    }
  ],
  "structuredContent": {
    "ok": false,
    "error": {
      "error_code": "TEXT_LANGUAGE_MISMATCH",
      "message": "language=ta expects Tamil script but the text is mostly Latin.",
      "retryable": false,
      "hint": "Translate the text to the target language first (translate_text with script='native'), then pass its output_path here."
    },
    "cost_inr": 0,
    "latency_s": 0.002,
    "warnings": []
  },
  "isError": true
}
```

### BAD: unknown tool
```
$ inspector --cli python -m bodhan_mcp --method tools/call --tool-name does_not_exist
{
  "content": [
    {
      "type": "text",
      "text": "{\"ok\": false, \"error\": {\"error_code\": \"UNKNOWN_TOOL\", \"message\": \"No tool named 'does_not_exist'.\", \"retryable\": false, \"hint\": \"Call tools/list to see available tools.\"}, \"cost_inr\": 0.0}"
    }
  ],
  "structuredContent": {
    "ok": false,
    "error": {
      "error_code": "UNKNOWN_TOOL",
      "message": "No tool named 'does_not_exist'.",
      "retryable": false,
      "hint": "Call tools/list to see available tools."
    },
    "cost_inr": 0
  },
  "isError": true
}
```
