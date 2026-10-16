from datetime import date, time
from enum import Enum
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator


NonEmptyText = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=20_000),
]
ShortText = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=1_000),
]


class DecisionState(str, Enum):
    APPROVED = "Approved"
    CONTESTED = "Contested"
    OPEN_QUESTION = "Open Question"


class RiskSeverity(str, Enum):
    LOW = "Low"
    MEDIUM = "Medium"
    HIGH = "High"
    CRITICAL = "Critical"


class StrictOutputModel(BaseModel):
    """Base for LLM response types that reject unexpected JSON properties."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class TranscriptSegment(StrictOutputModel):
    """One redacted, time-aligned ASR segment with an optional speaker label."""

    start_seconds: float = Field(ge=0)
    end_seconds: float = Field(gt=0)
    speaker_id: ShortText | None = None
    text: NonEmptyText

    @model_validator(mode="after")
    def validate_time_range(self) -> "TranscriptSegment":
        if self.end_seconds <= self.start_seconds:
            raise ValueError("end_seconds must be greater than start_seconds")
        return self


class AudioProcessingResult(StrictOutputModel):
    """Redacted transcript output and the result of optional local diarization."""

    transcript: str
    transcript_segments: list[TranscriptSegment]
    duration_seconds: float = Field(ge=0)
    diarization_status: Literal[
        "completed",
        "not_configured",
        "unavailable",
        "privacy_fallback",
    ]


class MemoryAnswerModel(StrictOutputModel):
    """An answer grounded in a non-empty set of retrieved transcript chunks."""

    answer: NonEmptyText
    source_ids: list[ShortText] = Field(
        min_length=1,
        description="IDs of retrieved transcript chunks that directly support the answer.",
    )

    @model_validator(mode="after")
    def source_ids_are_unique(self) -> "MemoryAnswerModel":
        if len(self.source_ids) != len(set(self.source_ids)):
            raise ValueError("source_ids must be unique")
        return self


class ExtractedTask(StrictOutputModel):
    description: NonEmptyText = Field(
        description="A specific, actionable task agreed during the meeting."
    )
    owner: ShortText | None = Field(
        default=None,
        description="The task owner as stated in the meeting, or null if not identified.",
    )
    deadline: date | None = Field(
        default=None,
        description=(
            "The agreed calendar deadline in YYYY-MM-DD form. Resolve relative dates such as "
            "Thursday or tomorrow using the meeting date supplied in the extraction context. "
            "Use null only when no deadline can be determined."
        ),
    )
    deadline_time: time | None = Field(
        default=None,
        description=(
            "The explicitly stated local clock time for the deadline, in 24-hour HH:MM:SS form; "
            "use null when the meeting does not specify a clock time."
        ),
    )
    source_timestamp_seconds: float | None = Field(
        default=None,
        ge=0,
        description="Seconds from the beginning of the recording where the task was discussed.",
    )


class ExtractedDecision(StrictOutputModel):
    statement: NonEmptyText = Field(
        description="The decision, contested proposal, or unresolved question."
    )
    state: DecisionState = Field(
        description="Whether the decision is Approved, Contested, or an Open Question."
    )
    rationale: NonEmptyText | None = Field(
        default=None,
        description="Relevant reasoning stated in the meeting, or null if none was stated.",
    )
    timestamp_seconds: float | None = Field(
        default=None,
        ge=0,
        description="Seconds from the beginning of the recording where this item was discussed.",
    )


class TimelineEvent(StrictOutputModel):
    timestamp_seconds: float = Field(
        ge=0,
        description="Seconds from the beginning of the recording for this event.",
    )
    title: ShortText = Field(description="A concise label for this point in the meeting.")
    description: NonEmptyText = Field(description="What happened or was discussed at this point.")


class ExtractedRisk(StrictOutputModel):
    description: NonEmptyText = Field(description="The identified project risk or bottleneck.")
    severity: RiskSeverity
    mitigation: NonEmptyText | None = Field(
        default=None,
        description="A mitigation discussed in the meeting, or null if none was stated.",
    )
    owner: ShortText | None = Field(
        default=None,
        description="The risk owner as stated in the meeting, or null if not identified.",
    )
    source_timestamp_seconds: float | None = Field(default=None, ge=0)


class MeetingIntelligenceModel(StrictOutputModel):
    executive_summary: NonEmptyText = Field(
        description="A concise, factual executive summary of the meeting."
    )
    key_topics: list[ShortText] = Field(
        min_length=0,
        description="Distinct topics discussed, in order of their prominence.",
    )
    timeline: list[TimelineEvent] = Field(
        min_length=0,
        description="Chronological events with timestamps relative to recording start.",
    )
    action_items: list[ExtractedTask] = Field(
        min_length=0,
        description="Tasks agreed during the meeting, including any stated owner and deadline.",
    )
    decisions: list[ExtractedDecision] = Field(
        min_length=0,
        description="Approved decisions, contested proposals, and open questions.",
    )
    risks: list[ExtractedRisk] = Field(
        min_length=0,
        description="Project risks and bottlenecks, with severity and stated mitigation where available.",
    )
