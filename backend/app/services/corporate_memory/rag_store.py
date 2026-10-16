"""Local persistent ChromaDB storage and retrieval for meeting transcripts."""

import hashlib
from pathlib import Path
from threading import Lock
from typing import Any, Mapping, Sequence

import chromadb

from app.core.config import PROJECT_ROOT
from app.services.audio_ingestion.pii_redactor import redact_pii


CHROMA_DIRECTORY = PROJECT_ROOT / "data" / "chroma_db"
CHUNK_SIZE_CHARACTERS = 1_000
CHUNK_OVERLAP_CHARACTERS = 150
UPSERT_BATCH_SIZE = 500
COLLECTION_NAME = "amip_memory"

_COLLECTION_LOCK = Lock()
_CHROMA_CLIENT = None
_COLLECTION = None


def _get_collection():
    """Create the local Chroma store only when a memory operation is requested."""
    global _CHROMA_CLIENT, _COLLECTION
    with _COLLECTION_LOCK:
        if _COLLECTION is None:
            CHROMA_DIRECTORY.mkdir(parents=True, exist_ok=True)
            _CHROMA_CLIENT = chromadb.PersistentClient(path=str(CHROMA_DIRECTORY))
            _COLLECTION = _CHROMA_CLIENT.get_or_create_collection(
                name=COLLECTION_NAME,
                metadata={"hnsw:space": "cosine"},
            )
        return _COLLECTION


def _chunk_transcript(
    transcript: str,
    chunk_size: int = CHUNK_SIZE_CHARACTERS,
    overlap: int = CHUNK_OVERLAP_CHARACTERS,
) -> list[tuple[str, int, int]]:
    """Split text into overlapping chunks without cutting ordinary words."""
    if chunk_size <= 0:
        raise ValueError("chunk_size must be greater than zero")
    if overlap < 0 or overlap >= chunk_size:
        raise ValueError("overlap must be nonnegative and smaller than chunk_size")

    chunks: list[tuple[str, int, int]] = []
    start = 0
    text_length = len(transcript)
    while start < text_length:
        end = min(start + chunk_size, text_length)
        if end < text_length:
            boundary = transcript.rfind(" ", start, end)
            if boundary > start + chunk_size // 2:
                end = boundary

        chunk = transcript[start:end].strip()
        if chunk:
            chunks.append((chunk, start, end))
        if end >= text_length:
            break

        next_start = max(start + 1, end - overlap)
        while next_start < end and not transcript[next_start].isspace():
            next_start += 1
        if next_start >= end:
            next_start = end
        start = next_start

    return chunks


def _validate_meeting_id(meeting_id: int) -> None:
    if isinstance(meeting_id, bool) or not isinstance(meeting_id, int) or meeting_id <= 0:
        raise ValueError("meeting_id must be a positive integer")


def _segment_character_ranges(
    transcript_segments: Sequence[Mapping[str, Any]] | None,
) -> list[tuple[int, int, float, float, str | None]]:
    """Map joined transcript character positions back to ASR time ranges."""
    ranges: list[tuple[int, int, float, float, str | None]] = []
    cursor = 0
    for segment in transcript_segments or ():
        text = segment.get("text")
        if not isinstance(text, str) or not text:
            continue
        start_seconds = segment.get("start_seconds")
        end_seconds = segment.get("end_seconds")
        if not isinstance(start_seconds, (int, float)) or not isinstance(end_seconds, (int, float)):
            cursor += len(text) + 1
            continue
        speaker_id = segment.get("speaker_id")
        ranges.append(
            (
                cursor,
                cursor + len(text),
                float(start_seconds),
                float(end_seconds),
                speaker_id if isinstance(speaker_id, str) else None,
            )
        )
        cursor += len(text) + 1
    return ranges


def index_meeting_transcript(
    meeting_id: int,
    transcript: str,
    transcript_segments: Sequence[Mapping[str, Any]] | None = None,
) -> list[str]:
    """PII-redact, chunk, embed, and persist one meeting transcript.

    Re-indexing a meeting upserts deterministic chunk IDs and removes stale chunks
    after the new set has been stored, so interrupted writes do not erase the old
    index before replacement content is available.
    """
    _validate_meeting_id(meeting_id)
    if not isinstance(transcript, str):
        raise TypeError("transcript must be a string")

    safe_transcript = redact_pii(transcript).strip()
    if not safe_transcript:
        raise ValueError("transcript must contain non-whitespace content")

    chunks = _chunk_transcript(safe_transcript)
    segment_ranges = _segment_character_ranges(transcript_segments)
    chunk_ids = [
        f"meeting-{meeting_id}-chunk-{index}-"
        f"{hashlib.sha256(chunk.encode('utf-8')).hexdigest()[:16]}"
        for index, (chunk, _, _) in enumerate(chunks)
    ]
    collection = _get_collection()
    existing = collection.get(where={"meeting_id": meeting_id})
    previous_ids = set(existing.get("ids", []))

    for offset in range(0, len(chunks), UPSERT_BATCH_SIZE):
        batch_chunks = chunks[offset : offset + UPSERT_BATCH_SIZE]
        batch_ids = chunk_ids[offset : offset + UPSERT_BATCH_SIZE]
        metadatas: list[dict[str, int | float | str]] = []
        for chunk_index, (_, start, end) in enumerate(
            batch_chunks, start=offset
        ):
            metadata: dict[str, int | float | str] = {
                "meeting_id": meeting_id,
                "chunk_index": chunk_index,
                "character_start": start,
                "character_end": end,
            }
            covered_segments = [
                segment
                for segment in segment_ranges
                if min(end, segment[1]) > max(start, segment[0])
            ]
            if covered_segments:
                metadata["start_seconds"] = min(segment[2] for segment in covered_segments)
                metadata["end_seconds"] = max(segment[3] for segment in covered_segments)
                speaker_ids = list(
                    dict.fromkeys(
                        segment[4] for segment in covered_segments if segment[4] is not None
                    )
                )
                if speaker_ids:
                    metadata["speaker_ids"] = ",".join(speaker_ids)
            metadatas.append(metadata)
        collection.upsert(
            ids=batch_ids,
            documents=[chunk for chunk, _, _ in batch_chunks],
            metadatas=metadatas,
        )

    stale_ids = previous_ids.difference(chunk_ids)
    if stale_ids:
        collection.delete(ids=sorted(stale_ids))

    return chunk_ids


def query_memory(
    user_query: str,
    n_results: int = 3,
    meeting_id: int | None = None,
) -> list[dict[str, Any]]:
    """Return the closest locally indexed transcript chunks for a query."""
    if not isinstance(user_query, str):
        raise TypeError("user_query must be a string")
    query = user_query.strip()
    if not query:
        raise ValueError("user_query must contain non-whitespace content")
    if isinstance(n_results, bool) or not isinstance(n_results, int) or n_results <= 0:
        raise ValueError("n_results must be a positive integer")
    if meeting_id is not None:
        _validate_meeting_id(meeting_id)

    collection = _get_collection()
    if collection.count() == 0:
        return []

    query_parameters: dict[str, Any] = {
        "query_texts": [query],
        "n_results": n_results,
        "include": ["documents", "metadatas", "distances"],
    }
    if meeting_id is not None:
        query_parameters["where"] = {"meeting_id": meeting_id}

    result = collection.query(
        **query_parameters,
    )
    documents = (result.get("documents") or [[]])[0] or []
    metadatas = (result.get("metadatas") or [[]])[0] or []
    distances = (result.get("distances") or [[]])[0] or []
    ids = (result.get("ids") or [[]])[0] or []

    matches: list[dict[str, Any]] = []
    for index, document in enumerate(documents):
        metadata = metadatas[index] if index < len(metadatas) else {}
        distance = distances[index] if index < len(distances) else None
        chunk_id = ids[index] if index < len(ids) else None
        matches.append(
            {
                "id": chunk_id,
                "meeting_id": metadata.get("meeting_id"),
                "chunk_index": metadata.get("chunk_index"),
                "character_start": metadata.get("character_start"),
                "character_end": metadata.get("character_end"),
                "start_seconds": metadata.get("start_seconds"),
                "end_seconds": metadata.get("end_seconds"),
                "speaker_ids": metadata.get("speaker_ids"),
                "transcript_chunk": document,
                "distance": distance,
            }
        )
    return matches


def delete_meeting_transcript(meeting_id: int) -> None:
    """Remove all indexed transcript chunks for a meeting."""
    _validate_meeting_id(meeting_id)
    _get_collection().delete(where={"meeting_id": meeting_id})
