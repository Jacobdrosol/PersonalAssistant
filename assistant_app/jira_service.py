from __future__ import annotations

import json
import re
from datetime import datetime, date
from pathlib import Path
from threading import RLock
from typing import Callable, Dict, List, Optional

from .jira_client import JiraApiError, JiraClient
from .models import JiraIssue, JiraProject
from .settings_store import JiraSettings


class JiraServiceError(Exception):
    """Raised when the Jira service cannot complete a request."""


class JiraService:
    def __init__(
        self,
        settings_provider: Callable[[], JiraSettings],
        *,
        debug_log_path: Optional[Path] = None,
    ) -> None:
        self._settings_provider = settings_provider
        self._lock = RLock()
        self._issues: List[JiraIssue] = []
        self._projects: List[JiraProject] = []
        self._last_sync: Optional[datetime] = None
        self._log_path = debug_log_path
        if self._log_path is not None:
            try:
                self._log_path.parent.mkdir(parents=True, exist_ok=True)
            except Exception:
                self._log_path = None

    def is_configured(self) -> bool:
        settings = self._settings_provider()
        return bool(settings.base_url and settings.email and settings.api_token)

    def last_sync(self) -> Optional[datetime]:
        with self._lock:
            return self._last_sync

    def get_cached_issues(self) -> List[JiraIssue]:
        with self._lock:
            return list(self._issues)

    def get_cached_projects(self) -> List[JiraProject]:
        with self._lock:
            return list(self._projects)

    def debug_log_path(self) -> Optional[Path]:
        return self._log_path

    def refresh(self) -> tuple[List[JiraIssue], List[JiraProject]]:
        settings = self._settings_provider()
        if not settings.base_url or not settings.email or not settings.api_token:
            raise JiraServiceError("Jira integration is not configured.")
        client = JiraClient.from_settings(
            settings,
            timeout=15.0,
            logger=self._log_request if self._log_path is not None else None,
        )
        fields = [
            "summary",
            "status",
            "priority",
            "project",
            "assignee",
            "reporter",
            "duedate",
            "issuetype",
            "updated",
            "created",
            "description",
        ]
        try:
            assigned_payload = client.search_issues(
                "assignee = currentUser() AND resolution = Unresolved ORDER BY updated DESC",
                fields=fields,
                max_results=100,
            )
            watched_payload = client.search_issues(
                "issuekey in watchedIssues() AND resolution = Unresolved ORDER BY updated DESC",
                fields=fields,
                max_results=100,
            )
            project_payload = client.list_projects()
        except JiraApiError as exc:
            raise JiraServiceError(f"{exc.status_code}: {exc.message}") from exc
        except Exception as exc:  # pragma: no cover - catch-all for network errors
            raise JiraServiceError(str(exc)) from exc

        issues = self._merge_payloads(assigned_payload, watched_payload, client)
        projects = self._parse_projects(project_payload)
        with self._lock:
            self._issues = issues
            self._projects = projects
            self._last_sync = datetime.utcnow()
        return issues, projects

    def _log_request(
        self,
        method: str,
        path: str,
        params: Optional[Dict[str, object]],
        body: Optional[Dict[str, object]],
        status: int,
        response_text: str,
    ) -> None:
        if self._log_path is None:
            return
        record = {
            "timestamp": datetime.utcnow().isoformat(timespec="seconds") + "Z",
            "method": method,
            "path": path,
            "params": params,
            "body": body,
            "status": status,
            "response": response_text,
        }
        try:
            with self._log_path.open("a", encoding="utf-8") as handle:
                json.dump(record, handle, ensure_ascii=False)
                handle.write("\n")
        except Exception:
            pass

    def _merge_payloads(
        self,
        assigned_payload: List[dict],
        watched_payload: List[dict],
        client: JiraClient,
    ) -> List[JiraIssue]:
        issues: dict[str, JiraIssue] = {}
        for raw in assigned_payload:
            issue = self._parse_issue(raw, client, is_assigned=True, is_watched=False)
            if issue:
                issues[issue.key] = issue
        for raw in watched_payload:
            key = raw.get("key")
            if not key:
                continue
            existing = issues.get(key)
            if existing:
                existing.is_watched = True
                continue
            issue = self._parse_issue(raw, client, is_assigned=False, is_watched=True)
            if issue:
                issues[issue.key] = issue
        return sorted(
            issues.values(),
            key=lambda issue: issue.updated or datetime.min,
            reverse=True,
        )

    def _parse_issue(
        self,
        payload: dict,
        client: JiraClient,
        *,
        is_assigned: bool,
        is_watched: bool,
    ) -> Optional[JiraIssue]:
        key = payload.get("key")
        if not key:
            return None
        fields = payload.get("fields") or {}
        project = fields.get("project") or {}
        project_key = project.get("key") or ""
        project_name = project.get("name") or project_key
        status = (fields.get("status") or {}).get("name") or "Unknown"
        priority = (fields.get("priority") or {}).get("name") or "None"
        issue_type = (fields.get("issuetype") or {}).get("name") or "Issue"
        assignee = self._user_display(fields.get("assignee"))
        reporter = self._user_display(fields.get("reporter"))
        summary = fields.get("summary") or "(no summary)"
        description = JiraClient.extract_plain_text(fields.get("description"))
        updated = self._parse_datetime(fields.get("updated"))
        created = self._parse_datetime(fields.get("created"))
        due_date = self._parse_date(fields.get("duedate"))
        url = client.build_issue_url(key)
        return JiraIssue(
            key=key,
            summary=summary,
            status=status,
            priority=priority,
            issue_type=issue_type,
            project_key=project_key,
            project_name=project_name,
            updated=updated,
            created=created,
            due_date=due_date,
            assignee=assignee,
            reporter=reporter,
            description=description,
            url=url,
            is_assigned=is_assigned,
            is_watched=is_watched,
        )

    def _parse_projects(self, payload: List[dict]) -> List[JiraProject]:
        projects: List[JiraProject] = []
        for raw in payload:
            key = raw.get("key")
            name = raw.get("name")
            project_id = str(raw.get("id") or "")
            if not key or not name:
                continue
            projects.append(
                JiraProject(
                    key=key,
                    name=name,
                    project_id=project_id,
                    project_type=raw.get("projectTypeKey"),
                )
            )
        projects.sort(key=lambda project: project.name.lower())
        return projects

    @staticmethod
    def _user_display(user: Optional[dict]) -> Optional[str]:
        if not isinstance(user, dict):
            return None
        return user.get("displayName") or user.get("emailAddress")

    @staticmethod
    def _parse_datetime(value: Optional[str]) -> Optional[datetime]:
        if not value:
            return None
        normalized = _TZ_FIX_PATTERN.sub(r"\1:\2", value)
        try:
            return datetime.fromisoformat(normalized)
        except ValueError:
            return None

    @staticmethod
    def _parse_date(value: Optional[str]) -> Optional[date]:
        if not value:
            return None
        try:
            return datetime.strptime(value, "%Y-%m-%d").date()
        except ValueError:
            return None


_TZ_FIX_PATTERN = re.compile(r"([+-]\d{2})(\d{2})$")


__all__ = ["JiraService", "JiraServiceError"]
