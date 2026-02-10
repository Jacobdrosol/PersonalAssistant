from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Callable, Iterable, Optional, Sequence, TYPE_CHECKING

from .plugins import EmailIngestManager
from .ui.views.email_ingest import EmailIngestView
from .ui.views.export_validator import ExportValidatorView
from .ui.views.jira_tab import JiraTabView
from .ui.views.knowledge_bank import KnowledgeBankView
from .ui.views.production_log import ProductionLogView
from .ui.views.select_builder import SelectBuilderView
from .ui.views.sql_builder import SqlBuilderView
from .ui.views.sql_assist import SqlAssistView
from .issue_calendar_tab import IssueCalendarTab

if TYPE_CHECKING:
    from .app import PersonalAssistantApp


@dataclass(frozen=True)
class SpecialFeature:
    key: str
    title: str
    description: str
    tab_label: Optional[str] = None
    insert_after: Optional[str] = None
    tab_builder: Optional[Callable[["PersonalAssistantApp"], object]] = None

    def is_tab_feature(self) -> bool:
        return self.tab_label is not None and self.tab_builder is not None


def _build_sql_assist(app: "PersonalAssistantApp") -> object:
    return SqlAssistView(app.notebook, app.db)


def _build_jira(app: "PersonalAssistantApp") -> object:
    return JiraTabView(
        app.notebook,
        service=app.jira_service,
        theme=app.theme,
        open_settings=app._show_settings_view,
    )


def _build_email_ingest(app: "PersonalAssistantApp") -> object:
    manager = EmailIngestManager(app.data_root)
    return EmailIngestView(app.notebook, manager)


def _build_issue_calendar(app: "PersonalAssistantApp") -> object:
    return IssueCalendarTab(app.notebook, app.db, app.theme)


def _build_production_log(app: "PersonalAssistantApp") -> object:
    return ProductionLogView(app.notebook, app.db, app.theme)


def _build_sql_builder(app: "PersonalAssistantApp") -> object:
    return SqlBuilderView(app.notebook)


def _build_select_builder(app: "PersonalAssistantApp") -> object:
    return SelectBuilderView(app.notebook)


def _build_export_validator(app: "PersonalAssistantApp") -> object:
    return ExportValidatorView(app.notebook)


def _build_knowledge_bank(app: "PersonalAssistantApp") -> object:
    return KnowledgeBankView(app.notebook)


SPECIAL_FEATURES: dict[str, SpecialFeature] = {
    "sql_assist": SpecialFeature(
        key="sql_assist",
        title="SQL Assist",
        description="Advanced workspace for SQL tables, data sources, and saved queries.",
        tab_label="SQL Assist",
        insert_after="scrum",
        tab_builder=_build_sql_assist,
    ),
    "jira": SpecialFeature(
        key="jira",
        title="JIRA Integration",
        description="Sync and review Jira issues inside the assistant.",
        tab_label="JIRA",
        insert_after="scrum",
        tab_builder=_build_jira,
    ),
    "email_ingest": SpecialFeature(
        key="email_ingest",
        title="Email Ingest",
        description="Ingest Outlook emails into searchable shards with summaries.",
        tab_label="Email Ingest",
        insert_after="scrum",
        tab_builder=_build_email_ingest,
    ),
    "issue_calendar": SpecialFeature(
        key="issue_calendar",
        title="Issue Calendar",
        description="Read-only calendar view for imported issue schedules with shared notes.",
        tab_label="Issue Calendar",
        insert_after="calendar",
        tab_builder=_build_issue_calendar,
    ),
    "production_log": SpecialFeature(
        key="production_log",
        title="Production Log",
        description=(
            "For tracking production counts, updating the client's log spreadsheet, "
            "and keeping consistent formatting of the spreadsheet."
        ),
        tab_label="Production Log",
        insert_after="issue_calendar",
        tab_builder=_build_production_log,
    ),
    "sql_builder": SpecialFeature(
        key="sql_builder",
        title="SQL Builder",
        description="AI tailored assistant to assist and build SQL queries.",
        tab_label="SQL Builder",
        insert_after="sql_assist",
        tab_builder=_build_sql_builder,
    ),
    "select_builder": SpecialFeature(
        key="select_builder",
        title="Select Builder",
        description=(
            "An assisted build tool for designed Selections based on criteria commonly found in the base "
            "system. To be used as an xml exporter tool, or, to be copied over into the system so that you "
            "may validate the criteria quickly."
        ),
        tab_label="Select Builder",
        insert_after="sql_builder",
        tab_builder=_build_select_builder,
    ),
    "export_validator": SpecialFeature(
        key="export_validator",
        title="Export Validator",
        description=(
            "A validation tool where configurations may be loaded in for a production instance via "
            "uploading the instance's xml, these configurations may be updated at any time, validate "
            "exported configurations against the configurations stored in the database for a production "
            "instance."
        ),
        tab_label="Export Validator",
        insert_after="select_builder",
        tab_builder=_build_export_validator,
    ),
    "knowledge_bank": SpecialFeature(
        key="knowledge_bank",
        title="Knowledge Bank",
        description="Documents and knowledge center to assist with work.",
        tab_label="Knowledge Bank",
        insert_after="export_validator",
        tab_builder=_build_knowledge_bank,
    ),
}

SPECIAL_UNLOCK_CODES: dict[str, Sequence[str]] = {
    "4927": ("sql_assist",),
    "7314": ("jira",),
    "8642": ("email_ingest",),
    "5398": ("issue_calendar",),
    "4826": ("production_log",),
    "6074": ("sql_builder",),
    "2758": ("select_builder",),
    "9136": ("export_validator",),
    "4183": ("knowledge_bank",),
}


def normalize_special_code(value: str) -> str:
    if not value:
        return ""
    return re.sub(r"\s+", "", value).strip()


def resolve_feature_keys_for_code(code: str) -> list[str]:
    normalized = normalize_special_code(code)
    if not normalized:
        return []
    keys = SPECIAL_UNLOCK_CODES.get(normalized, ())
    return [key for key in keys if key in SPECIAL_FEATURES]


def sanitize_special_feature_keys(keys: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    cleaned: list[str] = []
    for key in keys:
        if not isinstance(key, str):
            continue
        trimmed = key.strip()
        if not trimmed or trimmed in seen:
            continue
        if trimmed not in SPECIAL_FEATURES:
            continue
        cleaned.append(trimmed)
        seen.add(trimmed)
    return cleaned


def describe_special_features(keys: Iterable[str]) -> list[SpecialFeature]:
    return [SPECIAL_FEATURES[key] for key in keys if key in SPECIAL_FEATURES]
