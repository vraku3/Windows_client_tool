"""Persistent Case Notes & Investigation Casebook Workspace.

Provides persistent bookmarks, investigator notes, diagnostic tags (e.g. "Root Cause",
"Follow-up", "Benign"), re-ordering, and team brief export (Markdown / HTML) across all
diagnostic channels and log readers.
"""
from __future__ import annotations

import copy
import hashlib
import html
import json
import logging
import os
import threading
from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional, Set

from core.types import LogEntry

logger = logging.getLogger(__name__)

# Predefined standard investigation tags
TAG_ROOT_CAUSE = "Root Cause"
TAG_FOLLOW_UP = "Follow-up"
TAG_BENIGN = "Benign"
DEFAULT_TAGS = [TAG_ROOT_CAUSE, TAG_FOLLOW_UP, TAG_BENIGN]

_DEFAULT_MANAGER: Optional["CasebookManager"] = None


def compute_entry_fingerprint(entry: LogEntry) -> str:
    """Generate a stable fingerprint hash for a LogEntry across sessions and tabs."""
    if not entry:
        return ""
    raw = getattr(entry, "raw", {}) or {}
    eid = str(raw.get("event_id") or raw.get("Id") or "")
    log_name = str(raw.get("log_name") or raw.get("channel") or "")
    rec_id = str(raw.get("record_id") or "")
    ts_str = entry.timestamp.strftime("%Y-%m-%d %H:%M:%S") if getattr(entry, "timestamp", None) else ""
    source = str(entry.source or "").strip()
    level = str(entry.level or "").strip()
    msg = (entry.message or "").strip()[:200]

    key = f"{ts_str}|{source}|{level}|{eid}|{log_name}|{rec_id}|{msg}"
    return hashlib.sha256(key.encode("utf-8", errors="ignore")).hexdigest()[:16]


@dataclass
class CasebookEntry:
    """Persistent evidentiary record in the investigation casebook."""

    id: str
    fingerprint: str
    timestamp: str
    source: str
    level: str
    message: str
    channel: str = ""
    event_id: Optional[str] = None
    note: str = ""
    tags: List[str] = field(default_factory=list)
    bookmarked: bool = True
    order: int = 0
    raw: dict = field(default_factory=dict)
    created_at: str = ""
    updated_at: str = ""

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "CasebookEntry":
        raw = data.get("raw") or {}
        tags = data.get("tags") or []
        if isinstance(tags, str):
            tags = [tags]
        return cls(
            id=str(data.get("id") or ""),
            fingerprint=str(data.get("fingerprint") or ""),
            timestamp=str(data.get("timestamp") or ""),
            source=str(data.get("source") or ""),
            level=str(data.get("level") or ""),
            message=str(data.get("message") or ""),
            channel=str(data.get("channel") or ""),
            event_id=str(data.get("event_id")) if data.get("event_id") is not None else None,
            note=str(data.get("note") or ""),
            tags=list(tags),
            bookmarked=bool(data.get("bookmarked", True)),
            order=int(data.get("order") or 0),
            raw=dict(raw) if isinstance(raw, dict) else {},
            created_at=str(data.get("created_at") or ""),
            updated_at=str(data.get("updated_at") or ""),
        )

    def to_log_entry(self) -> LogEntry:
        """Convert back to a LogEntry instance for inspection in EventDetailDialog."""
        dt = datetime.now()
        if self.timestamp:
            for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d"):
                try:
                    dt = datetime.strptime(self.timestamp, fmt)
                    break
                except ValueError as ex:
                    logger.debug("Failed parsing timestamp '%s' with '%s': %s", self.timestamp, fmt, ex)
        raw_copy = dict(self.raw or {})
        if self.note:
            raw_copy["_user_note"] = self.note
        if self.tags:
            raw_copy["_user_tags"] = list(self.tags)
        if self.event_id:
            raw_copy["event_id"] = self.event_id
        if self.channel:
            raw_copy["log_name"] = self.channel
        raw_copy["_casebook_id"] = self.id
        raw_copy["_fingerprint"] = self.fingerprint
        return LogEntry(
            timestamp=dt,
            source=self.source,
            level=self.level,
            message=self.message,
            raw=raw_copy,
        )

    @classmethod
    def from_log_entry(
        cls,
        entry: LogEntry,
        note: str = "",
        tags: Optional[List[str]] = None,
        channel: str = "",
        bookmarked: bool = True,
        order: int = 0,
    ) -> "CasebookEntry":
        fp = compute_entry_fingerprint(entry)
        raw = getattr(entry, "raw", {}) or {}
        eid = str(raw.get("event_id") or raw.get("Id") or "")
        if not eid:
            eid = None
        ch = channel or str(raw.get("log_name") or raw.get("channel") or entry.source or "System")
        ts_str = entry.timestamp.strftime("%Y-%m-%d %H:%M:%S") if getattr(entry, "timestamp", None) else ""
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        existing_note = note or raw.get("_user_note", "")
        existing_tags = list(tags) if tags is not None else list(raw.get("_user_tags", []))

        # Safe copy of raw omitting ephemeral underscore keys
        clean_raw = {k: v for k, v in raw.items() if not str(k).startswith("_")}

        return cls(
            id=fp,
            fingerprint=fp,
            timestamp=ts_str,
            source=entry.source or "Unknown",
            level=entry.level or "Info",
            message=entry.message or "",
            channel=ch,
            event_id=eid,
            note=existing_note,
            tags=existing_tags,
            bookmarked=bookmarked,
            order=order,
            raw=clean_raw,
            created_at=now_str,
            updated_at=now_str,
        )


class CasebookManager:
    """Manages persistent case notes and investigation bookmarks.

    Stores entries to %APPDATA%/WindowsTweaker/investigation_notes.json (or specified path).
    Provides thread-safe access, tag assignment, reordering, and team brief export.
    """

    def __init__(self, storage_path: Optional[str] = None, auto_load: bool = True):
        self._lock = threading.RLock()
        self._entries: List[CasebookEntry] = []
        self._listeners: List[Callable[[], None]] = []

        if storage_path:
            self._storage_path = storage_path
        else:
            base_dir = os.environ.get("APPDATA") or os.path.expanduser("~")
            self._storage_path = os.path.join(base_dir, "WindowsTweaker", "investigation_notes.json")

        if auto_load:
            self.load()

    @property
    def storage_path(self) -> str:
        return self._storage_path

    def add_listener(self, callback: Callable[[], None]) -> None:
        """Register a callback invoked whenever casebook data is updated."""
        with self._lock:
            if callback not in self._listeners:
                self._listeners.append(callback)

    def remove_listener(self, callback: Callable[[], None]) -> None:
        with self._lock:
            if callback in self._listeners:
                self._listeners.remove(callback)

    def _notify_listeners(self) -> None:
        for cb in list(self._listeners):
            try:
                cb()
            except Exception as exc:
                logger.debug("Error notifying casebook listener: %s", exc)

    def load(self) -> List[CasebookEntry]:
        """Load entries from disk. Gracefully handles corrupted or missing files."""
        with self._lock:
            if not os.path.exists(self._storage_path):
                self._entries = []
                return []
            try:
                with open(self._storage_path, "r", encoding="utf-8") as f:
                    data = json.load(f)

                items = []
                if isinstance(data, dict):
                    raw_entries = data.get("entries", [])
                elif isinstance(data, list):
                    raw_entries = data
                else:
                    raw_entries = []

                for item in raw_entries:
                    if isinstance(item, dict):
                        try:
                            items.append(CasebookEntry.from_dict(item))
                        except Exception as ex:
                            logger.warning("Skipping malformed casebook entry: %s", ex)

                # Ensure order is sorted
                items.sort(key=lambda x: x.order)
                # Re-index orders sequentially
                for idx, entry in enumerate(items):
                    entry.order = idx

                self._entries = items
                return list(self._entries)
            except Exception as exc:
                logger.warning("Could not read casebook from %s: %s", self._storage_path, exc)
                self._entries = []
                return []

    def save(self) -> bool:
        """Save all entries atomically to disk."""
        with self._lock:
            try:
                dir_name = os.path.dirname(self._storage_path)
                if dir_name:
                    os.makedirs(dir_name, exist_ok=True)

                payload = {
                    "version": 1,
                    "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    "entries": [e.to_dict() for e in self._entries],
                }

                tmp_path = f"{self._storage_path}.tmp"
                with open(tmp_path, "w", encoding="utf-8") as f:
                    json.dump(payload, f, indent=2, ensure_ascii=False)

                if os.path.exists(self._storage_path):
                    os.replace(tmp_path, self._storage_path)
                else:
                    os.rename(tmp_path, self._storage_path)
                return True
            except Exception as exc:
                logger.error("Failed to save casebook to %s: %s", self._storage_path, exc)
                return False

    def get_entries(self) -> List[CasebookEntry]:
        """Return a copy of all casebook entries in current order."""
        with self._lock:
            return [copy.deepcopy(e) for e in self._entries]

    def get_entries_by_fingerprint(self) -> Dict[str, CasebookEntry]:
        """Return dictionary mapping fingerprint -> CasebookEntry."""
        with self._lock:
            return {e.fingerprint: copy.deepcopy(e) for e in self._entries if e.fingerprint}

    def get_entry(self, entry_id_or_fp: str) -> Optional[CasebookEntry]:
        with self._lock:
            for e in self._entries:
                if e.id == entry_id_or_fp or e.fingerprint == entry_id_or_fp:
                    return copy.deepcopy(e)
            return None

    def is_bookmarked(self, fingerprint: str) -> bool:
        with self._lock:
            for e in self._entries:
                if e.fingerprint == fingerprint:
                    return e.bookmarked
            return False

    def has_note(self, fingerprint: str) -> bool:
        with self._lock:
            for e in self._entries:
                if e.fingerprint == fingerprint:
                    return bool(e.note.strip())
            return False

    def add_or_update(self, entry: CasebookEntry) -> CasebookEntry:
        """Add a new entry or update an existing one matched by ID or fingerprint."""
        with self._lock:
            now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            match_idx = -1
            for idx, existing in enumerate(self._entries):
                if existing.id == entry.id or (existing.fingerprint and existing.fingerprint == entry.fingerprint):
                    match_idx = idx
                    break

            if match_idx >= 0:
                target = self._entries[match_idx]
                target.note = entry.note
                target.tags = list(entry.tags)
                target.bookmarked = entry.bookmarked
                target.updated_at = now_str
                if entry.raw:
                    target.raw = dict(entry.raw)
                if entry.message:
                    target.message = entry.message
                if entry.channel:
                    target.channel = entry.channel
                res = copy.deepcopy(target)
            else:
                entry.order = len(self._entries)
                if not entry.created_at:
                    entry.created_at = now_str
                entry.updated_at = now_str
                self._entries.append(entry)
                res = copy.deepcopy(entry)

            self.save()
            self._notify_listeners()
            return res

    def add_from_log_entry(
        self,
        log_entry: LogEntry,
        note: str = "",
        tags: Optional[List[str]] = None,
        channel: str = "",
        bookmarked: bool = True,
    ) -> CasebookEntry:
        """Helper to create/update a CasebookEntry from a LogEntry."""
        cb_entry = CasebookEntry.from_log_entry(
            log_entry, note=note, tags=tags, channel=channel, bookmarked=bookmarked
        )
        return self.add_or_update(cb_entry)

    def remove_entry(self, entry_id: str) -> bool:
        """Remove entry by ID."""
        with self._lock:
            initial_len = len(self._entries)
            self._entries = [e for e in self._entries if e.id != entry_id]
            if len(self._entries) != initial_len:
                for idx, e in enumerate(self._entries):
                    e.order = idx
                self.save()
                self._notify_listeners()
                return True
            return False

    def remove_by_fingerprint(self, fingerprint: str) -> bool:
        """Remove entry matching fingerprint."""
        with self._lock:
            initial_len = len(self._entries)
            self._entries = [e for e in self._entries if e.fingerprint != fingerprint]
            if len(self._entries) != initial_len:
                for idx, e in enumerate(self._entries):
                    e.order = idx
                self.save()
                self._notify_listeners()
                return True
            return False

    def set_note(self, entry_id_or_fp: str, note: str) -> bool:
        with self._lock:
            for e in self._entries:
                if e.id == entry_id_or_fp or e.fingerprint == entry_id_or_fp:
                    e.note = note.strip()
                    e.updated_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                    self.save()
                    self._notify_listeners()
                    return True
            return False

    def set_tags(self, entry_id_or_fp: str, tags: List[str]) -> bool:
        with self._lock:
            for e in self._entries:
                if e.id == entry_id_or_fp or e.fingerprint == entry_id_or_fp:
                    e.tags = list(tags)
                    e.updated_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                    self.save()
                    self._notify_listeners()
                    return True
            return False

    def add_tag(self, entry_id_or_fp: str, tag: str) -> bool:
        tag = tag.strip()
        if not tag:
            return False
        with self._lock:
            for e in self._entries:
                if e.id == entry_id_or_fp or e.fingerprint == entry_id_or_fp:
                    if tag not in e.tags:
                        e.tags.append(tag)
                        e.updated_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                        self.save()
                        self._notify_listeners()
                    return True
            return False

    def remove_tag(self, entry_id_or_fp: str, tag: str) -> bool:
        with self._lock:
            for e in self._entries:
                if e.id == entry_id_or_fp or e.fingerprint == entry_id_or_fp:
                    if tag in e.tags:
                        e.tags.remove(tag)
                        e.updated_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                        self.save()
                        self._notify_listeners()
                    return True
            return False

    def move_up(self, entry_id: str) -> bool:
        """Swap entry with preceding entry in order."""
        with self._lock:
            idx = next((i for i, e in enumerate(self._entries) if e.id == entry_id), -1)
            if idx > 0:
                self._entries[idx - 1], self._entries[idx] = self._entries[idx], self._entries[idx - 1]
                for i, e in enumerate(self._entries):
                    e.order = i
                self.save()
                self._notify_listeners()
                return True
            return False

    def move_down(self, entry_id: str) -> bool:
        """Swap entry with succeeding entry in order."""
        with self._lock:
            idx = next((i for i, e in enumerate(self._entries) if e.id == entry_id), -1)
            if 0 <= idx < len(self._entries) - 1:
                self._entries[idx + 1], self._entries[idx] = self._entries[idx], self._entries[idx + 1]
                for i, e in enumerate(self._entries):
                    e.order = i
                self.save()
                self._notify_listeners()
                return True
            return False

    def reorder_entries(self, ordered_ids: List[str]) -> bool:
        """Reorder all entries to match the provided list of IDs."""
        with self._lock:
            id_map = {e.id: e for e in self._entries}
            new_list = []
            for eid in ordered_ids:
                if eid in id_map:
                    new_list.append(id_map.pop(eid))
            # Append any remaining not in ordered_ids
            new_list.extend(id_map.values())
            for i, e in enumerate(new_list):
                e.order = i
            self._entries = new_list
            self.save()
            self._notify_listeners()
            return True

    def clear_all(self) -> None:
        """Clear all entries in the casebook."""
        with self._lock:
            self._entries.clear()
            self.save()
            self._notify_listeners()

    # ── Export Case Brief ───────────────────────────────────────────────

    def export_brief_markdown(self) -> str:
        """Generate a structured Markdown Case Brief for team handoff."""
        with self._lock:
            entries = list(self._entries)

        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        root_causes = [e for e in entries if TAG_ROOT_CAUSE in e.tags]
        follow_ups = [e for e in entries if TAG_FOLLOW_UP in e.tags]
        benigns = [e for e in entries if TAG_BENIGN in e.tags]

        lines = [
            "# 📋 Diagnostic Investigation Case Brief",
            "",
            f"**Generated:** {now_str}  ",
            f"**Total Evidentiary Items:** {len(entries)}  ",
            f"**Breakdown:** 🎯 {len(root_causes)} Root Cause | 📌 {len(follow_ups)} Follow-up | 🟢 {len(benigns)} Benign",
            "",
            "---",
            "",
            "## 1. Executive Summary & Timeline",
            "",
            "| # | Timestamp | Severity | Channel / Source | Event ID | Tags | Investigator Note |",
            "|---|---|---|---|---|---|---|",
        ]

        if not entries:
            lines.append("| — | — | — | *(No casebook entries recorded)* | — | — | — |")
        else:
            for idx, e in enumerate(entries, start=1):
                ts = e.timestamp or "—"
                lvl = e.level or "Info"
                src = f"{e.channel} / {e.source}" if e.channel and e.channel != e.source else e.source
                eid = e.event_id or "—"
                tag_str = ", ".join(f"`{t}`" for t in e.tags) if e.tags else "—"
                note_snippet = (e.note.split("\n")[0][:80] + "...") if len(e.note) > 80 else (e.note or "—")
                # Escape pipes in markdown table
                src_esc = src.replace("|", "/")
                note_esc = note_snippet.replace("|", "/")
                lines.append(f"| {idx} | {ts} | {lvl} | {src_esc} | {eid} | {tag_str} | {note_esc} |")

        lines.extend([
            "",
            "---",
            "",
            "## 2. Detailed Evidentiary Records & Annotations",
            "",
        ])

        if not entries:
            lines.append("*(No diagnostic records in this casebook)*")
        else:
            for idx, e in enumerate(entries, start=1):
                eid_str = f" (Event ID: {e.event_id})" if e.event_id else ""
                lines.append(f"### Item {idx}: [{e.level}] {e.source}{eid_str}")
                lines.append(f"- **Timestamp:** `{e.timestamp}`")
                if e.channel:
                    lines.append(f"- **Channel / Source:** {e.channel} / {e.source}")
                if e.tags:
                    lines.append(f"- **Tags:** {', '.join(f'`{t}`' for t in e.tags)}")
                if e.note:
                    lines.append("- **Investigator Note / Hypothesis:**")
                    for n_line in e.note.splitlines():
                        lines.append(f"  > 📝 {n_line}")
                lines.append("- **Log Message:**")
                lines.append("  ```text")
                for m_line in (e.message or "").splitlines():
                    lines.append(f"  {m_line}")
                lines.append("  ```")
                if e.raw:
                    lines.append("- **Structured Telemetry:**")
                    lines.append("  ```json")
                    lines.append("  " + json.dumps(e.raw, indent=2, ensure_ascii=False).replace("\n", "\n  "))
                    lines.append("  ```")
                lines.append("")
                lines.append("---")
                lines.append("")

        return "\n".join(lines)

    def export_brief_html(self) -> str:
        """Generate a self-contained, beautifully styled HTML Case Brief."""
        with self._lock:
            entries = list(self._entries)

        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        root_causes = sum(1 for e in entries if TAG_ROOT_CAUSE in e.tags)
        follow_ups = sum(1 for e in entries if TAG_FOLLOW_UP in e.tags)
        benigns = sum(1 for e in entries if TAG_BENIGN in e.tags)
        esc = html.escape

        css = _build_case_brief_css()
        table_html = _build_case_brief_table(entries, esc)
        cards_html = _build_case_brief_cards(entries, esc)

        return f"""<!DOCTYPE html>
<html lang='en'>
<head>
  <meta charset='utf-8'>
  <title>Investigation Case Brief</title>
  <style>
{css}
  </style>
</head>
<body>
<div class='container'>
  <h1>📋 Diagnostic Investigation Case Brief</h1>
  <div class='meta-bar'>
    <div><strong>Generated:</strong> {now_str}</div>
    <div><strong>Total Items:</strong> {len(entries)}</div>
    <div><span class='badge badge-root'>🎯 Root Cause: {root_causes}</span></div>
    <div><span class='badge badge-follow'>📌 Follow-up: {follow_ups}</span></div>
    <div><span class='badge badge-benign'>🟢 Benign: {benigns}</span></div>
  </div>
  <h2>1. Timeline & Executive Summary</h2>
  <table>
    <thead>
      <tr><th>#</th><th>Timestamp</th><th>Level</th><th>Channel / Source</th><th>Event ID</th><th>Tags</th><th>Investigator Note</th></tr>
    </thead>
    <tbody>
{table_html}
    </tbody>
  </table>
  <h2>2. Evidentiary Records & Annotations</h2>
{cards_html}
</div>
</body>
</html>
"""

    def export_brief_to_file(self, target_path: str, format_type: str = "md") -> bool:
        """Export brief to a specified file path."""
        try:
            content = self.export_brief_html() if format_type.lower() == "html" else self.export_brief_markdown()
            dir_name = os.path.dirname(target_path)
            if dir_name:
                os.makedirs(dir_name, exist_ok=True)
            with open(target_path, "w", encoding="utf-8") as f:
                f.write(content)
            return True
        except Exception as exc:
            logger.error("Failed to write case brief to %s: %s", target_path, exc)
            return False


def _build_case_brief_css() -> str:
    from core.semantic_colors import CHROME_PALETTES, SEMANTIC_PALETTES, PANE_BACKGROUND
    c = CHROME_PALETTES["dark"]
    s = SEMANTIC_PALETTES["dark"]
    bg_col = PANE_BACKGROUND["dark"]
    surf_col = c["surface"]
    card_col = c["surface_selected"]
    outline_col = c["outline"]
    text_col = c["text"]

    return f"""
  body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif; background-color: {bg_col}; color: {text_col}; margin: 0; padding: 24px; }}
  .container {{ max-width: 1100px; margin: 0 auto; background-color: {surf_col}; padding: 32px; border-radius: 8px; border: 1px solid {outline_col}; box-shadow: 0 4px 16px rgba(0,0,0,0.4); }}
  h1, h2, h3 {{ color: {s['info']}; margin-top: 0; }}
  h2 {{ border-bottom: 1px solid {outline_col}; padding-bottom: 8px; margin-top: 28px; }}
  .meta-bar {{ display: flex; gap: 16px; flex-wrap: wrap; margin-bottom: 24px; padding: 12px; background: {card_col}; border-radius: 6px; }}
  .badge {{ display: inline-block; padding: 4px 10px; border-radius: 12px; font-size: 12px; font-weight: bold; }}
  .badge-root {{ background-color: {s['error']}; color: white; }}
  .badge-follow {{ background-color: {s['warning']}; color: white; }}
  .badge-benign {{ background-color: {s['success']}; color: white; }}
  .badge-tag {{ background-color: {card_col}; color: {text_col}; }}
  .badge-crit {{ background-color: {s['error']}; color: white; }}
  .badge-err {{ background-color: {s['error']}; color: white; }}
  .badge-warn {{ background-color: {s['warning']}; color: black; }}
  .badge-info {{ background-color: {s['info']}; color: white; }}
  table {{ width: 100%; border-collapse: collapse; margin: 16px 0; font-size: 13px; }}
  th, td {{ border: 1px solid {outline_col}; padding: 10px 12px; text-align: left; }}
  th {{ background-color: {card_col}; color: {s['info']}; font-weight: 600; }}
  tr:nth-child(even) {{ background-color: {surf_col}; }}
  .card {{ background-color: {card_col}; border: 1px solid {outline_col}; border-radius: 6px; padding: 16px; margin-bottom: 20px; }}
  .card-header {{ display: flex; justify-content: space-between; align-items: center; margin-bottom: 10px; }}
  .note-box {{ background-color: {card_col}; border-left: 4px solid {s['info']}; padding: 12px; border-radius: 4px; margin: 12px 0; }}
  pre {{ background-color: {bg_col}; padding: 12px; border-radius: 4px; overflow-x: auto; font-family: Consolas, monospace; font-size: 12px; }}
"""


def _build_case_brief_table(entries: list, esc) -> str:
    if not entries:
        return "      <tr><td colspan='7' style='text-align:center;'>No entries recorded.</td></tr>"
    rows = []
    for idx, e in enumerate(entries, start=1):
        lvl_cls = "badge-info"
        lvl_lower = e.level.lower()
        if "crit" in lvl_lower:
            lvl_cls = "badge-crit"
        elif "err" in lvl_lower:
            lvl_cls = "badge-err"
        elif "warn" in lvl_lower:
            lvl_cls = "badge-warn"

        tags_html = " ".join(f"<span class='badge badge-tag'>{esc(t)}</span>" for t in e.tags) or "—"
        src = f"{e.channel} / {e.source}" if e.channel and e.channel != e.source else e.source
        rows.append(
            f"      <tr>"
            f"<td>{idx}</td>"
            f"<td>{esc(e.timestamp)}</td>"
            f"<td><span class='badge {lvl_cls}'>{esc(e.level)}</span></td>"
            f"<td>{esc(src)}</td>"
            f"<td>{esc(e.event_id or '—')}</td>"
            f"<td>{tags_html}</td>"
            f"<td>{esc(e.note or '—')}</td>"
            f"</tr>"
        )
    return "\n".join(rows)


def _build_case_brief_cards(entries: list, esc) -> str:
    if not entries:
        return "<p>No records in this casebook.</p>"
    cards = []
    for idx, e in enumerate(entries, start=1):
        eid_str = f" (Event ID: {esc(e.event_id)})" if e.event_id else ""
        card = [
            f"  <div class='card'>",
            f"    <div class='card-header'>",
            f"      <h3 style='margin:0;'>Item #{idx}: [{esc(e.level)}] {esc(e.source)}{eid_str}</h3>",
            f"      <div>{esc(e.timestamp)}</div>",
            f"    </div>",
        ]
        if e.tags:
            tags_spans = []
            for t in e.tags:
                t_cls = "badge-tag"
                if t == TAG_ROOT_CAUSE:
                    t_cls = "badge-root"
                elif t == TAG_FOLLOW_UP:
                    t_cls = "badge-follow"
                elif t == TAG_BENIGN:
                    t_cls = "badge-benign"
                tags_spans.append(f"<span class='badge {t_cls}'>{esc(t)}</span>")
            card.append(f"    <div style='margin-bottom:8px;'>{' '.join(tags_spans)}</div>")

        if e.note:
            card.append(f"    <div class='note-box'><b>📝 Investigator Note:</b> {esc(e.note)}</div>")

        card.append(f"    <b>Message:</b><pre>{esc(e.message)}</pre>")
        if e.raw:
            raw_json = json.dumps(e.raw, indent=2, ensure_ascii=False)
            card.append(f"    <b>Structured Telemetry:</b><pre>{esc(raw_json)}</pre>")
        card.append("  </div>")
        cards.append("\n".join(card))
    return "\n".join(cards)


def get_casebook_manager(storage_path: Optional[str] = None) -> CasebookManager:
    """Retrieve the singleton CasebookManager or create one for the specified path."""
    global _DEFAULT_MANAGER
    if storage_path is not None:
        return CasebookManager(storage_path=storage_path)
    if _DEFAULT_MANAGER is None:
        _DEFAULT_MANAGER = CasebookManager()
    return _DEFAULT_MANAGER


def set_casebook_manager(manager: Optional[CasebookManager]) -> None:
    """Set or reset the global CasebookManager instance (critical for test isolation)."""
    global _DEFAULT_MANAGER
    _DEFAULT_MANAGER = manager
