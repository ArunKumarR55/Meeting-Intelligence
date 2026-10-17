"""OAuth, encrypted token storage, and event operations for Google and Microsoft calendars."""

import base64
import hashlib
import secrets
from datetime import date, datetime, timedelta, timezone
from urllib.parse import quote, urlencode
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import requests
from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.models.domain import ActionItem, CalendarConnection, CalendarOAuthState, CalendarTaskSync, utc_now


PROVIDERS = {"google", "microsoft"}
HTTP_TIMEOUT_SECONDS = 20


class CalendarConfigurationError(RuntimeError):
    """Raised for missing provider credentials or token encryption configuration."""


class CalendarProviderError(RuntimeError):
    """Raised when a calendar provider rejects or cannot complete an API request."""


def _validate_provider(provider: str) -> str:
    normalized = provider.casefold()
    if normalized not in PROVIDERS:
        raise ValueError("Calendar provider must be 'google' or 'microsoft'")
    return normalized


def _credentials(provider: str) -> tuple[str, str]:
    provider = _validate_provider(provider)
    if provider == "google":
        client_id = settings.google_calendar_client_id
        secret = settings.google_calendar_client_secret
    else:
        client_id = settings.microsoft_calendar_client_id
        secret = settings.microsoft_calendar_client_secret
    if not client_id or secret is None:
        raise CalendarConfigurationError(f"{provider.title()} Calendar OAuth credentials are not configured")
    return client_id, secret.get_secret_value()


def _fernet() -> Fernet:
    key = settings.calendar_token_encryption_key
    if key is None:
        raise CalendarConfigurationError("CALENDAR_TOKEN_ENCRYPTION_KEY must be configured before connecting a calendar")
    try:
        return Fernet(key.get_secret_value().encode("ascii"))
    except (ValueError, UnicodeEncodeError) as error:
        raise CalendarConfigurationError("CALENDAR_TOKEN_ENCRYPTION_KEY must be a valid Fernet key") from error


def _encrypt_token(token: str) -> str:
    return _fernet().encrypt(token.encode("utf-8")).decode("ascii")


def _decrypt_token(token: str) -> str:
    try:
        return _fernet().decrypt(token.encode("ascii")).decode("utf-8")
    except (InvalidToken, ValueError, UnicodeEncodeError) as error:
        raise CalendarConfigurationError("Stored calendar credentials could not be decrypted; verify CALENDAR_TOKEN_ENCRYPTION_KEY") from error


def _redirect_uri(provider: str) -> str:
    return f"{settings.public_base_url}/api/v1/calendar/{provider}/callback"


def create_authorization_url(provider: str, db: Session) -> str:
    """Persist a random one-use state and return the provider consent URL."""
    provider = _validate_provider(provider)
    client_id, _ = _credentials(provider)
    _fernet()  # Fail before redirecting if secrets cannot be persisted safely.
    db.execute(
        delete(CalendarOAuthState).where(
            CalendarOAuthState.created_at < utc_now() - timedelta(days=1)
        )
    )
    state = secrets.token_urlsafe(48)
    verifier = secrets.token_urlsafe(64)
    db.add(CalendarOAuthState(state=state, provider=provider, pkce_verifier=verifier))
    db.commit()
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode("ascii")).digest()).decode("ascii").rstrip("=")
    redirect_uri = _redirect_uri(provider)

    if provider == "google":
        parameters = {
            "client_id": client_id,
            "redirect_uri": redirect_uri,
            "response_type": "code",
            "scope": "openid email https://www.googleapis.com/auth/calendar.events",
            "access_type": "offline",
            "prompt": "consent",
            "include_granted_scopes": "true",
            "state": state,
            "code_challenge": challenge,
            "code_challenge_method": "S256",
        }
        return "https://accounts.google.com/o/oauth2/v2/auth?" + urlencode(parameters)

    tenant = settings.microsoft_calendar_tenant.strip() or "common"
    parameters = {
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "response_mode": "query",
        "scope": "openid profile email offline_access User.Read Calendars.ReadWrite",
        "state": state,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
    }
    return f"https://login.microsoftonline.com/{tenant}/oauth2/v2.0/authorize?" + urlencode(parameters)


def _post_token(provider: str, form: dict[str, str]) -> dict[str, object]:
    provider = _validate_provider(provider)
    client_id, client_secret = _credentials(provider)
    tenant = settings.microsoft_calendar_tenant.strip() or "common"
    token_url = (
        "https://oauth2.googleapis.com/token"
        if provider == "google"
        else f"https://login.microsoftonline.com/{tenant}/oauth2/v2.0/token"
    )
    try:
        response = requests.post(
            token_url,
            data={**form, "client_id": client_id, "client_secret": client_secret},
            timeout=HTTP_TIMEOUT_SECONDS,
        )
    except requests.RequestException as error:
        raise CalendarProviderError("Calendar identity provider could not be reached") from error
    if not response.ok:
        raise CalendarProviderError(f"Calendar identity provider rejected the token request (HTTP {response.status_code})")
    payload = response.json()
    if not isinstance(payload, dict) or not isinstance(payload.get("access_token"), str):
        raise CalendarProviderError("Calendar identity provider returned an invalid token response")
    return payload


def complete_authorization(provider: str, code: str, state: str, db: Session) -> CalendarConnection:
    """Validate the one-use state, redeem the authorization code, and encrypt tokens."""
    provider = _validate_provider(provider)
    state_record = db.get(CalendarOAuthState, state)
    if state_record is None or state_record.provider != provider:
        raise ValueError("Calendar authorization state is invalid or already used")
    created_at = state_record.created_at
    if created_at.tzinfo is None:
        created_at = created_at.replace(tzinfo=timezone.utc)
    if utc_now() - created_at > timedelta(minutes=15):
        db.delete(state_record)
        db.commit()
        raise ValueError("Calendar authorization expired; please connect again")

    token_payload = _post_token(provider, {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": _redirect_uri(provider),
        "code_verifier": state_record.pkce_verifier or "",
    })
    access_token = str(token_payload["access_token"])
    refresh_token = token_payload.get("refresh_token")
    existing = db.get(CalendarConnection, provider)
    if not refresh_token and existing is not None and existing.refresh_token_encrypted:
        encrypted_refresh = existing.refresh_token_encrypted
    else:
        encrypted_refresh = _encrypt_token(str(refresh_token)) if refresh_token else None
    expires_in = int(token_payload.get("expires_in", 3600))

    account_email = _get_account_email(provider, access_token)
    scopes = str(token_payload.get("scope") or (
        "openid email calendar.events" if provider == "google" else "offline_access User.Read Calendars.ReadWrite"
    ))
    if existing is None:
        existing = CalendarConnection(
            provider=provider,
            access_token_encrypted=_encrypt_token(access_token),
            refresh_token_encrypted=encrypted_refresh,
            token_expires_at=utc_now() + timedelta(seconds=expires_in),
            scopes=scopes,
            account_email=account_email,
        )
        db.add(existing)
    else:
        existing.access_token_encrypted = _encrypt_token(access_token)
        existing.refresh_token_encrypted = encrypted_refresh
        existing.token_expires_at = utc_now() + timedelta(seconds=expires_in)
        existing.scopes = scopes
        existing.account_email = account_email or existing.account_email
        existing.updated_at = utc_now()
    db.delete(state_record)
    db.commit()
    db.refresh(existing)
    return existing


def _get_account_email(provider: str, access_token: str) -> str | None:
    endpoint = (
        "https://openidconnect.googleapis.com/v1/userinfo"
        if provider == "google"
        else "https://graph.microsoft.com/v1.0/me?$select=mail,userPrincipalName"
    )
    try:
        response = requests.get(
            endpoint,
            headers={"Authorization": f"Bearer {access_token}"},
            timeout=HTTP_TIMEOUT_SECONDS,
        )
    except requests.RequestException:
        return None
    if not response.ok:
        return None
    data = response.json()
    if provider == "google":
        return data.get("email")
    return data.get("mail") or data.get("userPrincipalName")


def connection_status(db: Session) -> list[dict[str, object]]:
    connections = {item.provider: item for item in db.scalars(select(CalendarConnection)).all()}
    try:
        _fernet()
        encryption_key_is_valid = True
    except CalendarConfigurationError:
        encryption_key_is_valid = False
    statuses = []
    for provider in sorted(PROVIDERS):
        if provider == "google":
            configured = bool(settings.google_calendar_client_id and settings.google_calendar_client_secret and encryption_key_is_valid)
        else:
            configured = bool(settings.microsoft_calendar_client_id and settings.microsoft_calendar_client_secret and encryption_key_is_valid)
        statuses.append({
            "provider": provider,
            "connected": provider in connections,
            "configured": configured,
            "account_email": connections[provider].account_email if provider in connections else None,
            "connected_at": connections[provider].connected_at if provider in connections else None,
        })
    return statuses


def _token(provider: str, db: Session) -> tuple[CalendarConnection, str]:
    provider = _validate_provider(provider)
    connection = db.get(CalendarConnection, provider)
    if connection is None:
        raise ValueError(f"{provider.title()} Calendar is not connected")
    access_token = _decrypt_token(connection.access_token_encrypted)
    expiry = connection.token_expires_at
    now = utc_now()
    if expiry is not None:
        if expiry.tzinfo is None:
            expiry = expiry.replace(tzinfo=timezone.utc)
        if expiry <= now + timedelta(seconds=90):
            if not connection.refresh_token_encrypted:
                raise ValueError(f"{provider.title()} Calendar authorization expired; reconnect the account")
            refresh_payload = _post_token(provider, {
                "grant_type": "refresh_token",
                "refresh_token": _decrypt_token(connection.refresh_token_encrypted),
            })
            access_token = str(refresh_payload["access_token"])
            connection.access_token_encrypted = _encrypt_token(access_token)
            if refresh_payload.get("refresh_token"):
                connection.refresh_token_encrypted = _encrypt_token(str(refresh_payload["refresh_token"]))
            connection.token_expires_at = now + timedelta(seconds=int(refresh_payload.get("expires_in", 3600)))
            connection.updated_at = now
            db.commit()
    return connection, access_token


def _provider_request(provider: str, method: str, endpoint: str, db: Session, **kwargs: object) -> dict[str, object]:
    _, token = _token(provider, db)
    headers = dict(kwargs.pop("headers", {}))
    headers["Authorization"] = f"Bearer {token}"
    headers.setdefault("Accept", "application/json")
    try:
        response = requests.request(
            method,
            endpoint,
            headers=headers,
            timeout=HTTP_TIMEOUT_SECONDS,
            **kwargs,
        )
    except requests.RequestException as error:
        raise CalendarProviderError("Calendar provider could not be reached") from error
    if not response.ok:
        if response.status_code == 401:
            raise CalendarProviderError("Calendar authorization was rejected; reconnect the account")
        raise CalendarProviderError(f"Calendar provider request failed (HTTP {response.status_code})")
    if not response.content:
        return {}
    payload = response.json()
    return payload if isinstance(payload, dict) else {}


def list_upcoming_events(provider: str, db: Session, days: int = 30) -> list[dict[str, object]]:
    """Fetch a bounded next-days event window and normalize provider responses."""
    provider = _validate_provider(provider)
    now = datetime.now(timezone.utc)
    until = now + timedelta(days=days)
    if provider == "google":
        response = _provider_request(
            provider,
            "GET",
            "https://www.googleapis.com/calendar/v3/calendars/primary/events",
            db,
            params={
                "timeMin": now.isoformat(),
                "timeMax": until.isoformat(),
                "singleEvents": "true",
                "orderBy": "startTime",
                "maxResults": 100,
            },
        )
        events = response.get("items", [])
        normalized = []
        for item in events if isinstance(events, list) else []:
            if not isinstance(item, dict):
                continue
            start = item.get("start", {})
            normalized.append({
                "id": item.get("id"),
                "subject": item.get("summary") or "Untitled event",
                "start": start.get("dateTime") or start.get("date"),
                "end": (item.get("end") or {}).get("dateTime") or (item.get("end") or {}).get("date"),
                "location": (item.get("location") or ""),
                "web_url": item.get("htmlLink"),
                "provider": provider,
            })
        return normalized

    endpoint = "https://graph.microsoft.com/v1.0/me/calendarView"
    response = _provider_request(
        provider,
        "GET",
        endpoint,
        db,
        params={
            "startDateTime": now.isoformat(),
            "endDateTime": until.isoformat(),
            "$select": "id,subject,start,end,location,webLink",
            "$orderby": "start/dateTime",
            "$top": 100,
        },
        headers={"Prefer": 'outlook.timezone="UTC"'},
    )
    events = response.get("value", [])
    normalized = []
    for item in events if isinstance(events, list) else []:
        if not isinstance(item, dict):
            continue
        normalized.append({
            "id": item.get("id"),
            "subject": item.get("subject") or "Untitled event",
            "start": (item.get("start") or {}).get("dateTime"),
            "end": (item.get("end") or {}).get("dateTime"),
            "location": ((item.get("location") or {}).get("displayName") or ""),
            "web_url": item.get("webLink"),
            "provider": provider,
        })
    return normalized


def push_action_item_deadline(provider: str, action_item: ActionItem, db: Session) -> dict[str, object]:
    """Create a task event or update its existing event from the current deadline."""
    provider = _validate_provider(provider)
    if action_item.due_date is None:
        raise ValueError("This action item has no due date")
    existing_sync = db.scalar(
        select(CalendarTaskSync).where(
            CalendarTaskSync.provider == provider,
            CalendarTaskSync.action_item_id == action_item.id,
        )
    )
    due: date = action_item.due_date
    end_date = due + timedelta(days=1)
    meeting_title = action_item.meeting.title if action_item.meeting else "AMIP action item"
    description_parts = [action_item.description]
    if action_item.owner:
        description_parts.append(f"Owner: {action_item.owner}")
    description_parts.append(f"Source meeting: {meeting_title}")
    description = "\n".join(description_parts)

    if action_item.due_time is not None:
        try:
            local_timezone = ZoneInfo(settings.reminder_timezone)
        except (ZoneInfoNotFoundError, ValueError) as error:
            raise CalendarConfigurationError(
                "REMINDER_TIMEZONE must be a valid IANA timezone to create timed calendar events"
            ) from error
        timed_start = datetime.combine(due, action_item.due_time, tzinfo=local_timezone)
        timed_end = timed_start + timedelta(hours=1)

    if provider == "google":
        if action_item.due_time is not None:
            start_payload = {
                "dateTime": timed_start.isoformat(),
                "timeZone": settings.reminder_timezone,
            }
            end_payload = {
                "dateTime": timed_end.isoformat(),
                "timeZone": settings.reminder_timezone,
            }
        else:
            start_payload = {"date": due.isoformat()}
            end_payload = {"date": end_date.isoformat()}
        payload = {
            "summary": f"AMIP task: {action_item.description[:150]}",
            "description": description,
            "start": start_payload,
            "end": end_payload,
        }
        if existing_sync is not None and existing_sync.event_id:
            updated = _provider_request(
                provider,
                "PATCH",
                f"https://www.googleapis.com/calendar/v3/calendars/primary/events/{quote(existing_sync.event_id, safe='')}",
                db,
                json=payload,
            )
            existing_sync.event_url = updated.get("htmlLink") or existing_sync.event_url
            existing_sync.synced_at = utc_now()
            db.commit()
            return {
                "provider": provider,
                "event_id": existing_sync.event_id,
                "event_url": existing_sync.event_url,
                "already_synced": True,
                "updated": True,
            }
        created = _provider_request(
            provider,
            "POST",
            "https://www.googleapis.com/calendar/v3/calendars/primary/events",
            db,
            json=payload,
        )
        result = {"provider": provider, "event_id": created.get("id"), "event_url": created.get("htmlLink")}
        db.add(CalendarTaskSync(
            provider=provider,
            action_item_id=action_item.id,
            event_id=result["event_id"],
            event_url=result["event_url"],
        ))
        db.commit()
        return result

    if action_item.due_time is not None:
        graph_start = timed_start.astimezone(timezone.utc).replace(tzinfo=None).isoformat()
        graph_end = timed_end.astimezone(timezone.utc).replace(tzinfo=None).isoformat()
        graph_body = {
            "isAllDay": False,
            "start": {"dateTime": graph_start, "timeZone": "UTC"},
            "end": {"dateTime": graph_end, "timeZone": "UTC"},
        }
    else:
        graph_body = {
            "isAllDay": True,
            "start": {"dateTime": f"{due.isoformat()}T00:00:00", "timeZone": "UTC"},
            "end": {"dateTime": f"{end_date.isoformat()}T00:00:00", "timeZone": "UTC"},
        }
    payload = {
        "subject": f"AMIP task: {action_item.description[:150]}",
        "body": {"contentType": "Text", "content": description},
        **graph_body,
    }
    if existing_sync is not None and existing_sync.event_id:
        updated = _provider_request(
            provider,
            "PATCH",
            f"https://graph.microsoft.com/v1.0/me/events/{quote(existing_sync.event_id, safe='')}",
            db,
            json=payload,
        )
        existing_sync.event_url = updated.get("webLink") or existing_sync.event_url
        existing_sync.synced_at = utc_now()
        db.commit()
        return {
            "provider": provider,
            "event_id": existing_sync.event_id,
            "event_url": existing_sync.event_url,
            "already_synced": True,
            "updated": True,
        }
    created = _provider_request(
        provider,
        "POST",
        "https://graph.microsoft.com/v1.0/me/events",
        db,
        json=payload,
    )
    result = {"provider": provider, "event_id": created.get("id"), "event_url": created.get("webLink")}
    db.add(CalendarTaskSync(
        provider=provider,
        action_item_id=action_item.id,
        event_id=result["event_id"],
        event_url=result["event_url"],
    ))
    db.commit()
    return result
