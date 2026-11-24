"""Offline tests for the parts Python decides (no API key, no network):

    pip install pytest && pytest test_policy.py
"""

import lesson
import memory
import parent
import quiz
import tutor
import verify

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


def test_dashboard_mastery_is_sticky_and_skills_are_cumulative(tmp_path, monkeypatch):
    monkeypatch.setattr(memory, "PROFILES_DIR", tmp_path)
    profile = memory.load("Aarav")
    s1 = tutor.Session(card=CARD, band="middle")
    s1.concepts["c1"]["status"] = "explained"
    s1.quality = [2, 2]
    s1.plants["m1"]["status"], s1.plants["m2"]["status"] = "caught", "revealed"
    memory.record(profile, s1, tutor.report(s1))

    s2 = tutor.Session(card=CARD, band="middle", memory=memory.for_session(profile, "Photosynthesis"))
    s2.concepts["c2"]["status"] = "explained"  # c1 is not touched this time
    s2.quality = [3]
    memory.record(profile, s2, tutor.report(s2))

    d = memory.skills(profile)
    topic = d["topics"][0]
    assert topic["mastered"] == ["Leaves make food", "Ingredients"]  # c1 stays mastered
    assert topic["to_practice"] == ["Chlorophyll"]
    assert (d["mastered_total"], d["concepts_total"], d["sessions"]) == (2, 3, 2)
    by_name = {s["name"]: s for s in d["skills"]}
    assert by_name["Spotting mistakes"]["value"] == 50  # 1 of 2 tricks over all sessions
    assert by_name["Thinking deeper (why / what if)"]["value"] is None  # never tested
    assert by_name["Explaining in your own words"]["trend"] == 33  # session 2 (100%) vs session 1 (67%)
    assert any("Chlorophyll" in step for step in d["next_steps"])


def test_unanswered_trick_does_not_lower_spotting_skill():
    profile = {"student": "x", "level": None, "topics": {}, "sessions": []}
    s = tutor.Session(card=CARD, band="middle")
    s.plants["m1"]["status"] = "planted"  # session ended before the student answered
    memory.record(profile, s, tutor.report(s))
    spot = next(k for k in memory.skills(profile)["skills"] if k["name"] == "Spotting mistakes")
    assert spot["value"] is None


def test_dashboard_for_a_new_student_is_empty():
    d = memory.skills({"student": "x", "level": None, "topics": {}, "sessions": []})
    assert d["sessions"] == 0 and d["mastered_total"] == 0


def _talked(said):
    s = tutor.Session(card=CARD, band="middle")
    s.history = [{"role": "assistant", "content": "Hi! I missed the class."}, {"role": "user", "content": said}]
    return s


def test_classmate_may_not_use_lesson_words_the_student_never_said():
    s = _talked("plants make food in their leaves")
    assert verify.leaked_terms(s, {"move": "probe_gap"}, "Does the chlorophyll help with that?") == ["chlorophyll"]
    assert verify.leaked_terms(s, {"move": "probe_gap"}, "Ooh, what do the leaves need for it?") == []
    assert verify.leaked_terms(s, {"move": "wrap_up"}, "You taught me about chlorophyll!") == []


def test_planted_mistake_is_allowed_but_must_be_present():
    s = _talked("plants make food in their leaves")
    plant = {"move": "plant", "mid": "m1"}
    said_it = verify.decide(s, plant, "So plants eat soil, right?",
                            [{"claim": "Plants eat soil", "verdict": "false", "evidence": "make their own food"}])
    assert said_it["ok"] and said_it["plant_present"]
    forgot = verify.decide(s, plant, "Wow, that is so cool!", [])
    assert not forgot["ok"] and forgot["plant_present"] is False


def test_any_other_false_claim_blocks_the_line():
    s = _talked("plants make food in their leaves")
    result = verify.decide(s, {"move": "clarify", "cid": "c1"}, "So roots make the food?",
                           [{"claim": "Roots make the food", "verdict": "false", "evidence": "in their leaves"}])
    assert not result["ok"] and "Roots make the food" in result["problems"][0]


def test_a_failed_line_is_regenerated_once_with_the_problems_as_feedback(monkeypatch):
    s = _talked("plants make food in their leaves")
    move = {"move": "probe_gap", "cid": "c3"}
    feedback = []
    monkeypatch.setattr(verify, "fact_check", lambda sess, m, line: verify.decide(sess, m, line, []))
    line, check = verify.checked(s, move, "Is it the chlorophyll?",
                                 lambda fb: feedback.append(fb) or "What makes leaves green?")
    assert line == "What makes leaves green?" and check["regenerated"] and check["ok"]
    assert check["first_leaks"] == ["chlorophyll"] and "chlorophyll" in feedback[0]


def test_a_hint_may_not_give_the_answer_away():
    s = _talked("yes plants eat soil")
    hint = {"move": "hint_plant", "mid": "m1"}  # correction: "They make food."
    told = verify.decide(s, hint, "Hmm, or do they make food themselves?", [])
    assert not told["ok"] and "gives away" in told["problems"][-1]
    assert verify.decide(s, hint, "Hmm, I'm not sure. Where does the plant's dinner come from?", [])["ok"]


def test_the_same_vague_idea_is_clarified_at_most_twice():
    s = tutor.Session(card=CARD, band="middle")
    s.concepts["c1"]["status"] = "partial"
    s.turn, s.last_move = 1, "clarify"
    moves = [tutor.choose_move(s) for _ in range(3)]
    assert [m["move"] for m in moves[:2]] == ["clarify", "clarify"] and moves[2] != {"move": "clarify", "cid": "c1"}


def test_notebook_holds_only_what_the_student_taught():
    s = tutor.Session(card=CARD, band="middle")
    tutor.update(s, {"concepts": [{"id": "c1", "status": "explained", "quote": "leaves make the food"}], "quality": 2})
    s.plants["m1"]["status"], s.pending_plant = "planted", "m1"
    tutor.update(s, ev(2, plant="caught"))
    assert [n["text"] for n in s.learned] == ["leaves make the food", "They make food."]


def test_quiz_options_are_shuffled_without_losing_the_answer():
    q = {"q": "Where do plants make food?", "options": ["leaves", "roots", "soil", "stem"], "answer": 0}
    s = quiz._shuffled(q)
    assert sorted(s["options"]) == sorted(q["options"]) and s["options"][s["answer"]] == "leaves"
    assert quiz.score({"questions": [s]}, [s["answer"]]) == 1 and quiz.score({"questions": [s]}, [None]) == 0


def test_learning_gain_is_post_minus_pre():
    profile = {"student": "x", "level": None, "topics": {}, "sessions": []}
    s = tutor.Session(card=CARD, band="middle")
    memory.record(profile, s, tutor.report(s))
    memory.add_quiz(profile, pre=1, post=3, total=3)
    d = memory.skills(profile)
    assert d["learning_gain"] == 2 and d["quizzes"][0]["post"] == 3


def test_grounding_rejects_quotes_not_on_the_page():
    page = set(lesson._words("Green plants make their own food using sunlight, water and carbon dioxide."))
    assert lesson.is_grounded("plants make their own food", page)
    assert not lesson.is_grounded("roots absorb nitrogen from fertiliser", page)
