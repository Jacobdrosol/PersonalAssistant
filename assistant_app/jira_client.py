from __future__ import annotations

import base64
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional, Tuple

import requests

from .settings_store import JiraSettings, normalize_jira_base_url


class JiraApiError(Exception):
    """Raised when Jira responds with an error."""

    def __init__(self, status_code: int, message: str):
        super().__init__(f"Jira API error {status_code}: {message}")
        self.status_code = status_code
        self.message = message


@dataclass(slots=True)
class JiraCredentials:
    base_url: str
    email: str
    api_token: str


class JiraClient:
    """Minimal Jira REST API helper used for credential validation."""

    def __init__(
        self,
        credentials: JiraCredentials,
        *,
        timeout: float = 10.0,
        logger: Optional[Callable[[str, str, Optional[Dict[str, Any]], Optional[Dict[str, Any]], int, str], None]] = None,
    ) -> None:
        self.credentials = JiraCredentials(
            base_url=normalize_jira_base_url(credentials.base_url).rstrip("/"),
            email=credentials.email.strip(),
            api_token=credentials.api_token.strip(),
        )
        self.timeout = timeout
        self._logger = logger

    @classmethod
    def from_settings(
        cls,
        settings: JiraSettings,
        *,
        timeout: float = 10.0,
        logger: Optional[Callable[[str, str, Optional[Dict[str, Any]], Optional[Dict[str, Any]], int, str], None]] = None,
    ) -> "JiraClient":
        return cls(
            JiraCredentials(
                base_url=settings.base_url,
                email=settings.email,
                api_token=settings.api_token,
            ),
            timeout=timeout,
            logger=logger,
        )

    def test_connection(self) -> Tuple[bool, str]:
        creds = self.credentials
        if not creds.base_url or not creds.email or not creds.api_token:
            return False, "Base URL, email, and API token are required."
        try:
            payload = self._request("GET", "/rest/api/3/myself")
        except JiraApiError as exc:
            if exc.status_code == 401:
                return False, "Unauthorized. Check your email and API token."
            if exc.status_code == 404:
                return False, "Base URL appears invalid. Verify the Jira site address."
            return False, exc.message
        except requests.RequestException as exc:
            return False, f"Connection failed: {exc}"

        display_name = payload.get("displayName") or payload.get("emailAddress") or "your account"
        return True, f"Connected to Jira as {display_name}."

    def search_issues(
        self,
        jql: str,
        *,
        fields: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        max_results: int = 100,
    ) -> List[Dict[str, Any]]:
        max_results = max(1, min(max_results, 100))
        search_body: Dict[str, Any] = {
            "jql": jql,
            "startAt": 0,
            "maxResults": max_results,
        }
        if fields:
            search_body["fields"] = fields
        if expand:
            search_body["expand"] = expand
        try:
            payload = self._request("POST", "/rest/api/3/search", json_body=search_body)
            issues = payload.get("issues")
            if isinstance(issues, list):
                return issues
        except JiraApiError as exc:
            if exc.status_code not in (404, 410):
                raise
            # POST /search removed, fall through to /search/jql
        query_entry: Dict[str, Any] = {
            "query": {"queryString": jql},
            "maxResults": max_results,
        }
        if fields:
            query_entry["fields"] = fields
        if expand:
            query_entry["expand"] = expand
        body = {"queries": [query_entry]}
        payload = self._request("POST", "/rest/api/3/search/jql", json_body=body)
        queries = payload.get("queries")
        if isinstance(queries, list) and queries:
            results = queries[0].get("results")
            if isinstance(results, dict):
                issues = results.get("issues")
                if isinstance(issues, list):
                    return issues
        return []

    def list_projects(self, *, max_results: int = 200) -> List[Dict[str, Any]]:
        payload = self._request("GET", "/rest/api/3/project/search", params={"maxResults": max_results})
        values = payload.get("values")
        if isinstance(values, list):
            return values
        # Older APIs may return the projects list directly.
        projects = payload.get("projects")
        if isinstance(projects, list):
            return projects
        return []

    def build_issue_url(self, key: str) -> str:
        return f"{self.credentials.base_url}/browse/{key}"

    def _basic_token(self) -> str:
        creds = self.credentials
        token = f"{creds.email}:{creds.api_token}".encode("utf-8")
        return base64.b64encode(token).decode("ascii")

    def _request(
        self,
        method: str,
        path: str,
        *,
        params: Optional[Dict[str, Any]] = None,
        json_body: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        url = f"{self.credentials.base_url}{path}"
        headers = {
            "Authorization": f"Basic {self._basic_token()}",
            "Accept": "application/json",
        }
        if json_body is not None:
            headers["Content-Type"] = "application/json"
        if path.startswith("/rest/api/3/search/jql"):
            headers["X-ExperimentalApi"] = "opt-in"
        response = requests.request(
            method,
            url,
            headers=headers,
            params=params,
            json=json_body,
            timeout=self.timeout,
        )
        if self._logger:
            try:
                self._logger(
                    method,
                    path,
                    params if params else None,
                    json_body if json_body else None,
                    response.status_code,
                    response.text[:2000],
                )
            except Exception:
                pass
        if response.status_code >= 400:
            snippet = response.text[:400] if response.text else response.reason
            raise JiraApiError(response.status_code, f"{response.reason}: {snippet}")
        if not response.content:
            return {}
        try:
            return response.json()
        except ValueError:
            return {}

    @staticmethod
    def extract_plain_text(value: Any) -> str:
        """Extract plain text from Jira's description fields."""
        if isinstance(value, str):
            return value
        if isinstance(value, dict):
            return JiraClient._extract_text_from_node(value).strip()
        if isinstance(value, list):
            parts = [JiraClient.extract_plain_text(item) for item in value]
            return " ".join(part for part in parts if part)
        return ""

    @staticmethod
    def _extract_text_from_node(node: Any) -> str:
        if isinstance(node, str):
            return node
        if not isinstance(node, dict):
            return ""
        node_type = node.get("type")
        if node_type == "text":
            return node.get("text", "")
        content = node.get("content")
        if isinstance(content, list):
            return " ".join(JiraClient._extract_text_from_node(child) for child in content)
        return ""


__all__ = ["JiraClient", "JiraCredentials", "JiraApiError"]
