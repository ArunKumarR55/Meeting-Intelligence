"""Approximate transcript-based speaking and filler-word metrics."""

import re
from typing import TypedDict


WORD_PATTERN = re.compile(r"\b[\w]+(?:['’][\w]+)?\b", re.UNICODE)
FILLER_PATTERN = re.compile(r"\b(um|uh|like)\b", re.IGNORECASE)
ASSUMED_WORDS_PER_MINUTE = 150.0
MINIMUM_ESTIMATED_DURATION_SECONDS = 60.0


class ParticipationMetrics(TypedDict):
    word_count: int
    estimated_duration_seconds: float
    estimated_duration_minutes: float
    words_per_minute: float
    filler_word_count: int
    filler_words: dict[str, int]
    duration_estimation_method: str


def calculate_participation_metrics(transcript_text: str) -> ParticipationMetrics:
    """Estimate speaking rate and count common fillers in a transcript.

    This MVP has no parsed speaker timestamps, so duration is approximated at
    150 words per minute with a one-minute minimum. WPM is therefore a proxy,
    not a measured acoustic speaking rate. The word ``like`` is counted wherever
    it appears, including non-filler uses.
    """
    if not isinstance(transcript_text, str):
        raise TypeError("transcript_text must be a string")

    words = WORD_PATTERN.findall(transcript_text)
    word_count = len(words)
    if word_count == 0:
        estimated_duration_seconds = 0.0
        words_per_minute = 0.0
    else:
        estimated_duration_seconds = max(
            MINIMUM_ESTIMATED_DURATION_SECONDS,
            word_count / ASSUMED_WORDS_PER_MINUTE * 60.0,
        )
        words_per_minute = word_count / (estimated_duration_seconds / 60.0)

    filler_matches = [match.casefold() for match in FILLER_PATTERN.findall(transcript_text)]
    filler_counts = {
        "um": filler_matches.count("um"),
        "uh": filler_matches.count("uh"),
        "like": filler_matches.count("like"),
    }

    return {
        "word_count": word_count,
        "estimated_duration_seconds": round(estimated_duration_seconds, 2),
        "estimated_duration_minutes": round(estimated_duration_seconds / 60.0, 2),
        "words_per_minute": round(words_per_minute, 2),
        "filler_word_count": len(filler_matches),
        "filler_words": filler_counts,
        "duration_estimation_method": (
            "150_words_per_minute_with_60_second_minimum"
        ),
    }
