from bodhan_eval.speech_checks import evaluate_speech, tokenize


def test_hindi_words_are_tokenized_correctly():
    assert tokenize("बैठक सुबह 10 बजे शुरू होगी।") == [
        "बैठक", "सुबह", "10", "बजे", "शुरू", "होगी"
    ]


def test_number_pronunciation_difference_needs_review():
    result = evaluate_speech(
        "बैठक सुबह 10 बजे शुरू होगी।",
        "बैठक सुबह टेन बजे शुरू होगी",
    )
    assert result["status"] == "review"
    assert result["passed"] is None
    assert result["requires_semantic_review"] is True


def test_repetition_is_a_failure():
    result = evaluate_speech(
        "बैठक सुबह 10 बजे शुरू होगी",
        "बैठक बैठक सुबह 10 बजे शुरू होगी",
    )
    assert result["status"] == "fail"
    assert result["evidence"]["repetitions"] == ["बैठक"]


def test_identical_transcript_passes():
    result = evaluate_speech(
        "बैठक सुबह 10 बजे शुरू होगी",
        "बैठक सुबह 10 बजे शुरू होगी",
    )
    assert result["status"] == "pass"
    assert result["passed"] is True

def test_hindi_spoken_number_equivalence():
    result = evaluate_speech(
        "आज तापमान 25 डिग्री है",
        "आज तापमान ट्वेंटी फाइव डिग्री है",
        language="hi",
    )
    assert result["status"] == "pass"


def test_bengali_spoken_number_equivalence():
    result = evaluate_speech(
        "ট্রেনে যেতে প্রায় 6 ঘণ্টা সময় লাগে",
        "ট্রেনে যেতে প্রায় ছয় ঘণ্টা সময় লাগে",
        language="bn",
    )
    assert result["status"] == "pass"


def test_tamil_suffix_difference_stays_review():
    result = evaluate_speech(
        "தயவுசெய்து உங்கள் அடையாள அட்டையை கொண்டு வாருங்கள்",
        "தயவுசெய்து உங்கள் அடையாள அட்டையைக் கொண்டு வாருங்கள்",
        language="ta",
    )
    assert result["status"] == "review"