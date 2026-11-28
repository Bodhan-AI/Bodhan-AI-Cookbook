"""Sentiment and themes on the English pivot text: a lexicon baseline and an optional LLM."""

from __future__ import annotations

import json
import os
import re
from pathlib import Path

import requests
from vaderSentiment.vaderSentiment import SentimentIntensityAnalyzer

POSITIVE_AT = 0.05
NEGATIVE_AT = -0.05
CLAUSE_SPLIT = re.compile(r"(?<=[.!?।])\s+|\s*;\s*|,?\s+(?:but|however|although|though|whereas)\s+", re.IGNORECASE)
LABELS = ("positive", "neutral", "negative")


def load_taxonomy(path: str | Path) -> dict:
    taxonomy = json.loads(Path(path).read_text(encoding="utf-8"))
    for name, theme in taxonomy["themes"].items():
        if not theme.get("keywords") or not theme.get("reply"):
            raise ValueError(f"theme {name!r} needs both 'keywords' and 'reply'")
    return taxonomy


def label_for(score: float) -> str:
    if score >= POSITIVE_AT:
        return "positive"
    if score <= NEGATIVE_AT:
        return "negative"
    return "neutral"


def keyword_pattern(keywords: list[str]) -> re.Pattern[str]:
    parts = [re.escape(keyword[:-1]) + r"\w*" if keyword.endswith("*") else re.escape(keyword) for keyword in keywords]
    return re.compile(r"\b(?:" + "|".join(parts) + r")\b", re.IGNORECASE)


class LexiconAnalyser:
    name = "lexicon"

    def __init__(self, taxonomy: dict):
        words = taxonomy.get("sentiment_words", {})
        self.phrases = {phrase: phrase.replace(" ", "") for phrase in words if " " in phrase}
        self.vader = SentimentIntensityAnalyzer()
        self.vader.lexicon.update({self.phrases.get(word, word): score for word, score in words.items()})
        self.patterns = {name: keyword_pattern(theme["keywords"]) for name, theme in taxonomy["themes"].items()}

    def score(self, text: str) -> float:
        lowered = text.lower()
        for phrase, joined in self.phrases.items():
            lowered = lowered.replace(phrase, joined)
        return self.vader.polarity_scores(lowered)["compound"]

    def themes_in(self, text: str) -> list[str]:
        return [name for name, pattern in self.patterns.items() if pattern.search(text)]

    def analyse(self, english: str) -> dict:
        overall = self.score(english)
        by_theme: dict[str, list[float]] = {}
        for clause in (part for part in CLAUSE_SPLIT.split(english) if part and part.strip()):
            for theme in self.themes_in(clause):
                by_theme.setdefault(theme, []).append(self.score(clause))
        aspects = []
        for theme, scores in by_theme.items():
            mean = round(sum(scores) / len(scores), 3)
            aspects.append({"theme": theme, "score": mean, "sentiment": label_for(mean)})
        return {"sentiment": label_for(overall), "score": round(overall, 3), "aspects": aspects, "analyser": self.name}


LLM_PROMPT = """You label one piece of customer feedback. Reply with JSON only, no prose:
{{"sentiment": "positive|neutral|negative", "score": <number from -1 to 1>, "aspects": [{{"theme": "<one of: {themes}>", "sentiment": "positive|neutral|negative"}}]}}
List an aspect only when the feedback talks about that theme. Feedback:
{text}"""


class LLMAnalyser:
    name = "llm"

    def __init__(self, taxonomy: dict, fallback: LexiconAnalyser, post=requests.post):
        self.themes = list(taxonomy["themes"])
        self.fallback = fallback
        self.post = post
        self.base_url = os.environ.get("LLM_BASE_URL", "").rstrip("/")
        self.api_key = os.environ.get("LLM_API_KEY", "")
        self.model = os.environ.get("LLM_MODEL", "")
        if not (self.base_url and self.api_key and self.model):
            raise ValueError("--analyser llm needs LLM_BASE_URL, LLM_API_KEY and LLM_MODEL in the environment")

    def analyse(self, english: str) -> dict:
        try:
            return self._ask(english)
        except (requests.RequestException, ValueError, KeyError, IndexError, TypeError):
            result = self.fallback.analyse(english)
            result["analyser"] = "lexicon (llm reply unusable)"
            return result

    def _ask(self, english: str) -> dict:
        prompt = LLM_PROMPT.format(themes=", ".join(self.themes), text=english)
        response = self.post(
            f"{self.base_url}/chat/completions",
            headers={"Authorization": f"Bearer {self.api_key}"},
            json={"model": self.model, "messages": [{"role": "user", "content": prompt}], "temperature": 0},
            timeout=60,
        )
        response.raise_for_status()
        content = response.json()["choices"][0]["message"]["content"]
        data = json.loads(content[content.index("{"): content.rindex("}") + 1])
        sentiment = data["sentiment"]
        if sentiment not in LABELS:
            raise ValueError(f"unknown sentiment {sentiment!r}")
        score = max(-1.0, min(1.0, float(data.get("score", 0))))
        aspects = []
        for aspect in data.get("aspects") or []:
            if aspect.get("theme") in self.themes and aspect.get("sentiment") in LABELS:
                aspects.append({"theme": aspect["theme"], "sentiment": aspect["sentiment"], "score": None})
        return {"sentiment": sentiment, "score": round(score, 3), "aspects": aspects, "analyser": self.name}


def needs_reply(analysis: dict) -> bool:
    return analysis["sentiment"] == "negative" or any(aspect["sentiment"] == "negative" for aspect in analysis["aspects"])


def worst_theme(analysis: dict) -> str | None:
    negative = [aspect for aspect in analysis["aspects"] if aspect["sentiment"] == "negative"]
    if not negative:
        return None
    return min(negative, key=lambda aspect: aspect["score"] if aspect["score"] is not None else 0)["theme"]
