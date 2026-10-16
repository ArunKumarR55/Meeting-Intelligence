"""Conservative, auditable cross-meeting action-item follow-through tracking."""

import re
from difflib import SequenceMatcher

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models.domain import ActionItem, ActionItemDrift, Meeting, TaskStatus


_WORD_RE = re.compile(r"[a-z0-9]+")
_OPEN_STATUSES = {TaskStatus.PROMISED, TaskStatus.IN_PROGRESS, TaskStatus.BLOCKED}
_MINIMUM_MATCH = 0.58


def _normalized_words(value: str) -> tuple[str, ...]:
    return tuple(_WORD_RE.findall(value.casefold()))


def _similarity(first: ActionItem, second: ActionItem) -> float:
    left_words = _normalized_words(first.description)
    right_words = _normalized_words(second.description)
    if not left_words or not right_words:
        return 0.0
    left = " ".join(left_words)
    right = " ".join(right_words)
    text_score = SequenceMatcher(None, left, right, autojunk=False).ratio()
    left_set, right_set = set(left_words), set(right_words)
    overlap = len(left_set & right_set) / max(1, len(left_set | right_set))
    score = 0.65 * text_score + 0.35 * overlap
    if first.owner and second.owner:
        if first.owner.casefold().strip() == second.owner.casefold().strip():
            score = min(1.0, score + 0.08)
        else:
            score *= 0.85
    return round(score, 4)


def _classify(prior: ActionItem, current: ActionItem | None, score: float) -> tuple[str, str]:
    if current is None:
        return (
            "not_mentioned",
            "No sufficiently similar task was extracted from this meeting; omission alone does not prove the task was dropped.",
        )
    current_status = current.status.value if isinstance(current.status, TaskStatus) else str(current.status)
    if current_status == TaskStatus.COMPLETED.value:
        label = "completed"
    elif current_status == TaskStatus.BLOCKED.value:
        label = "blocked"
    elif prior.due_date and current.due_date and current.due_date > prior.due_date:
        label = "delayed"
    elif score < 0.72:
        label = "changed"
    else:
        label = "carried_forward"

    evidence_parts = [f"Matched later action item with similarity {score:.0%}."]
    if current.owner:
        evidence_parts.append(f"Later owner: {current.owner}.")
    if current.due_date:
        evidence_parts.append(f"Later due date: {current.due_date.isoformat()}.")
    if prior.due_date and current.due_date and current.due_date > prior.due_date:
        evidence_parts.append(f"Original due date: {prior.due_date.isoformat()}.")
    return label, " ".join(evidence_parts)


def track_action_item_drift(db: Session, meeting_id: int) -> list[ActionItemDrift]:
    """Compare this meeting's tasks to prior unresolved tasks and persist observations.

    Matching is deliberately conservative and does not mutate prior task status.
    A missing match is recorded as ``not_mentioned`` rather than asserting deletion.
    """
    current_meeting = db.get(Meeting, meeting_id)
    if current_meeting is None:
        raise ValueError("Meeting not found")

    current_items = list(
        db.scalars(
            select(ActionItem)
            .where(ActionItem.meeting_id == meeting_id)
            .order_by(ActionItem.id)
        ).all()
    )
    prior_items = list(
        db.scalars(
            select(ActionItem)
            .join(Meeting, Meeting.id == ActionItem.meeting_id)
            .where(Meeting.created_at < current_meeting.created_at, ActionItem.meeting_id != meeting_id)
            .order_by(Meeting.created_at.desc(), ActionItem.id.desc())
        ).all()
    )
    prior_items = [
        item for item in prior_items
        if (item.status if isinstance(item.status, TaskStatus) else TaskStatus(item.status)) in _OPEN_STATUSES
    ]
    existing_prior_ids = set(
        db.scalars(
            select(ActionItemDrift.prior_action_item_id).where(
                ActionItemDrift.observed_in_meeting_id == meeting_id
            )
        ).all()
    )
    available_current = set(range(len(current_items)))
    observations: list[ActionItemDrift] = []

    for prior in prior_items:
        if prior.id in existing_prior_ids:
            continue
        ranked = sorted(
            ((_similarity(prior, current_items[index]), index) for index in available_current),
            reverse=True,
        )
        score, index = ranked[0] if ranked else (0.0, -1)
        matched = current_items[index] if index >= 0 and score >= _MINIMUM_MATCH else None
        if matched is not None:
            available_current.remove(index)
        label, evidence = _classify(prior, matched, score if matched is not None else 0.0)
        observation = ActionItemDrift(
            prior_action_item_id=prior.id,
            observed_action_item_id=matched.id if matched is not None else None,
            observed_in_meeting_id=meeting_id,
            drift_type=label,
            match_score=score if matched is not None else 0.0,
            evidence=evidence,
        )
        db.add(observation)
        observations.append(observation)

    db.flush()
    return observations


def list_action_item_drift(db: Session, limit: int = 200) -> list[dict[str, object]]:
    """Return persisted observations with the source and follow-up meeting labels."""
    rows = db.execute(
        select(ActionItemDrift, ActionItem, Meeting)
        .join(ActionItem, ActionItem.id == ActionItemDrift.prior_action_item_id)
        .join(Meeting, Meeting.id == ActionItem.meeting_id)
        .order_by(ActionItemDrift.created_at.desc(), ActionItemDrift.id.desc())
        .limit(limit)
    )
    # Fetch observed entities separately; explicit lookup avoids ambiguous ORM aliases.
    result: list[dict[str, object]] = []
    for drift, prior, source_meeting in rows:
        observed = db.get(ActionItem, drift.observed_action_item_id) if drift.observed_action_item_id else None
        observed_meeting = db.get(Meeting, drift.observed_in_meeting_id)
        result.append({
            "id": drift.id,
            "drift_type": drift.drift_type,
            "match_score": drift.match_score,
            "evidence": drift.evidence,
            "prior_action_item_id": prior.id,
            "prior_description": prior.description,
            "prior_owner": prior.owner,
            "prior_due_date": prior.due_date.isoformat() if prior.due_date else None,
            "source_meeting_id": source_meeting.id,
            "source_meeting_title": source_meeting.title,
            "observed_action_item_id": observed.id if observed else None,
            "observed_description": observed.description if observed else None,
            "observed_meeting_id": observed_meeting.id if observed_meeting else None,
            "observed_meeting_title": observed_meeting.title if observed_meeting else None,
            "created_at": drift.created_at,
        })
    return result
