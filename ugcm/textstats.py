"""Text measurements used by the gate: words, syllables, sentences, Flesch-Kincaid grade."""

from __future__ import annotations

import re

_WORD = re.compile(r"[A-Za-z0-9]+(?:['’][A-Za-z]+)?")
_SENT_SPLIT = re.compile(r"(?<=[.!?])\s+")


def words(text: str) -> list[str]:
    return _WORD.findall(text)


def word_count(text: str) -> int:
    return len(words(text))


def sentences(text: str) -> list[str]:
    parts = [s.strip() for s in _SENT_SPLIT.split(text.strip()) if s.strip()]
    return parts or ([text.strip()] if text.strip() else [])


def syllables(word: str) -> int:
    """Heuristic English syllable count. Good enough for a readability ceiling, tested on samples."""
    w = word.lower().strip("'’")
    if not w:
        return 0
    if w.isdigit():
        # Numbers are read aloud; "24" is "twen-ty four". Approximate two syllables per digit group.
        return max(1, len(w))
    if len(w) <= 3:
        return 1
    w = re.sub(r"(?:[^laeiouy]es|ed|[^laeiouy]e)$", "", w)
    w = re.sub(r"^y", "", w)
    groups = re.findall(r"[aeiouy]{1,2}", w)
    return max(1, len(groups))


def fk_grade(text: str) -> float:
    """Flesch-Kincaid grade level. Empty text scores 0."""
    ws = words(text)
    if not ws:
        return 0.0
    n_sent = max(1, len(sentences(text)))
    n_syl = sum(syllables(w) for w in ws)
    return round(0.39 * (len(ws) / n_sent) + 11.8 * (n_syl / len(ws)) - 15.59, 2)


def speak_seconds(text: str, words_per_second: float) -> float:
    return round(word_count(text) / words_per_second, 2)
