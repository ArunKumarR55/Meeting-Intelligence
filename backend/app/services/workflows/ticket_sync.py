"""GitHub Issues integration for extracted AMIP action items."""

import re
from urllib.parse import unquote, urlsplit

import requests


GITHUB_API_BASE = "https://api.github.com"
GITHUB_API_VERSION = "2022-11-28"
GITHUB_REPOSITORY_COMPONENT = re.compile(r"^[A-Za-z0-9_.-]+$")


def _repository_owner_and_name(repo_url: str) -> tuple[str, str]:
    if not isinstance(repo_url, str) or not repo_url.strip():
        raise ValueError("repo_url must be a GitHub repository URL")

    parsed_url = urlsplit(repo_url.strip())
    if parsed_url.scheme != "https" or parsed_url.netloc.casefold() != "github.com":
        raise ValueError("repo_url must use the https://github.com host")

    path_components = [
        unquote(component)
        for component in parsed_url.path.strip("/").split("/")
        if component
    ]
    if len(path_components) != 2:
        raise ValueError("repo_url must identify exactly one owner and repository")

    owner, repository = path_components
    if repository.endswith(".git"):
        repository = repository[:-4]
    if (
        not GITHUB_REPOSITORY_COMPONENT.fullmatch(owner)
        or not GITHUB_REPOSITORY_COMPONENT.fullmatch(repository)
    ):
        raise ValueError("repo_url contains an invalid owner or repository name")
    return owner, repository


def push_task_to_github(task_description: str, repo_url: str, github_token: str) -> dict:
    """Create a GitHub issue for one extracted action item and return its JSON."""
    if not isinstance(task_description, str) or not task_description.strip():
        raise ValueError("task_description must contain non-whitespace content")
    if not isinstance(github_token, str) or not github_token.strip():
        raise ValueError("github_token must contain a token")

    owner, repository = _repository_owner_and_name(repo_url)
    description = task_description.strip()
    first_line = next(
        (line.strip() for line in description.splitlines() if line.strip()),
        "",
    )
    title = first_line[:256]
    issue_url = f"{GITHUB_API_BASE}/repos/{owner}/{repository}/issues"
    headers = {
        "Accept": "application/vnd.github+json",
        "Authorization": f"Bearer {github_token.strip()}",
        "X-GitHub-Api-Version": GITHUB_API_VERSION,
        "User-Agent": "AMIP-Meeting-Intelligence-Platform",
    }
    payload = {"title": title, "body": description}

    try:
        response = requests.post(
            issue_url,
            headers=headers,
            json=payload,
            timeout=(5, 30),
        )
    except requests.Timeout as error:
        raise TimeoutError("GitHub issue creation timed out") from error
    except requests.RequestException as error:
        raise RuntimeError("GitHub issue creation request failed") from error

    try:
        response_data = response.json()
    except ValueError:
        response_data = {}

    if not response.ok:
        error_message = (
            response_data.get("message")
            if isinstance(response_data, dict)
            else None
        )
        if not isinstance(error_message, str) or not error_message:
            error_message = "GitHub returned an error without a message"
        raise RuntimeError(
            f"GitHub issue creation failed with HTTP {response.status_code}: {error_message}"
        )

    if (
        not isinstance(response_data, dict)
        or not isinstance(response_data.get("html_url"), str)
    ):
        raise RuntimeError("GitHub returned a successful response without an issue URL")
    return response_data
