from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import hashlib
import math
import json
import sqlite3
import subprocess
import sys
import threading
from pathlib import Path
from typing import Callable, Dict, Iterable, List, Optional, Sequence, Tuple

try:  # COM initialization helpers
    import pythoncom  # type: ignore
except ImportError:  # pragma: no cover - handled at runtime
    pythoncom = None  # type: ignore

try:  # Optional dependency for YAML persistence
    import yaml  # type: ignore
except ImportError:  # pragma: no cover - handled at runtime
    yaml = None  # type: ignore

try:  # Optional dependency for Outlook automation
    import win32com.client  # type: ignore
except ImportError:  # pragma: no cover - handled at runtime
    win32com = None  # type: ignore

OUTLOOK_MAIL_ITEM = 0


@dataclass(slots=True)
class EmailRunConfig:
    run_id: str
    description: str
    include_folders: List[str]
    include_subfolders: bool
    summarize_after_ingest: bool
    shard_dir: Path
    summaries_dir: Path
    model: str = "t5-small"
    last_ingested: Optional[datetime] = None
    overwrite: bool = False
    next_shard_label: Optional[str] = None
    profile_name: Optional[str] = None

    @property
    def config_path(self) -> Path:
        return self.shard_dir.parent / "config.yaml"

    @property
    def run_dir(self) -> Path:
        return self.shard_dir.parent

    def to_dict(self) -> Dict[str, object]:
        payload: Dict[str, object] = {
            "run_id": self.run_id,
            "description": self.description,
            "include_folders": self.include_folders,
            "include_subfolders": self.include_subfolders,
            "summarize_after_ingest": self.summarize_after_ingest,
            "shard_path": str(self.shard_dir),
            "summaries_path": str(self.summaries_dir),
            "model": self.model,
            "overwrite": self.overwrite,
        }
        if self.last_ingested is not None:
            payload["last_ingested"] = self.last_ingested.replace(microsecond=0).isoformat()
        if self.next_shard_label:
            payload["next_shard_label"] = self.next_shard_label
        if self.profile_name:
            payload["profile_name"] = self.profile_name
        return payload

    @classmethod
    def from_dict(cls, base_dir: Path, data: Dict[str, object]) -> "EmailRunConfig":
        run_id = str(data.get("run_id", "")).strip() or "run"
        shard_path = Path(data.get("shard_path", base_dir / run_id / "shards")).expanduser()
        summaries_path = Path(data.get("summaries_path", base_dir / run_id / "summaries")).expanduser()
        last_ingested_raw = data.get("last_ingested")
        last_ingested = None
        if isinstance(last_ingested_raw, str) and last_ingested_raw:
            try:
                last_ingested = datetime.fromisoformat(last_ingested_raw)
            except ValueError:
                last_ingested = None
        next_shard_label = str(data.get("next_shard_label", "")).strip() or None
        profile_name = str(data.get("profile_name", "")).strip() or None
        return cls(
            run_id=run_id,
            description=str(data.get("description", "")),
            include_folders=[str(item) for item in (data.get("include_folders") or [])],
            include_subfolders=bool(data.get("include_subfolders", False)),
            summarize_after_ingest=bool(data.get("summarize_after_ingest", False)),
            shard_dir=shard_path,
            summaries_dir=summaries_path,
            model=str(data.get("model", "t5-small")),
            last_ingested=last_ingested,
            overwrite=bool(data.get("overwrite", False)),
            next_shard_label=next_shard_label,
            profile_name=profile_name,
        )


@dataclass(slots=True)
class EmailRecord:
    entry_id: str
    hash_id: str
    thread_id: str
    folder_path: str
    subject: str
    sender: str
    recipients: str
    received_time: datetime
    body: str
    summary: str = ""


@dataclass(slots=True)
class EmailIngestResult:
    inserted: int
    summarized: int
    shard_path: Path
    summary_path: Optional[Path]
    newest_timestamp: Optional[datetime]
    cancelled: bool = False
    brief_summary: Optional[str] = None
    report_path: Optional[Path] = None
    run_token: str = ""
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None


@dataclass(slots=True)
class DependencyReport:
    available: bool
    missing: Sequence[str] = field(default_factory=list)
    install_command: Sequence[str] = field(default_factory=tuple)


class ConfigPersistenceError(RuntimeError):
    """Raised when a configuration cannot be loaded or saved."""


class OutlookUnavailableError(RuntimeError):
    """Raised when Outlook COM automation is not available."""


class SummarizerUnavailableError(RuntimeError):
    """Raised when summarization dependencies are missing."""


class ConfigStore:
    def __init__(self, base_path: Path) -> None:
        self.base_path = base_path
        self.base_path.mkdir(parents=True, exist_ok=True)

    def list_configs(self) -> List[EmailRunConfig]:
        configs: List[EmailRunConfig] = []
        for run_dir in sorted(self.base_path.iterdir()):
            if not run_dir.is_dir():
                continue
            config_path = run_dir / "config.yaml"
            if not config_path.exists():
                continue
            try:
                configs.append(self.load_config(run_dir.name))
            except ConfigPersistenceError:
                continue
        return configs

    def load_config(self, run_id: str) -> EmailRunConfig:
        run_dir = self.base_path / run_id
        config_path = run_dir / "config.yaml"
        if not config_path.exists():
            raise ConfigPersistenceError(f"No configuration found for {run_id!r}")
        if yaml is None:
            raise ConfigPersistenceError("PyYAML is required to load configurations.")
        try:
            data = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
        except Exception as exc:  # pragma: no cover - file errors
            raise ConfigPersistenceError(str(exc)) from exc
        shard_dir = run_dir / "shards"
        summaries_dir = run_dir / "summaries"
        payload = dict(data)
        payload.setdefault("shard_path", str(shard_dir))
        payload.setdefault("summaries_path", str(summaries_dir))
        return EmailRunConfig.from_dict(self.base_path, payload)

    def save_config(self, config: EmailRunConfig) -> None:
        if yaml is None:
            raise ConfigPersistenceError("PyYAML is required to save configurations.")
        config.run_dir.mkdir(parents=True, exist_ok=True)
        config.shard_dir.mkdir(parents=True, exist_ok=True)
        config.summaries_dir.mkdir(parents=True, exist_ok=True)
        payload = config.to_dict()
        try:
            config.config_path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")
        except Exception as exc:  # pragma: no cover - filesystem errors
            raise ConfigPersistenceError(str(exc)) from exc


class DependencyInspector:
    SUMMARY_PACKAGES = ("transformers", "torch")

    @staticmethod
    def check_summary_dependencies() -> DependencyReport:
        missing: List[str] = []
        for package in DependencyInspector.SUMMARY_PACKAGES:
            if not DependencyInspector._module_exists(package):
                missing.append(package)
        command: Tuple[str, ...] = ()
        if missing:
            command = (sys.executable, "-m", "pip", "install", "--user", *missing)
        return DependencyReport(available=not missing, missing=missing, install_command=command)

    @staticmethod
    def _module_exists(name: str) -> bool:
        try:
            __import__(name)
            return True
        except ImportError:
            return False

    @staticmethod
    def install_missing(packages: Sequence[str], observer: Optional[Callable[[str], None]] = None) -> int:
        if not packages:
            return 0
        command = [sys.executable, "-m", "pip", "install", "--user", *packages]
        process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        assert process.stdout is not None
        for line in process.stdout:
            if observer:
                observer(line.rstrip())
        return process.wait()


class OutlookClient:
    def __init__(self, profile_name: Optional[str] = None) -> None:
        if win32com is None:
            raise OutlookUnavailableError(
                "pywin32 is required to access Outlook. Install it with 'pip install pywin32'."
            )
        try:
            try:
                self._app = win32com.client.Dispatch("Outlook.Application")
            except Exception:
                self._app = win32com.client.DispatchEx("Outlook.Application")
            self._namespace = self._app.GetNamespace("MAPI")
        except Exception as exc:  # pragma: no cover - COM specific
            raise OutlookUnavailableError(f"Failed to initialise Outlook COM: {exc}") from exc
        self._profile_name = profile_name
        self._logged_on = False
        self._ensure_logon()

    def close(self) -> None:
        if not self._logged_on:
            return
        try:
            self._namespace.Logoff()
        except Exception:
            pass
        self._logged_on = False

    def _ensure_logon(self) -> None:
        if self._logged_on:
            return
        try:
            if self._profile_name:
                self._namespace.Logon(self._profile_name, "", False, False)
            else:
                self._namespace.Logon("", "", False, False)
        except Exception as exc:
            raise OutlookUnavailableError(str(exc)) from exc
        self._logged_on = True

    def list_folders(self, reporter: Optional[Callable[[str], None]] = None) -> List[str]:
        self._ensure_logon()
        paths: List[str] = []
        for store in self._namespace.Folders:
            name = getattr(store, "Name", "(unknown store)")
            if reporter:
                reporter(f"Enumerating store: {name}")
            if name.lower().startswith("public folders"):
                if reporter:
                    reporter(f"Skipping store: {name}")
                continue
            self._walk_folder(store, name, paths, reporter)
        return paths

    def _walk_folder(self, folder, prefix: str, sink: List[str], reporter: Optional[Callable[[str], None]] = None) -> None:
        sink.append(prefix)
        if reporter and len(sink) % 50 == 0:
            reporter(f"... {len(sink)} folders collected")
        try:
            children = folder.Folders
        except Exception:
            return
        for child in children:
            child_name = getattr(child, "Name", "(unnamed)")
            self._walk_folder(child, f"{prefix}/{child_name}", sink, reporter)

    def iter_messages(
        self,
        folder_path: str,
        include_subfolders: bool,
        since: Optional[datetime],
    ) -> Iterable[EmailRecord]:
        self._ensure_logon()
        folder = self._resolve_folder(folder_path)
        if folder is None:
            return []
        yield from self._iter_messages_in_folder(folder, folder_path, include_subfolders, since)

    def _iter_messages_in_folder(
        self,
        folder,
        folder_path: str,
        include_subfolders: bool,
        since: Optional[datetime],
    ) -> Iterable[EmailRecord]:
        items = folder.Items
        items.Sort("[ReceivedTime]")
        for item in items:
            if getattr(item, "Class", None) != 43:  # 43 = olMail
                continue
            received = self._to_datetime(getattr(item, "ReceivedTime", None))
            if since and received and received <= since:
                continue
            body = getattr(item, "Body", "") or ""
            subject = getattr(item, "Subject", "") or ""
            sender = getattr(item, "SenderEmailAddress", "") or ""
            to_line = getattr(item, "To", "") or ""
            cc_line = getattr(item, "CC", "") or ""
            recipients = ", ".join(p for p in [to_line, cc_line] if p)
            entry_id = str(getattr(item, "EntryID", ""))
            thread_id = str(getattr(item, "ConversationID", ""))
            if not entry_id:
                continue
            hash_id = hashlib.sha256((subject + "\n" + body).encode("utf-8", errors="ignore")).hexdigest()
            record = EmailRecord(
                entry_id=entry_id,
                hash_id=hash_id,
                thread_id=thread_id,
                folder_path=folder_path,
                subject=subject,
                sender=sender,
                recipients=recipients,
                received_time=received or datetime.now(timezone.utc),
                body=body,
            )
            yield record
        if include_subfolders:
            for child in folder.Folders:
                child_path = f"{folder_path}/{child.Name}"
                yield from self._iter_messages_in_folder(child, child_path, True, since)

    def _resolve_folder(self, path: str):
        parts = [part for part in path.split("/") if part]
        if not parts:
            return None
        folder = None
        try:
            folder = self._namespace.Folders.Item(parts[0])
            for part in parts[1:]:
                folder = folder.Folders.Item(part)
            return folder
        except Exception:
            return None

    @staticmethod
    def _to_datetime(raw) -> Optional[datetime]:
        if raw is None:
            return None
        if isinstance(raw, datetime):
            return raw
        try:
            return datetime.fromtimestamp(float(raw))
        except Exception:
            return None



class ShardStore:
    def __init__(self, path: Path, run_id: str) -> None:
        self.path = path
        self.run_id = run_id
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.path)
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA synchronous=NORMAL")
        self._ensure_schema()

    def close(self) -> None:
        self.conn.close()

    def _ensure_schema(self) -> None:
        self.conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS emails (
                id TEXT PRIMARY KEY,
                thread_id TEXT,
                folder TEXT,
                subject TEXT,
                sender TEXT,
                recipients TEXT,
                received_time TEXT,
                content TEXT,
                summary TEXT,
                hash TEXT NOT NULL,
                run_id TEXT NOT NULL
            );

            CREATE UNIQUE INDEX IF NOT EXISTS idx_emails_hash ON emails(hash);

            CREATE TABLE IF NOT EXISTS emails_fts (
                rowid INTEGER PRIMARY KEY,
                subject TEXT,
                content TEXT,
                summary TEXT
            );

            CREATE UNIQUE INDEX IF NOT EXISTS idx_emails_fts_rowid ON emails_fts(rowid);
            """
        )
        self.conn.commit()

    def load_known_keys(self) -> Tuple[set[str], set[str]]:
        known_ids = {row[0] for row in self.conn.execute("SELECT id FROM emails")}
        known_hashes = {row[0] for row in self.conn.execute("SELECT hash FROM emails")}
        return known_ids, known_hashes

    def insert_records(self, records: Sequence[EmailRecord]) -> List[EmailRecord]:
        known_ids, known_hashes = self.load_known_keys()
        inserted: List[EmailRecord] = []
        for record in records:
            if record.entry_id in known_ids or record.hash_id in known_hashes:
                continue
            self.conn.execute(
                """
                INSERT INTO emails (id, thread_id, folder, subject, sender, recipients, received_time, content, summary, hash, run_id)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record.entry_id,
                    record.thread_id,
                    record.folder_path,
                    record.subject,
                    record.sender,
                    record.recipients,
                    record.received_time.replace(microsecond=0).isoformat(),
                    record.body,
                    record.summary,
                    record.hash_id,
                    self.run_id,
                ),
            )
            rowid = self.conn.execute("SELECT rowid FROM emails WHERE id = ?", (record.entry_id,)).fetchone()[0]
            self.conn.execute(
                "INSERT INTO emails_fts(rowid, subject, content, summary) VALUES (?, ?, ?, ?)",
                (rowid, record.subject, record.body, record.summary),
            )
            known_ids.add(record.entry_id)
            known_hashes.add(record.hash_id)
            inserted.append(record)
        self.conn.commit()
        return inserted

    def update_summaries(self, summaries: Dict[str, str]) -> None:
        for entry_id, summary in summaries.items():
            self.conn.execute("UPDATE emails SET summary = ? WHERE id = ?", (summary, entry_id))
            row = self.conn.execute("SELECT rowid, subject, content FROM emails WHERE id = ?", (entry_id,)).fetchone()
            if row:
                rowid, subject, content = row
                existing = self.conn.execute(
                    "SELECT 1 FROM emails_fts WHERE rowid = ?",
                    (rowid,),
                ).fetchone()
                if existing:
                    self.conn.execute(
                        "UPDATE emails_fts SET subject = ?, content = ?, summary = ? WHERE rowid = ?",
                        (subject, content, summary, rowid),
                    )
                else:
                    self.conn.execute(
                        "INSERT INTO emails_fts(rowid, subject, content, summary) VALUES (?, ?, ?, ?)",
                        (rowid, subject, content, summary),
                    )
        self.conn.commit()


class SummarizationEngine:
    def __init__(self, model_name: str = "t5-small") -> None:
        self.model_name = model_name
        self._pipeline = None
        self.report = DependencyInspector.check_summary_dependencies()

    @property
    def available(self) -> bool:
        return self.report.available

    def summarize(
        self,
        records: Sequence[EmailRecord],
        *,
        progress: Optional[Callable[[str], None]] = None,
        cancel_event: Optional[threading.Event] = None,
    ) -> tuple[Dict[str, str], bool]:
        if not records:
            return {}, False
        if not self.available:
            raise SummarizerUnavailableError(
                "transformers/torch are required for summarization. Please install the missing packages."
            )
        pipeline = self._ensure_pipeline()
        summaries: Dict[str, str] = {}
        batch: List[EmailRecord] = []
        total = len(records)
        batches = math.ceil(total / 10)
        batch_index = 0
        cancelled = False
        for record in records:
            if cancel_event and cancel_event.is_set():
                cancelled = True
                break
            batch.append(record)
            if len(batch) == 10:
                batch_index += 1
                summaries.update(
                    self._summarize_batch(
                        pipeline,
                        batch,
                        batch_index=batch_index,
                        total_batches=batches,
                        progress=progress,
                        cancel_event=cancel_event,
                    )
                )
                batch.clear()
                if cancel_event and cancel_event.is_set():
                    cancelled = True
                    break
        if not cancelled and batch:
            batch_index += 1
            summaries.update(
                self._summarize_batch(
                    pipeline,
                    batch,
                    batch_index=batch_index,
                    total_batches=batches,
                    progress=progress,
                    cancel_event=cancel_event,
                )
            )
        return summaries, cancelled or (cancel_event.is_set() if cancel_event else False)

    def _ensure_pipeline(self):
        if self._pipeline is None:
            from transformers import pipeline  # type: ignore

            self._pipeline = pipeline(
                task="summarization",
                model=self.model_name,
                tokenizer=self.model_name,
                framework="pt",
            )
        return self._pipeline

    def _summarize_batch(
        self,
        pipeline,
        batch: Sequence[EmailRecord],
        *,
        batch_index: int,
        total_batches: int,
        progress: Optional[Callable[[str], None]] = None,
        cancel_event: Optional[threading.Event] = None,
    ) -> Dict[str, str]:  # type: ignore[no-untyped-def]
        texts = [record.body or record.subject for record in batch]
        if progress:
            progress(f"Summarizing batch {batch_index}/{total_batches}")
        token_estimates = [max(1, len(text.split())) for text in texts]
        max_tokens = max(token_estimates) if token_estimates else 1
        dynamic_max = min(120, max(40, int(max_tokens * 1.25)))
        dynamic_min = max(20, min(dynamic_max - 5, dynamic_max // 2))
        if dynamic_min >= dynamic_max:
            dynamic_min = max(10, dynamic_max - 10)
        results = pipeline(
            texts,
            max_length=dynamic_max,
            min_length=dynamic_min,
            do_sample=False,
            truncation=True,
            max_new_tokens=None,
        )
        summaries: Dict[str, str] = {}
        for record, result in zip(batch, results):
            summary_text = result.get("summary_text") if isinstance(result, dict) else None
            summaries[record.entry_id] = summary_text.strip() if summary_text else ""
        return summaries

    @staticmethod
    def build_summary_document(records: Sequence[EmailRecord], summaries: Dict[str, str]) -> str:
        lines: List[str] = []
        for record in sorted(records, key=lambda r: r.received_time):
            header = record.received_time.strftime("%Y-%m-%d %H:%M")
            lines.append(f"[{header}] {record.subject}  -  {record.sender}")
            summary = summaries.get(record.entry_id, "")
            if summary:
                lines.append(summary)
            lines.append("")
        return "\n".join(lines).strip() + "\n"

    def generate_brief_summary(
        self,
        records: Sequence[EmailRecord],
        summaries: Dict[str, str],
        *,
        progress: Optional[Callable[[str], None]] = None,
        cancel_event: Optional[threading.Event] = None,
    ) -> str:
        if not records:
            return "No new emails were ingested during this run."
        if cancel_event and cancel_event.is_set():
            return "Run was cancelled before a briefing summary was generated."
        segments: List[str] = []
        for record in records:
            snippet = summaries.get(record.entry_id)
            if not snippet:
                body = (record.body or record.subject or "").strip()
                snippet = body[:260] + ("..." if len(body) > 260 else "")
            segments.append(
                f"Email from {record.sender or 'unknown sender'} about {record.subject or 'no subject'}: {snippet}"
            )
        combined = "\n".join(segments)
        if progress:
            progress("Generating briefing summary")
        pipeline = self._ensure_pipeline()
        total_tokens = max(1, len(combined.split()))
        max_len = min(140, max(60, total_tokens // 4))
        min_len = max(40, min(max_len - 10, total_tokens // 6))
        result = pipeline(
            [combined],
            max_length=max_len,
            min_length=min_len,
            do_sample=False,
            truncation=True,
            max_new_tokens=None,
        )
        brief = ""
        if isinstance(result, list) and result:
            brief = result[0].get("summary_text", "")
        return brief.strip() or combined[:500]


class EmailIngestManager:
    def __init__(self, base_path: Path) -> None:
        self.base_path = base_path
        self.base_path.mkdir(parents=True, exist_ok=True)
        self.store = ConfigStore(base_path / "email_runs")
        self._cancel_event = threading.Event()

    def _log_debug(self, message: str, progress: Optional[Callable[[str], None]] = None) -> None:
        if progress:
            progress(message)

    def list_configs(self) -> List[EmailRunConfig]:
        return self.store.list_configs()

    def load_config(self, run_id: str) -> EmailRunConfig:
        return self.store.load_config(run_id)

    def save_config(self, config: EmailRunConfig) -> None:
        self.store.save_config(config)

    def create_default_config(self, run_id: str) -> EmailRunConfig:
        run_id = run_id.strip() or "run"
        run_dir = self.store.base_path / run_id
        shard_dir = run_dir / "shards"
        summaries_dir = run_dir / "summaries"
        default_label = datetime.now().strftime("%Y-%m")
        return EmailRunConfig(
            run_id=run_id,
            description="",
            include_folders=[],
            include_subfolders=True,
            summarize_after_ingest=True,
            shard_dir=shard_dir,
            summaries_dir=summaries_dir,
            model="t5-small",
            last_ingested=None,
            overwrite=False,
            next_shard_label=default_label,
            profile_name=None,
        )

    def dependency_report(self) -> DependencyReport:
        return DependencyInspector.check_summary_dependencies()

    def install_dependencies(self, observer: Optional[Callable[[str], None]] = None) -> int:
        report = self.dependency_report()
        if report.available:
            if observer:
                observer("Dependencies already satisfied.")
            return 0
        if observer:
            observer(f"Installing: {', '.join(report.missing)}")
        return DependencyInspector.install_missing(report.missing, observer)

    def list_outlook_folders(self, profile_name: Optional[str] = None, progress: Optional[Callable[[str], None]] = None) -> List[str]:
        self._log_debug('CoInitialize start', progress)
        if pythoncom is not None:
            pythoncom.CoInitialize()
        client: Optional[OutlookClient] = None
        try:
            self._log_debug('Creating Outlook client for folder list', progress)
            client = OutlookClient(profile_name=profile_name)
            self._log_debug('Outlook client created; enumerating folders', progress)
            folders = client.list_folders(reporter=progress)
            self._log_debug(f'Got {len(folders)} folders from Outlook', progress)
            return folders
        finally:
            if client is not None:
                client.close()
            if pythoncom is not None:
                pythoncom.CoUninitialize()
            self._log_debug('CoUninitialize complete', progress)

    def cancel_current_run(self) -> None:
        self._cancel_event.set()


    def run_now(
        self,
        config: EmailRunConfig,
        progress: Optional[Callable[[str], None]] = None,
    ) -> EmailIngestResult:
        self._cancel_event.clear()
        started_at = datetime.now()
        run_token = started_at.strftime("%Y-%m-%d_%H%M%S")
        self._log_debug('Run requested', progress)
        if pythoncom is not None:
            pythoncom.CoInitialize()
        client: Optional[OutlookClient] = None
        inserted_records: List[EmailRecord] = []
        summaries: Dict[str, str] = {}
        summary_path: Optional[Path] = None
        brief_summary: Optional[str] = None
        summarized_count = 0
        cancelled = False
        newest_timestamp = config.last_ingested
        try:
            shard_file = self._determine_shard_file(config)
            if progress:
                progress(f"Target shard -> {shard_file}")
            self._log_debug('Creating Outlook client for run', progress)
            client = OutlookClient(profile_name=config.profile_name)
            self._log_debug('Enumerating messages for run', progress)
            records: List[EmailRecord] = []
            for folder_path in config.include_folders:
                if self._cancel_event.is_set():
                    cancelled = True
                    self._log_debug('Run cancelled before folder enumeration completed', progress)
                    break
                if progress:
                    progress(f"Scanning folder: {folder_path}")
                records.extend(
                    list(client.iter_messages(folder_path, config.include_subfolders, config.last_ingested))
                )
            if not cancelled:
                records.sort(key=lambda record: record.received_time)
                shard = ShardStore(shard_file, config.run_id)
                try:
                    if self._cancel_event.is_set():
                        cancelled = True
                    else:
                        inserted_records = shard.insert_records(records)
                        if inserted_records:
                            newest_timestamp = max(record.received_time for record in inserted_records)
                        if inserted_records and config.summarize_after_ingest and not self._cancel_event.is_set():
                            summarizer = SummarizationEngine(config.model)
                            if summarizer.available:
                                self._log_debug('Running summarizer', progress)
                                summaries, summary_cancelled = summarizer.summarize(
                                    inserted_records,
                                    progress=progress,
                                    cancel_event=self._cancel_event,
                                )
                                cancelled = cancelled or summary_cancelled
                                if summaries:
                                    shard.update_summaries(summaries)
                                    summary_doc = SummarizationEngine.build_summary_document(inserted_records, summaries)
                                    summary_path = self._write_summary(config.summaries_dir, summary_doc)
                                    summarized_count = len([entry for entry in summaries.values() if entry])
                                    if not cancelled:
                                        brief_summary = summarizer.generate_brief_summary(
                                            inserted_records,
                                            summaries,
                                            progress=progress,
                                            cancel_event=self._cancel_event,
                                        )
                            else:
                                if progress:
                                    progress(
                                        "Summarization skipped: transformers/torch missing. Use 'Install Dependencies' to enable."
                                    )
                        elif not inserted_records:
                            brief_summary = "No new emails were ingested during this run."
                finally:
                    shard.close()
        finally:
            if client is not None:
                client.close()
            if pythoncom is not None:
                pythoncom.CoUninitialize()
            self._log_debug('Run completed; COM released', progress)
        if brief_summary is None:
            brief_summary = "Run was cancelled before a briefing summary was generated." if cancelled else "Summary generation skipped."
        if not cancelled and newest_timestamp and (config.last_ingested is None or newest_timestamp > config.last_ingested):
            config.last_ingested = newest_timestamp
            self.save_config(config)
        completed_at = datetime.now()
        report_path = self._write_run_report(
            config,
            run_token=run_token,
            started_at=started_at,
            completed_at=completed_at,
            inserted_records=inserted_records,
            summary_path=summary_path,
            brief_summary=brief_summary,
            cancelled=cancelled,
            summarized_count=summarized_count,
        )
        return EmailIngestResult(
            inserted=len(inserted_records),
            summarized=summarized_count,
            shard_path=shard_file,
            summary_path=summary_path,
            newest_timestamp=config.last_ingested,
            cancelled=cancelled,
            brief_summary=brief_summary,
            report_path=report_path,
            run_token=run_token,
            started_at=started_at,
            completed_at=completed_at,
        )
    def _determine_shard_file(self, config: EmailRunConfig) -> Path:
        now = datetime.now()
        label = config.next_shard_label or f"{now:%Y-%m}"
        shard_name = f"{label}.sqlite"
        return config.shard_dir / shard_name

    def _write_summary(self, directory: Path, payload: str) -> Path:
        directory.mkdir(parents=True, exist_ok=True)
        target = directory / f"summary_{datetime.now():%Y-%m-%d_%H%M%S}.txt"
        target.write_text(payload, encoding="utf-8")
        return target

    def _write_run_report(
        self,
        config: EmailRunConfig,
        *,
        run_token: str,
        started_at: datetime,
        completed_at: datetime,
        inserted_records: Sequence[EmailRecord],
        summary_path: Optional[Path],
        brief_summary: str,
        cancelled: bool,
        summarized_count: int,
    ) -> Path:
        report_dir = config.run_dir / "runs"
        report_dir.mkdir(parents=True, exist_ok=True)
        inserted_payload = [
            {
                "id": record.entry_id,
                "subject": record.subject,
                "sender": record.sender,
                "folder": record.folder_path,
                "received_time": record.received_time.replace(microsecond=0).isoformat(),
            }
            for record in inserted_records
        ]
        payload = {
            "run_token": run_token,
            "config_id": config.run_id,
            "profile_name": config.profile_name,
            "include_folders": config.include_folders,
            "include_subfolders": config.include_subfolders,
            "started_at": started_at.replace(microsecond=0).isoformat(),
            "completed_at": completed_at.replace(microsecond=0).isoformat(),
            "cancelled": cancelled,
            "inserted_count": len(inserted_records),
            "summarized_count": summarized_count,
            "summary_path": str(summary_path) if summary_path else None,
            "brief_summary": brief_summary,
            "inserted_records": inserted_payload,
        }
        report_path = report_dir / f"run_{run_token}.json"
        report_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        return report_path


