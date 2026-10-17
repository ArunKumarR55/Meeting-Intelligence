"""Create Jira Cloud issues for AMIP action items."""

from __future__ import annotations

from datetime import date
from typing import Any
from urllib.parse import urlsplit

import requests


def _adf_description(text: str) -> dict[str, Any]:
    """Convert plain meeting task text into Jira's Atlassian Document Format."""
    paragraphs = []
    for line in text.splitlines() or [text]:
        content = [{"type": "text", "text": line}] if line else []
        paragraphs.append({"type": "paragraph", "content": content})
    return {"type": "doc", "version": 1, "content": paragraphs}


def create_jira_issue(
    *,
    task_description: str,
    base_url: str,
    email: str,
    api_token: str,
    project_key: str,
    issue_type: str = "Task",
    due_date: date | None = None,
) -> dict[str, str]:
    """Create one Jira Cloud issue and return its key and browser URL."""
    values = {
        "task_description": task_description,
        "base_url": base_url,
        "email": email,
        "api_token": api_token,
        "project_key": project_key,
        "issue_type": issue_type,
    }
    if any(not isinstance(value, str) or not value.strip() for value in values.values()):
        raise ValueError("Jira connection settings and task description must be provided")

    normalized_description = task_description.strip()
    first_line = next((line.strip() for line in normalized_description.splitlines() if line.strip()), "")
    summary = first_line[:255]
    normalized_base_url = base_url.strip().rstrip("/")
    endpoint = f"{normalized_base_url}/rest/api/3/issue"
    payload = {
        "fields": {
            "project": {"key": project_key.strip()},
            "summary": summary,
            "description": _adf_description(normalized_description),
            "issuetype": {"name": issue_type.strip()},
        }
    }
    if due_date is not None:
        payload["fields"]["duedate"] = due_date.isoformat()

    try:
        response = requests.post(
            endpoint,
            auth=(email.strip(), api_token.strip()),
            headers={"Accept": "application/json", "Content-Type": "application/json"},
            json=payload,
            timeout=(5, 30),
        )
    except requests.Timeout as error:
        raise TimeoutError("Jira issue creation timed out") from error
    except requests.RequestException as error:
        raise RuntimeError("Jira issue creation request failed") from error

    try:
        response_data = response.json()
    except ValueError:
        response_data = {}

    if not response.ok:
        # Keep provider details and request contents out of logs and API responses.
        raise RuntimeError(f"Jira rejected issue creation with HTTP {response.status_code}")

    issue_key = response_data.get("key") if isinstance(response_data, dict) else None
    if not isinstance(issue_key, str) or not issue_key.strip():
        raise RuntimeError("Jira returned a successful response without an issue key")
    return {
        "issue_key": issue_key,
        "issue_url": f"{normalized_base_url}/browse/{issue_key}",
    }


def update_jira_issue_deadline(
    *,
    issue_url: str,
    base_url: str,
    email: str,
    api_token: str,
    due_date: date,
) -> None:
    """Update the Jira due-date field for a previously linked issue."""
    normalized_base_url = base_url.strip().rstrip("/")
    base = urlsplit(normalized_base_url)
    issue = urlsplit(issue_url)
    issue_key = issue.path.rstrip("/").rsplit("/", 1)[-1]
    if (
        base.scheme != "https"
        or issue.scheme != "https"
        or issue.netloc.casefold() != base.netloc.casefold()
        or not issue.path.startswith("/browse/")
        or not issue_key
        or not all(character.isalnum() or character == "-" for character in issue_key)
    ):
        raise ValueError("Stored Jira issue URL is invalid or does not match the configured Jira host")
    endpoint = f"{normalized_base_url}/rest/api/3/issue/{issue_key}"
    try:
        response = requests.put(
            endpoint,
            auth=(email.strip(), api_token.strip()),
            headers={"Accept": "application/json", "Content-Type": "application/json"},
            json={"fields": {"duedate": due_date.isoformat()}},
            timeout=(5, 30),
        )
    except requests.Timeout as error:
        raise TimeoutError("Jira issue update timed out") from error
    except requests.RequestException as error:
        raise RuntimeError("Jira issue update request failed") from error
    if not response.ok:
        raise RuntimeError(f"Jira rejected the due-date update with HTTP {response.status_code}")
