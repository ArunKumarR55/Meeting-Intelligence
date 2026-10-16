"""Conservative transcript-based mapping from diarization IDs to speaker names."""

from __future__ import annotations

import re
from collections import Counter, defaultdict
from collections.abc import Mapping, Sequence
from typing import Any


_NAME = r"[A-Z][A-Za-z'’\-]{1,39}(?:\s+[A-Z][A-Za-z'’\-]{1,39}){0,2}"
_SELF_INTRODUCTION = re.compile(
    rf"\b(?:my name is|i am|i'm|this is|i go by)\s+(?P<name>{_NAME})",
    re.IGNORECASE,
)
_DIRECT_ADDRESS = re.compile(
    rf"^\s*(?:(?:hey|hi|hello|okay|ok|so|well|and)[, ]+)?(?P<name>[A-Z][A-Za-z'’\-]{{1,39}})(?=\s*[,?:])",
    re.IGNORECASE,
)
_QUESTION_OR_REQUEST = re.compile(
    r"\?|\b(?:can|could|would|will|do|did|are|is)\s+you\b|\bwhat\s+do\s+you\s+think\b|\bhow\s+about\s+you\b|\bplease\b|\btell\s+(?:me|us)\b|\bshare\s+(?:your|the)\b",
    re.IGNORECASE,
)
_NON_NAME_SINGLE_WORDS = {
    "and",
    "available",
    "absolutely",
    "actually",
    "awesome",
    "but",
    "excited",
    "fine",
    "glad",
    "good",
    "great",
    "happy",
    "here",
    "yeah",
    "yep",
    "yes",
    "no",
    "okay",
    "perfect",
    "ready",
    "right",
    "sure",
    "sorry",
    "thanks",
    "welcome",
}


def _field(segment: Mapping[str, Any] | object, key: str) -> Any:
    if isinstance(segment, Mapping):
        return segment.get(key)
    return getattr(segment, key, None)


def _clean_name(candidate: str) -> str | None:
    name = " ".join(candidate.strip(" \t\r\n,.;:!?()[]{}\"'“”‘’").split())
    words = name.split()
    if not words or len(words) > 3:
        return None
    if any(not word[0].isupper() for word in words):
        return None
    if len(words) == 1 and words[0].casefold() in _NON_NAME_SINGLE_WORDS:
        return None
    return name


def infer_speaker_identities(
    transcript_segments: Sequence[Mapping[str, Any] | object],
) -> dict[str, dict[str, object]]:
    """Infer names only from explicit introductions or clear call-and-response turns.

    The diarization speaker IDs remain the stable identifiers. A single direct
    question or request addressed by name and answered promptly by another
    speaker is considered supporting evidence; unsupported mentions are ignored.
    """
    ordered_segments = sorted(
        transcript_segments,
        key=lambda segment: float(_field(segment, "start_seconds") or 0.0),
    )
    introductions: dict[str, list[tuple[str, float | None]]] = defaultdict(list)
    addressed_replies: dict[str, list[str]] = defaultdict(list)

    for index, segment in enumerate(ordered_segments):
        speaker_id = _field(segment, "speaker_id")
        text = _field(segment, "text")
        if not isinstance(speaker_id, str) or not speaker_id.strip() or not isinstance(text, str):
            continue
        start_seconds = _field(segment, "start_seconds")
        evidence_time = float(start_seconds) if isinstance(start_seconds, (int, float)) else None

        for match in _SELF_INTRODUCTION.finditer(text):
            name = _clean_name(match.group("name"))
            if name:
                introductions[speaker_id].append((name, evidence_time))

        address = _DIRECT_ADDRESS.match(text)
        if not address:
            continue
        name = _clean_name(address.group("name"))
        remainder = text[address.end() :]
        if not name or not _QUESTION_OR_REQUEST.search(remainder):
            continue

        end_seconds = _field(segment, "end_seconds")
        end_time = float(end_seconds) if isinstance(end_seconds, (int, float)) else evidence_time
        for following in ordered_segments[index + 1 :]:
            response_speaker_id = _field(following, "speaker_id")
            response_start = _field(following, "start_seconds")
            response_start = float(response_start) if isinstance(response_start, (int, float)) else None
            if response_start is not None and end_time is not None and response_start - end_time > 12.0:
                break
            if response_speaker_id == speaker_id:
                continue
            if isinstance(response_speaker_id, str) and response_speaker_id.strip():
                addressed_replies[response_speaker_id].append(name)
            break

    identities: dict[str, dict[str, object]] = {}
    for speaker_id in sorted(set(introductions) | set(addressed_replies)):
        explicit_names = introductions.get(speaker_id, [])
        if explicit_names:
            explicit_counts = Counter(name.casefold() for name, _time in explicit_names)
            winning_key, winning_count = explicit_counts.most_common(1)[0]
            tied = sum(1 for count in explicit_counts.values() if count == winning_count) > 1
            if tied:
                continue
            winning_name = next(
                name for name, _time in explicit_names if name.casefold() == winning_key
            )
            identities[speaker_id] = {
                "name": winning_name,
                "confidence": 0.99,
                "source": "self_introduction",
                "confirmed": False,
            }
            continue

        replies = addressed_replies.get(speaker_id, [])
        if not replies:
            continue
        counts = Counter(name.casefold() for name in replies)
        ranked = counts.most_common()
        if len(ranked) > 1 and ranked[0][1] == ranked[1][1]:
            continue
        winning_key, winning_count = ranked[0]
        winning_name = next(name for name in replies if name.casefold() == winning_key)
        confidence = min(0.94, 0.84 + 0.05 * (winning_count - 1))
        if len(ranked) > 1:
            confidence = min(confidence, 0.82 + 0.03 * (ranked[0][1] - ranked[1][1]))
        if confidence < 0.80:
            continue
        identities[speaker_id] = {
            "name": winning_name,
            "confidence": round(confidence, 2),
            "source": "direct_address_reply",
            "confirmed": False,
        }

    return identities
