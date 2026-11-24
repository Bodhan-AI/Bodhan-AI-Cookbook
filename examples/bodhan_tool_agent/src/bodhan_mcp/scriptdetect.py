# Unicode blocks for each script, and the languages that use it.
SCRIPTS = {
    "Devanagari": ((0x0900, 0x097F), ["hi", "mr", "ne", "sa", "mai", "doi", "kok", "brx"]),
    "Bengali": ((0x0980, 0x09FF), ["bn", "as", "mni"]),
    "Gurmukhi": ((0x0A00, 0x0A7F), ["pa"]),
    "Gujarati": ((0x0A80, 0x0AFF), ["gu"]),
    "Odia": ((0x0B00, 0x0B7F), ["or"]),
    "Tamil": ((0x0B80, 0x0BFF), ["ta"]),
    "Telugu": ((0x0C00, 0x0C7F), ["te"]),
    "Kannada": ((0x0C80, 0x0CFF), ["kn"]),
    "Malayalam": ((0x0D00, 0x0D7F), ["ml"]),
    "Arabic": ((0x0600, 0x06FF), ["ur", "ks", "sd"]),
    "Ol Chiki": ((0x1C50, 0x1C7F), ["sat"]),
}


def detect(text: str) -> dict:
    counts = {name: 0 for name in SCRIPTS}
    latin = 0
    for ch in text:
        code = ord(ch)
        if ch.isascii() and ch.isalpha():
            latin += 1
            continue
        for name, ((low, high), _) in SCRIPTS.items():
            if low <= code <= high:
                counts[name] += 1
                break
    counts["Latin"] = latin
    total = sum(counts.values())
    if total == 0:
        return {"script": "none", "candidates": [], "share": 0.0}
    top = max(counts, key=counts.get)
    candidates = ["en"] if top == "Latin" else SCRIPTS[top][1]
    return {"script": top, "candidates": candidates, "share": round(counts[top] / total, 2)}