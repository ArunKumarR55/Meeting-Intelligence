"""Google and Microsoft Calendar OAuth, event listing, and task deadline sync."""

import logging
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session, joinedload

from app.core.config import settings
from app.core.database import get_db
from app.db.models.domain import ActionItem, CalendarOAuthState
from app.services.integrations.calendar_service import (
    CalendarConfigurationError,
    CalendarProviderError,
    complete_authorization,
    connection_status,
    create_authorization_url,
    list_upcoming_events,
    push_action_item_deadline,
)


logger = logging.getLogger(__name__)
router = APIRouter()
_VALID_PROVIDERS = {"google", "microsoft"}


def _provider_or_404(provider: str) -> str:
    normalized = provider.casefold()
    if normalized not in _VALID_PROVIDERS:
        raise HTTPException(status_code=404, detail="Calendar provider not found")
    return normalized


@router.get("/connections", summary="List connected calendar providers")
def get_connections(db: Session = Depends(get_db)) -> dict[str, object]:
    return {"connections": connection_status(db)}


@router.get("/{provider}/connect", summary="Start Calendar OAuth authorization")
def connect_calendar(provider: str, db: Session = Depends(get_db)) -> RedirectResponse:
    provider = _provider_or_404(provider)
    try:
        authorization_url = create_authorization_url(provider, db)
    except CalendarConfigurationError as error:
        raise HTTPException(status_code=503, detail=str(error)) from error
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    return RedirectResponse(authorization_url, status_code=status.HTTP_302_FOUND)


@router.get("/{provider}/callback", summary="Complete Calendar OAuth authorization")
def calendar_callback(
    provider: str,
    db: Session = Depends(get_db),
    code: str | None = Query(default=None, min_length=1),
    state: str | None = Query(default=None, min_length=16),
    error: str | None = Query(default=None),
) -> RedirectResponse:
    provider = _provider_or_404(provider)
    if state:
        state_record = db.get(CalendarOAuthState, state)
    else:
        state_record = None
    if error:
        if state_record is not None:
            db.delete(state_record)
            db.commit()
        target = f"{settings.frontend_base_url.rstrip('/')}/?{urlencode({'calendar': provider, 'status': 'denied'})}"
        return RedirectResponse(target, status_code=status.HTTP_303_SEE_OTHER)
    if not code or not state:
        raise HTTPException(status_code=400, detail="Calendar callback is missing its authorization code or state")
    try:
        connection = complete_authorization(provider, code, state, db)
    except ValueError as exception:
        raise HTTPException(status_code=400, detail=str(exception)) from exception
    except CalendarConfigurationError as exception:
        raise HTTPException(status_code=503, detail=str(exception)) from exception
    except CalendarProviderError as exception:
        logger.warning("Calendar OAuth token exchange failed for %s: %s", provider, exception)
        raise HTTPException(status_code=502, detail=str(exception)) from exception
    target = f"{settings.frontend_base_url.rstrip('/')}/?{urlencode({'calendar': provider, 'status': 'connected', 'account': connection.account_email or ''})}"
    return RedirectResponse(target, status_code=status.HTTP_303_SEE_OTHER)


@router.get("/{provider}/events", summary="List upcoming calendar events")
def upcoming_events(provider: str, db: Session = Depends(get_db)) -> dict[str, object]:
    provider = _provider_or_404(provider)
    try:
        events = list_upcoming_events(provider, db)
    except ValueError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    except CalendarConfigurationError as error:
        raise HTTPException(status_code=503, detail=str(error)) from error
    except CalendarProviderError as error:
        raise HTTPException(status_code=502, detail=str(error)) from error
    return {"provider": provider, "events": events}


@router.post("/{provider}/tasks/{action_item_id}", summary="Add an action-item deadline to a calendar")
def sync_action_item_deadline(
    provider: str,
    action_item_id: int,
    db: Session = Depends(get_db),
) -> dict[str, object]:
    provider = _provider_or_404(provider)
    item = db.query(ActionItem).options(joinedload(ActionItem.meeting)).filter(ActionItem.id == action_item_id).one_or_none()
    if item is None:
        raise HTTPException(status_code=404, detail="Action item not found")
    try:
        result = push_action_item_deadline(provider, item, db)
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    except CalendarConfigurationError as error:
        raise HTTPException(status_code=503, detail=str(error)) from error
    except CalendarProviderError as error:
        raise HTTPException(status_code=502, detail=str(error)) from error
    return {"action_item_id": item.id, **result}


@router.delete("/{provider}/connection", status_code=status.HTTP_204_NO_CONTENT, summary="Disconnect a calendar")
def disconnect_calendar(provider: str, db: Session = Depends(get_db)) -> None:
    from app.db.models.domain import CalendarConnection

    provider = _provider_or_404(provider)
    connection = db.get(CalendarConnection, provider)
    if connection is None:
        raise HTTPException(status_code=404, detail="Calendar connection not found")
    db.delete(connection)
    db.commit()
    return None
