"""Finds stated facts in free-text notes, using the phrases in config/note_phrases.yaml.

Partners' notes, call notes and email replies often hold the facts that decide a
stage: a stated need, a verbal yes, legal joining, a budget cut. The reader:

  * splits a note into sentences;
  * turns a sentence into a signal when it contains a listed phrase, and keeps
    the sentence itself as evidence, so anyone can check the reading;
  * never turns hedged wording ("they may have budget next year") into a
    signal: such sentences become open questions instead.

This is deliberately simple and predictable. Facts written in wording the
phrase list does not know are missed, and the evaluation counts how many.
A later phase can add an AI reader; it would have to quote its sentence too.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from . import config


@dataclass
class Reading:
    facts: list[tuple[str, str]]          # (signal type, sentence)
    open_questions: list[str]             # hedged sentences about deal topics


def split_sentences(text: str) -> list[str]:
    text = re.sub(r"\s+", " ", (text or "").replace("’", "'")).strip()
    if not text:
        return []
    parts = re.split(r"(?<=[.!?;])\s+", text)
    return [p.strip() for p in parts if p.strip()]


def _contains(sentence: str, phrase: str) -> bool:
    return re.search(rf"(?<![a-z]){re.escape(phrase.lower())}(?![a-z])", sentence) is not None


def read_text(text: str | None) -> Reading:
    phrases = config.note_phrases()
    hedges = phrases.get("hedges", [])
    topics = phrases.get("topic_words", [])
    facts: list[tuple[str, str]] = []
    questions: list[str] = []
    found_types: set[str] = set()
    for sentence in split_sentences(text or ""):
        lower = sentence.lower()
        hedged = any(_contains(lower, h) for h in hedges)
        matched = []
        for signal_type, spec in phrases["signals"].items():
            if any(_contains(lower, p) for p in spec.get("phrases", [])):
                if any(_contains(lower, n) for n in spec.get("negations", [])):
                    continue
                matched.append(signal_type)
        if hedged:
            if matched or any(_contains(lower, t) for t in topics):
                questions.append(sentence)
            continue
        for signal_type in matched:
            if signal_type not in found_types:
                found_types.add(signal_type)
                facts.append((signal_type, sentence.rstrip(";").rstrip()))
    return Reading(facts, questions)
