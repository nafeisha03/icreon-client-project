"""Milestone 4: the notes reader turns stated facts into signals and hedged wording into questions."""
from calder_abm.notes import read_text, split_sentences


def types(reading):
    return [t for t, _ in reading.facts]


def test_a_stated_fact_becomes_a_signal_and_keeps_its_sentence():
    reading = read_text("Good call with Mei. Budget is approved for this year. They want an AI controls review.")
    assert types(reading) == ["budget_confirmed", "need_identified"]
    assert reading.facts[0][1] == "Budget is approved for this year."     # the evidence quote
    assert reading.open_questions == []


def test_hedged_wording_is_a_question_not_a_fact():
    reading = read_text("They may have budget next year. David might bring in their general counsel.")
    assert reading.facts == []
    assert reading.open_questions == ["They may have budget next year.",
                                      "David might bring in their general counsel."]


def test_hedged_small_talk_is_ignored():
    # A hedge without a deal topic is neither a fact nor a question worth listing.
    assert read_text("Maybe we will see them at the game.").open_questions == []


def test_negations_block_a_verbal_commitment():
    assert types(read_text("They gave us the go-ahead on Friday.")) == ["verbal_commitment"]
    assert "verbal_commitment" not in types(read_text("They do not intend to proceed this year."))


def test_wording_outside_the_phrase_list_is_missed_on_purpose():
    # The reader is simple and predictable; the evaluation counts what it misses.
    assert read_text("The CFO has earmarked funds for this.").facts == []


def test_sentences_split_on_full_stops_and_semicolons():
    assert split_sentences("One. Two; three!  Four?") == ["One.", "Two;", "three!", "Four?"]
    assert split_sentences("") == []
