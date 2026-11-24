"""Offline tests for the parts Python decides (no API key, no network):

    pip install pytest && pytest test_policy.py
"""

import lesson
import memory
import parent
import tutor

CARD = {
    "topic": "Photosynthesis",
    "concepts": [
        {"id": "c1", "name": "Leaves make food", "explanation": "Plants make food in their leaves."},
        {"id": "c2", "name": "Ingredients", "explanation": "They need sunlight, water and carbon dioxide."},
        {"id": "c3", "name": "Chlorophyll", "explanation": "Chlorophyll traps sunlight."},
    ],
    "misconceptions": [
        {"id": "m1", "concept_id": "c1", "wrong_statement": "Plants eat soil.", "correction": "They make food.",
         "evidence": "make their own food", "difficulty": "easy"},
        {"id": "m2", "concept_id": "c2", "wrong_statement": "Plants only need water.", "correction": "Also light.",
         "evidence": "sunlight, water", "difficulty": "medium"},
    ],
}


def ev(quality, explained=(), plant="none"):
    return {"concepts": [{"id": c, "status": "explained", "quote": "..."} for c in explained],
            "plant_response": plant, "student_errors": [], "resolved_errors": [], "quality": quality}


def test_one_good_answer_is_not_enough_to_level_up():
    s = tutor.Session(card=CARD, band="middle")
    tutor.update(s, ev(2, ["c1"]))
    assert s.level == 1
    tutor.update(s, ev(2, ["c2"]))
    assert s.level == 2


def test_two_weak_answers_drop_the_level():
    s = tutor.Session(card=CARD, band="middle", level=3)
    tutor.update(s, ev(1))
    assert s.level == 3
    tutor.update(s, ev(1))
    assert s.level == 2


def test_catching_a_planted_mistake_levels_up_at_once():
    s = tutor.Session(card=CARD, band="middle")
    s.plants["m1"]["status"], s.pending_plant = "planted", "m1"
    tutor.update(s, ev(2, plant="caught"))
    assert s.plants["m1"]["status"] == "caught" and s.level == 2


def test_mastery_never_goes_down_within_a_session():
    s = tutor.Session(card=CARD, band="middle")
    tutor.update(s, ev(2, ["c1"]))
    tutor.update(s, {"concepts": [{"id": "c1", "status": "partial"}], "quality": 1})
    assert s.concepts["c1"]["status"] == "explained"


def test_student_who_explains_nothing_still_gets_tested():
    s = tutor.Session(card=CARD, band="middle")
    s.turn, s.last_move = 3, "probe_gap"
    assert tutor.choose_move(s)["move"] == "probe_gap"
    s.turn = 5
    assert tutor.choose_move(s) == {"move": "plant", "mid": "m1"}


def test_no_plant_on_a_wrong_idea_the_student_already_holds():
    s = tutor.Session(card=CARD, band="middle")
    s.student_errors.append({"concept_id": "c1", "claim": "Plants eat soil", "correction": "...",
                             "probed": True, "resolved": False})
    s.turn, s.last_move = 5, "probe_error"
    assert tutor.choose_move(s) == {"move": "plant", "mid": "m2"}


def test_report_says_not_tested_when_nothing_was_planted():
    assert tutor.report(tutor.Session(card=CARD, band="middle"))["mistakes_caught"].startswith("not tested")


def test_unanswered_trick_is_not_counted_as_a_miss():
    s = tutor.Session(card=CARD, band="middle")
    s.plants["m1"]["status"], s.plants["m2"]["status"] = "caught", "planted"
    assert tutor.report(s)["mistakes_caught"] == "1/1 on their own, 0 after a hint, 1 not answered yet"


def test_memory_starts_at_last_level_and_targets_weak_spots():
    mem = {"level": 3, "weak_concepts": ["Chlorophyll"], "retest_plants": ["Plants only need water."]}
    s = tutor.Session(card=CARD, band="middle", memory=mem)
    assert s.level == 3
    s.turn = 1
    assert tutor.choose_move(s) == {"move": "probe_gap", "cid": "c3"}
    s.turn, s.last_move = 2, "probe_gap"
    assert tutor.choose_move(s) == {"move": "plant", "mid": "m2"}


def test_remembered_level_is_clamped_to_the_band():
    s = tutor.Session(card=CARD, band="primary", memory={"level": 3})
    assert s.level == 2


def test_memory_round_trip(tmp_path, monkeypatch):
    monkeypatch.setattr(memory, "PROFILES_DIR", tmp_path)
    profile = memory.load("Aarav")
    assert memory.for_session(profile, "Photosynthesis") == {}

    s = tutor.Session(card=CARD, band="middle")
    s.concepts["c1"]["status"] = "explained"
    s.plants["m1"]["status"] = "revealed"
    memory.save(memory.record(profile, s, tutor.report(s)))

    mem = memory.for_session(memory.load("Aarav"), "Photosynthesis")
    assert mem["same_topic"] and mem["level"] == 1
    assert mem["weak_concepts"] == ["Ingredients", "Chlorophyll"]
    assert mem["retest_plants"] == ["Plants eat soil."]


def test_parent_facts_never_turn_a_miss_into_praise():
    s = tutor.Session(card=CARD, band="middle")
    s.concepts["c1"]["status"] = "partial"
    s.plants["m1"]["status"] = "revealed"
    text = parent.facts(tutor.report(s))
    assert "Explained well: nothing yet" in text
    assert "did NOT notice" in text


def test_grounding_rejects_quotes_not_on_the_page():
    page = set(lesson._words("Green plants make their own food using sunlight, water and carbon dioxide."))
    assert lesson.is_grounded("plants make their own food", page)
    assert not lesson.is_grounded("roots absorb nitrogen from fertiliser", page)
