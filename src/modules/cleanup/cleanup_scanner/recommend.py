"""Cleanup recommendations: Qt-free rules over facts that were already measured.

Each rule reads an injected :class:`RecoInputs` (the scan snapshot, the deletion
history, the scan summaries, drive free space, the pending-restart answer and a
few optional probes) and returns :class:`Recommendation` objects -- or nothing.
A recommendation only ever SAYS something or NAVIGATES (switch tab, pre-tick the
named rows); no rule removes anything, and this module imports nothing that can.

Rules that matter here:

- Every title carries the measured figure it is based on ("C: has 14.0 GB free").
- A fact that could not be read is not an answer: a ``None`` probe means the rule
  that needs it stays silent or says it could not tell, never that it is fine.
- Nothing under :data:`PROTECTED_ROOTS` is ever a target or counted in a total:
  ``%USERPROFILE%\\.cache\\huggingface`` holds AI models the owner uses and
  ``.cache\\codex-runtimes`` is a live tool runtime. Model downloads get an
  information-only note (``model_caches``) that offers no action at all.
- There is deliberately no rule that pushes superseded drivers (2.2 MB of 7.13 GB).

:func:`gather_inputs` collects the optional probes (process list, model cache
sizes, crash-dump age, the 7-day temp share, Disk Cleanup configuration) and is
meant to run on a Worker; :func:`evaluate` is pure.
"""
from __future__ import annotations

import logging
import ntpath
import os
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta
from typing import Callable, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from core.formatting import format_size
from modules.cleanup.cleanup_scanner.disk_size import on_disk_size

logger = logging.getLogger(__name__)

GB = 1024 ** 3
MB = 1024 ** 2

# ── Severity ──────────────────────────────────────────────────────────────
HIGH = "high"
WARNING = "warning"
INFO = "info"
SEVERITY_RANK = {HIGH: 0, WARNING: 1, INFO: 2}
SEVERITY_WORD = {HIGH: "Important", WARNING: "Suggestion", INFO: "Good to know"}

# ── Tab keys a recommendation can navigate to ─────────────────────────────
# The integration maps these to its tab widgets. When the snapshot's rows carry
# their own tab keys, a rule prefers the tab its rows are on (see _pick_tab).
TAB_OVERVIEW = "overview"
TAB_SYSTEM = "system"
TAB_BROWSER = "browser"
TAB_APPS = "apps"
TAB_WU = "wu"
TAB_LOGS = "logs"
TAB_LARGE = "large"
TAB_DEV = "dev"
TAB_DISCOVER = "discover"

# ── Thresholds (every rule's number is named here) ────────────────────────
LOW_FREE_PERCENT = 10
LOW_FREE_BYTES = 20 * GB
WU_LEFTOVER_BYTES = 1 * GB
TEMP_TOTAL_BYTES = 5 * GB
TEMP_OLD_BYTES = 5 * GB
TEMP_OLD_DAYS = 7
RECYCLE_BYTES = 1 * GB
DUMP_BYTES = 500 * MB
DUMP_AGE_DAYS = 30
BROWSER_BYTES = 500 * MB
REPEAT_HOLDER_WINDOW = 3
REPEAT_HOLDER_MIN = 2
DEV_CACHE_BYTES = 2 * GB
SHADER_BYTES = 200 * MB
STALE_DAYS = 30
STALE_SAFE_BYTES = 2 * GB
GROWTH_PER_WEEK = 5 * GB
GROWTH_MIN_SUMMARIES = 3
GROWTH_MIN_SPAN_DAYS = 3      # rescans minutes apart must not extrapolate to a week
GROWTH_MAX_SPAN_DAYS = 30
MODEL_CACHE_MIN_BYTES = 1     # any non-empty model folder earns the note
LOW_FREE_TARGETS = 5
DISMISS_DAYS = 30
DISMISS_CONFIG_KEY = "modules.cleanup.dismissed"


@dataclass(frozen=True)
class Recommendation:
    id: str
    severity: str
    title: str
    evidence: str
    target_tab: Optional[str] = None     # None: nothing to navigate to
    target_ids: Tuple[str, ...] = ()
    action_label: str = ""               # "" -> the card shows no action button
    age_days: int = 0                    # age filter the target tab should apply
    note: str = ""                       # a second line, e.g. "this is permanent"
    size: int = 0                        # the measured bytes behind the title
    why: str = ""                        # plain-text reason for the tooltip

    @property
    def navigates(self) -> bool:
        return bool(self.target_tab and self.action_label)


@dataclass
class RecoInputs:
    snapshot: object = None                          # ScanSnapshot or None
    history: Sequence[dict] = ()                     # cleanup_deleted.json entries, any order
    summaries: Sequence[dict] = ()                   # scan_summaries.json, any order
    drives: Sequence[Tuple[str, int, int]] = ()      # drive_lines(): (root, free, total)
    pending_reboot: Optional[bool] = False           # None: could not be read
    old_temp_bytes: Optional[int] = None             # Temp share older than TEMP_OLD_DAYS
    running: Optional[Iterable[str]] = None          # lower-case exe names; None: not read
    model_caches: Mapping[str, Optional[int]] = field(default_factory=dict)
    newest_dump_age_days: Optional[float] = None
    disk_cleanup_categories: Optional[int] = None    # None: not read
    now: Optional[datetime] = None


# ── Path classification ───────────────────────────────────────────────────

def _home() -> str:
    return os.environ.get("USERPROFILE") or os.path.expanduser("~")


#: (folder relative to the user profile, the tool that manages it). These are
#: model downloads and live runtimes: reported, never targeted.
MODEL_CACHE_DIRS: Tuple[Tuple[str, str], ...] = (
    (r".cache\huggingface", "huggingface-cli delete-cache"),
    (r".ollama\models", "ollama rm <model>"),
    (r".cache\torch", "the program that downloaded them"),
    (r".lmstudio\models", "LM Studio's My Models page"),
)
#: Never a target, never counted (model caches plus live tool runtimes).
PROTECTED_RELATIVE: Tuple[str, ...] = tuple(p for p, _tool in MODEL_CACHE_DIRS) + (
    r".cache\codex-runtimes",
)


def _norm(path: str) -> str:
    return ntpath.normcase(ntpath.normpath(str(path)))


def protected_roots(home: Optional[str] = None) -> Tuple[str, ...]:
    home = home or _home()
    return tuple(_norm(ntpath.join(home, rel)) for rel in PROTECTED_RELATIVE)


def is_protected(path: str, home: Optional[str] = None) -> bool:
    """True when `path` IS, is INSIDE, or CONTAINS a protected root.

    Containing counts too: a row for the whole ``.cache`` folder would take the
    models with it, so it is as off-limits as the models themselves.
    """
    p = _norm(path)
    for root in protected_roots(home):
        if p == root or p.startswith(root + "\\") or root.startswith(p.rstrip("\\") + "\\"):
            return True
    return False


def _has(path_l: str, *fragments: str) -> bool:
    return any(f in path_l for f in fragments)


WU_IDS = frozenset({"wu_cache", "update_cleanup", "windowsupdate_orch_cache"})
TEMP_IDS = frozenset({"temp_files", "per_drive_temp", "userprofile_temp", "install_temp"})
RECYCLE_IDS = frozenset({"recycle_bin", "per_drive_recycle_bin"})
DUMP_IDS = frozenset({"memory_dumps", "minidump", "user_crash_dumps", "crash_dumps_system"})
DEV_IDS = frozenset({"npm_cache", "pip_cache", "nuget_cache", "nuget_global_packages",
                     "gradle_caches_cache", "pnpm_cache", "yarn_cache", "maven_repo_cache"})
SHADER_FRAGMENTS = ("\\d3dscache", "\\dxcache", "\\glcache", "\\vkcache", "\\dxccache",
                    "\\vulkancache", "\\graphicscache", "\\shadercache", "\\nv_cache")

#: path fragment -> (exe, display name)
BROWSERS: Tuple[Tuple[str, str, str], ...] = (
    ("\\google\\chrome\\", "chrome.exe", "Chrome"),
    ("\\microsoft\\edge\\", "msedge.exe", "Edge"),
    ("\\bravesoftware\\", "brave.exe", "Brave"),
    ("\\mozilla\\firefox\\", "firefox.exe", "Firefox"),
    ("\\opera software\\", "opera.exe", "Opera"),
    ("\\vivaldi\\", "vivaldi.exe", "Vivaldi"),
)


def is_wu(row) -> bool:
    p = _norm(row.path)
    return row.id in WU_IDS or _has(p, "\\softwaredistribution\\download", "\\softwaredistribution\\backup")


def is_temp(row) -> bool:
    p = _norm(row.path)
    return row.id in TEMP_IDS or ntpath.basename(p) in ("temp", "tmp")


def is_recycle(row) -> bool:
    return row.id in RECYCLE_IDS or "$recycle.bin" in _norm(row.path)


def is_dump(row) -> bool:
    p = _norm(row.path)
    return (row.id in DUMP_IDS or p.endswith(".dmp")
            or _has(p, "\\crashdumps", "\\minidump"))


def is_dev_cache(row) -> bool:
    p = _norm(row.path)
    return row.id in DEV_IDS or _has(p, "\\npm-cache", "\\pip\\cache", "\\.nuget\\packages",
                                     "\\.gradle\\caches", "\\yarn\\cache", "\\pnpm\\store")


def is_shader(row) -> bool:
    return "shader" in row.id or _has(_norm(row.path), *SHADER_FRAGMENTS)


def browser_of(row) -> Optional[Tuple[str, str]]:
    p = _norm(row.path)
    for fragment, exe, name in BROWSERS:
        if fragment in p:
            return exe, name
    return None


def drive_of(path: str) -> str:
    return ntpath.splitdrive(str(path))[0].upper()


# ── Helpers ───────────────────────────────────────────────────────────────

def _rows(snapshot, home: Optional[str] = None) -> list:
    """Unique rows (cross-tab duplicates counted once), protected roots dropped."""
    if snapshot is None:
        return []
    rows = getattr(snapshot, "unique_rows", None)
    if rows is None:
        rows = getattr(snapshot, "rows", ())
    return [r for r in rows if r.size >= 0 and not is_protected(r.path, home)]


def _total(rows) -> int:
    return sum(r.size for r in rows)


def _ids(rows) -> Tuple[str, ...]:
    return tuple(dict.fromkeys(r.id for r in sorted(rows, key=lambda r: -r.size)))


def _pick_tab(rows, preferred: str) -> str:
    """The preferred tab when a target row is on it, else the largest row's tab."""
    for r in rows:
        if str(r.tab).lower() == preferred:
            return r.tab
    if rows:
        return max(rows, key=lambda r: r.size).tab
    return preferred


def _parse_at(value) -> Optional[datetime]:
    if not isinstance(value, str):
        return None
    try:
        when = datetime.fromisoformat(value)
    except ValueError:
        return None
    return when.replace(tzinfo=None) if when.tzinfo else when


def _days(n: float) -> str:
    n = int(n)
    return f"{n} day" if n == 1 else f"{n} days"


def _sorted_by_time(entries: Sequence[dict]) -> List[Tuple[datetime, dict]]:
    out = []
    for e in entries or ():
        if isinstance(e, dict):
            when = _parse_at(e.get("at"))
            if when is not None:
                out.append((when, e))
    out.sort(key=lambda t: t[0])
    return out


def _labels(rows, limit: int = 3) -> str:
    seen = []
    for r in sorted(rows, key=lambda r: -r.size):
        if r.label not in seen:
            seen.append(r.label)
        if len(seen) == limit:
            break
    return ", ".join(seen)


# ── Rules ─────────────────────────────────────────────────────────────────

def rule_low_free_space(inp: RecoInputs, rows) -> List[Recommendation]:
    out = []
    for root, free, total in inp.drives or ():
        if not total or total <= 0:
            continue
        pct = free * 100 / total
        if not (pct < LOW_FREE_PERCENT or free < LOW_FREE_BYTES):
            continue
        drive = drive_of(root) or str(root).rstrip("\\")
        safe = [r for r in rows if r.effective_safety == "safe" and drive_of(r.path) == drive]
        top = sorted(safe, key=lambda r: -r.size)[:LOW_FREE_TARGETS]
        title = f"{drive} has {format_size(free)} free ({pct:.0f} %)"
        if top:
            evidence = (f"Largest safe items on {drive}: "
                        + ", ".join(f"{r.label} {format_size(r.size)}" for r in top) + ".")
            out.append(Recommendation(
                f"low_free_space.{drive}", HIGH, title, evidence, TAB_OVERVIEW, _ids(top),
                "Review safe items", size=free,
                why=(f"A drive under {LOW_FREE_PERCENT} % or {format_size(LOW_FREE_BYTES)} free "
                     "slows updates and can make them fail.")))
        else:
            out.append(Recommendation(
                f"low_free_space.{drive}", HIGH, title,
                f"Nothing Cleanup rates safe is on {drive}; TreeSize shows what fills it.",
                size=free))
    return out


def rule_wu_leftovers(inp: RecoInputs, rows) -> List[Recommendation]:
    wu = [r for r in rows if is_wu(r)]
    size = _total(wu)
    if size <= WU_LEFTOVER_BYTES:
        return []
    if inp.pending_reboot is True:
        return [Recommendation(
            "wu_leftovers", WARNING, f"Restart first: Windows still needs its update files ({format_size(size)})",
            "A restart is pending, so an update may still be installing from these files.",
            size=size, why="Cleaning update files mid-install makes the update download again.")]
    if inp.pending_reboot is None:
        return [Recommendation(
            "wu_leftovers", WARNING, f"Windows Update leftovers: {format_size(size)}",
            "Could not read whether a restart is pending: restart first if an update is installing.",
            size=size)]
    return [Recommendation(
        "wu_leftovers", WARNING, f"Windows Update leftovers: {format_size(size)}",
        f"No restart is pending. {_labels(wu)}.", _pick_tab(wu, TAB_WU), _ids(wu),
        "Review update files", size=size,
        why="Downloaded update packages Windows no longer needs once installed.")]


def needs_old_temp_probe(snapshot, home: Optional[str] = None) -> bool:
    """Only measure the week-old Temp share when Temp is big enough to matter."""
    return _total([r for r in _rows(snapshot, home) if is_temp(r)]) > TEMP_TOTAL_BYTES


def rule_old_temp(inp: RecoInputs, rows) -> List[Recommendation]:
    temp = [r for r in rows if is_temp(r)]
    total = _total(temp)
    if total <= TEMP_TOTAL_BYTES or inp.old_temp_bytes is None or inp.old_temp_bytes <= TEMP_OLD_BYTES:
        return []
    return [Recommendation(
        "old_temp", WARNING, f"Temp holds {format_size(inp.old_temp_bytes)} older than a week",
        f"Temp is {format_size(total)} in all. Files under a day old are always kept.",
        _pick_tab(temp, TAB_SYSTEM), _ids(temp), "Show week-old Temp", age_days=TEMP_OLD_DAYS,
        size=inp.old_temp_bytes,
        why="Installers and programs leave extracted files behind in Temp.")]


def rule_recycle_bin(inp: RecoInputs, rows) -> List[Recommendation]:
    rb = [r for r in rows if is_recycle(r)]
    size = _total(rb)
    if size <= RECYCLE_BYTES:
        return []
    drives = sorted({drive_of(r.path) for r in rb if drive_of(r.path)})
    where = f" on {', '.join(drives)}" if drives else ""
    return [Recommendation(
        "recycle_bin", WARNING, f"Recycle Bin: {format_size(size)}{where}",
        "Check nothing in it is still wanted.", _pick_tab(rb, TAB_SYSTEM), _ids(rb),
        "Review Recycle Bin", note="Emptying the Recycle Bin is permanent: nothing can be restored.",
        size=size)]


def rule_crash_dumps(inp: RecoInputs, rows) -> List[Recommendation]:
    dumps = [r for r in rows if is_dump(r)]
    size = _total(dumps)
    age = inp.newest_dump_age_days
    if size <= DUMP_BYTES or age is None or age <= DUMP_AGE_DAYS:
        return []
    return [Recommendation(
        "crash_dumps", INFO, f"Crash dumps: {format_size(size)}, newest {_days(age)} old",
        "Keep them if you are still investigating a crash.", _pick_tab(dumps, TAB_LOGS),
        _ids(dumps), "Review crash dumps", size=size,
        why="Dumps only help diagnose a crash; old ones rarely matter.")]


def _holder_counts(history: Sequence[dict]) -> Tuple[Dict[str, int], int]:
    """How many of the last REPEAT_HOLDER_WINDOW cleans each process held files in."""
    recent = [e for _when, e in _sorted_by_time(history) if isinstance(e.get("owners"), dict)]
    recent = recent[-REPEAT_HOLDER_WINDOW:]
    counts: Dict[str, int] = {}
    for e in recent:
        for name in e["owners"]:
            counts[str(name)] = counts.get(str(name), 0) + 1
    return counts, len(recent)


def _last_owner_files(history: Sequence[dict], exe: str) -> Optional[int]:
    for _when, e in reversed(_sorted_by_time(history)):
        owners = e.get("owners")
        if isinstance(owners, dict):
            for name, n in owners.items():
                if str(name).lower().split(" ")[0] == exe:
                    try:
                        return int(n)
                    except (TypeError, ValueError):
                        return None
            return None
    return None


def rule_browser_running(inp: RecoInputs, rows) -> List[Recommendation]:
    if inp.running is None:
        return []
    running = {str(n).lower() for n in inp.running}
    per: Dict[Tuple[str, str], list] = {}
    for r in rows:
        b = browser_of(r)
        if b:
            per.setdefault(b, []).append(r)
    out = []
    for (exe, name), brs in sorted(per.items()):
        size = _total(brs)
        if size <= BROWSER_BYTES or exe not in running:
            continue
        held = _last_owner_files(inp.history, exe)
        evidence = (f"The last clean left {held:,} files in use by {exe}." if held
                    else "Files a running browser holds open are left in place and not freed.")
        out.append(Recommendation(
            f"browser_running.{exe}", WARNING, f"Close {name} first: {format_size(size)} of its cache is in use",
            evidence, _pick_tab(brs, TAB_BROWSER), _ids(brs), "Review browser caches", size=size))
    return out


#: Holders "close it first" would be nonsense for: the kernel and Windows' own
#: service hosts. Restart Manager names PID 4 "System".
NOT_CLOSABLE = frozenset({"system", "registry", "idle", "secure", "memory", "svchost.exe",
                          "csrss.exe", "lsass.exe", "services.exe", "wininit.exe", "winlogon.exe",
                          "smss.exe", "dwm.exe", "fontdrvhost.exe"})


def rule_repeat_holder(inp: RecoInputs, rows) -> List[Recommendation]:
    counts, window = _holder_counts(inp.history)
    out = []
    for name, n in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0])):
        if n < REPEAT_HOLDER_MIN or name.lower().split(" ")[0] in NOT_CLOSABLE:
            continue
        out.append(Recommendation(
            f"repeat_holder.{name.lower()}", INFO, f"{name} held files in {n} of the last {window} cleans",
            f"Close {name} before cleaning; the files it holds stay and are not freed."))
    return out[:3]


def rule_dev_caches(inp: RecoInputs, rows) -> List[Recommendation]:
    dev = [r for r in rows if is_dev_cache(r)]
    size = _total(dev)
    if size <= DEV_CACHE_BYTES:
        return []
    return [Recommendation(
        "dev_caches", INFO, f"Developer caches: {format_size(size)}",
        f"{_labels(dev)}. They re-download on the next build or install.",
        _pick_tab(dev, TAB_DEV), _ids(dev), "Review developer caches", size=size)]


def rule_shader_caches(inp: RecoInputs, rows) -> List[Recommendation]:
    sh = [r for r in rows if is_shader(r)]
    size = _total(sh)
    if size <= SHADER_BYTES:
        return []
    # Counter-advice: no action button on purpose.
    return [Recommendation(
        "shader_caches", INFO, f"Shader caches: {format_size(size)}; leave them unless a driver changed",
        "Rebuilding them causes stutter in games; clean only after a graphics driver change.",
        target_ids=_ids(sh), size=size)]


def rule_stale_clean(inp: RecoInputs, rows) -> List[Recommendation]:
    safe = [r for r in rows if r.effective_safety == "safe"]
    size = _total(safe)
    if size <= STALE_SAFE_BYTES:
        return []
    now = inp.now or datetime.now()
    dated = _sorted_by_time(inp.history)
    if dated:
        last = dated[-1][0]
        age = (now - last).total_seconds() / 86400
        if age <= STALE_DAYS:
            return []
        title = f"No clean in {_days(age)}; {format_size(size)} is safe to remove"
    else:
        title = f"No clean recorded yet; {format_size(size)} is safe to remove"
    return [Recommendation(
        "stale_clean", WARNING, title, f"Largest: {_labels(safe)}.", TAB_OVERVIEW, _ids(safe),
        "Review safe items", size=size)]


def rule_growth(inp: RecoInputs, rows) -> List[Recommendation]:
    dated = [(w, e) for w, e in _sorted_by_time(inp.summaries) if isinstance(e.get("total_size"), int)]
    if len(dated) < GROWTH_MIN_SUMMARIES:
        return []
    latest_at, latest = dated[-1]
    # Across the last three summaries; when those are rescans minutes apart,
    # reach back to the newest one at least GROWTH_MIN_SPAN_DAYS older.
    base = dated[-GROWTH_MIN_SUMMARIES]
    if (latest_at - base[0]).total_seconds() / 86400 < GROWTH_MIN_SPAN_DAYS:
        base = next(((w, e) for w, e in reversed(dated[:-GROWTH_MIN_SUMMARIES])
                     if (latest_at - w).total_seconds() / 86400 >= GROWTH_MIN_SPAN_DAYS), None)
    if base is not None and (latest_at - base[0]).total_seconds() / 86400 > GROWTH_MAX_SPAN_DAYS:
        base = None
    if base is None:
        return []
    base_at, first = base
    span = (latest_at - base_at).total_seconds() / 86400
    grew = latest["total_size"] - first["total_size"]
    per_week = grew * 7 / span
    if per_week <= GROWTH_PER_WEEK:
        return []
    evidence = "Find out what writes there."
    a, b = first.get("categories") or {}, latest.get("categories") or {}
    if isinstance(a, dict) and isinstance(b, dict):
        deltas = {k: int(b.get(k, 0) or 0) - int(a.get(k, 0) or 0) for k in b}
        if deltas:
            top, delta = max(deltas.items(), key=lambda kv: kv[1])
            if delta > 0:
                evidence = f"Most of it: {top} (+{format_size(delta)}). Find out what writes there."
    return [Recommendation(
        "growth", WARNING,
        f"Reclaimable space grew {format_size(grew)} in {_days(span)} ({format_size(int(per_week))} a week)",
        evidence, TAB_DISCOVER, (), "Find what writes there", size=grew)]


def rule_model_caches(inp: RecoInputs, rows) -> List[Recommendation]:
    """INFO only. It never targets, ticks or navigates: Cleanup does not offer these."""
    out = []
    tools = dict(MODEL_CACHE_DIRS)
    for path, size in (inp.model_caches or {}).items():
        if size is not None and size < MODEL_CACHE_MIN_BYTES:
            continue
        rel = next((r for r in tools if _norm(path).endswith(_norm(r))), None)
        tool = tools.get(rel, "the program that downloaded them")
        amount = format_size(size) if size is not None else "An unmeasured amount"
        out.append(Recommendation(
            f"model_caches.{_norm(path)}", INFO, f"{amount} of AI model downloads in {path}",
            f"Manage them with the tool that owns them ({tool}). Cleanup never deletes them.",
            size=size or 0))
    return out


def rule_disk_cleanup(inp: RecoInputs, rows) -> List[Recommendation]:
    if inp.disk_cleanup_categories != 0:
        return []
    return [Recommendation(
        "disk_cleanup_unconfigured", INFO, "Windows Disk Cleanup: 0 categories configured",
        "Configure it once to let it remove what only Windows itself can.", TAB_LARGE, (),
        "Configure Disk Cleanup")]


RULES: Tuple[Callable[[RecoInputs, list], List[Recommendation]], ...] = (
    rule_low_free_space, rule_wu_leftovers, rule_old_temp, rule_recycle_bin, rule_crash_dumps,
    rule_browser_running, rule_repeat_holder, rule_dev_caches, rule_shader_caches,
    rule_stale_clean, rule_growth, rule_model_caches, rule_disk_cleanup,
)


def sort_key(rec: Recommendation):
    return (SEVERITY_RANK.get(rec.severity, 9), -rec.size, rec.id)


def evaluate(inp: RecoInputs, home: Optional[str] = None) -> List[Recommendation]:
    """Every rule's recommendations, most severe first. A rule that raises is
    logged and skipped; the others still answer."""
    rows = _rows(inp.snapshot, home)
    out: List[Recommendation] = []
    for rule in RULES:
        try:
            out.extend(rule(inp, rows))
        except Exception as exc:   # one broken rule must not hide the rest
            logger.warning("cleanup recommendation %s failed: %s", rule.__name__, exc, exc_info=True)
    # Navigation pre-ticks rows BY ID, so an id that also names a protected
    # path anywhere in the snapshot is never handed to a tab.
    every = getattr(inp.snapshot, "rows", ()) or ()
    blocked = {r.id for r in every if is_protected(r.path, home)}
    if blocked:
        out = [replace(r, target_ids=tuple(i for i in r.target_ids if i not in blocked))
               if set(r.target_ids) & blocked else r for r in out]
    out.sort(key=sort_key)
    return out


# ── Dismissals (config modules.cleanup.dismissed = {id: ISO expiry}) ──────

def active_dismissals(store: Optional[Mapping], now: Optional[datetime] = None) -> Dict[str, str]:
    now = now or datetime.now()
    out = {}
    for rec_id, until in (store or {}).items():
        when = _parse_at(until)
        if when is not None and when > now:
            out[str(rec_id)] = until
    return out


def dismiss(store: Optional[Mapping], rec_id: str, now: Optional[datetime] = None,
            days: int = DISMISS_DAYS) -> Dict[str, str]:
    """A new store with `rec_id` hidden for `days`; expired entries dropped."""
    now = now or datetime.now()
    out = active_dismissals(store, now)
    out[rec_id] = (now + timedelta(days=days)).isoformat(timespec="seconds")
    return out


def visible(recs: Sequence[Recommendation], store: Optional[Mapping],
            now: Optional[datetime] = None) -> List[Recommendation]:
    hidden = active_dismissals(store, now)
    return [r for r in recs if r.id not in hidden]


# ── Probes (run on a Worker) ──────────────────────────────────────────────

def running_exe_names() -> Optional[set]:
    """Lower-case process image names, or None when the list could not be read."""
    try:
        import psutil
        names = set()
        for p in psutil.process_iter(["name"]):
            name = p.info.get("name")
            if name:
                names.add(name.lower())
        return names
    except Exception as exc:
        logger.warning("cannot list running processes for recommendations: %s", exc)
        return None


def measure_model_caches(home: Optional[str] = None,
                         size_of: Optional[Callable[[str], int]] = None) -> Dict[str, Optional[int]]:
    """{path: bytes} for each model folder that exists; None when it could not be measured.
    Reads sizes only (no file is opened, so no cloud placeholder is hydrated)."""
    home = home or _home()
    if size_of is None:
        from modules.cleanup.cleanup_scanner._common import get_dir_size as size_of
    out: Dict[str, Optional[int]] = {}
    for rel, _tool in MODEL_CACHE_DIRS:
        path = ntpath.join(home, rel)
        if not os.path.isdir(path):
            continue
        try:
            out[path] = int(size_of(path))
        except OSError as exc:
            logger.warning("cannot measure %s: %s", path, exc)
            out[path] = None
    return out


def newest_age_days(paths: Iterable[str], now: Optional[datetime] = None) -> Optional[float]:
    """Age of the most recently modified path (a folder's mtime moves when a dump
    is added). None when none could be read."""
    now_ts = (now or datetime.now()).timestamp()
    newest = None
    for p in paths:
        try:
            m = os.stat(p).st_mtime
        except OSError as error:
            logger.debug("recommend: dump folder %s unreadable: %s", p, error)
            continue
        newest = m if newest is None else max(newest, m)
    return None if newest is None else max(0.0, (now_ts - newest) / 86400)


#: File attributes that make an entry not worth counting: a link (never
#: followed) or a cloud placeholder (deleting it frees nothing locally).
_REPARSE = 0x400
_CLOUD = 0x1000 | 0x40000 | 0x400000


def older_than_bytes(path: str, days: int, now: Optional[float] = None,
                     is_cancelled: Callable[[], bool] = lambda: False,
                     excluded: Callable[[str], bool] = lambda p: False) -> Optional[int]:
    """Bytes of the FILES under `path` last modified more than `days` ago.

    Judged per file: the catalog's age floor tests the folder's own mtime,
    and %TEMP% is written every minute, so ``cached_scan(temp, 7)`` answers 0
    on a Temp folder full of month-old files. One scandir walk, stat only (no
    file is opened); links are not followed and cloud placeholders are
    skipped. None when `path` itself cannot be listed.
    """
    cutoff = (now if now is not None else datetime.now().timestamp()) - days * 86400
    total, stack, first = 0, [path], True
    while stack:
        if is_cancelled():
            break
        folder = stack.pop()
        try:
            entries = list(os.scandir(folder))
        except OSError as exc:
            if first:
                logger.warning("week-old Temp probe cannot list %s: %s", folder, exc)
                return None
            logger.debug("week-old Temp probe cannot list %s: %s", folder, exc)
            continue
        first = False
        for entry in entries:
            try:
                st = entry.stat(follow_symlinks=False)
                attrs = getattr(st, "st_file_attributes", 0)
                if attrs & (_REPARSE | _CLOUD) or entry.is_symlink() or excluded(entry.path):
                    continue
                if entry.is_dir(follow_symlinks=False):
                    stack.append(entry.path)
                elif st.st_mtime < cutoff:
                    total += on_disk_size(entry.path, st)
            except OSError as exc:
                logger.debug("week-old Temp probe cannot read %s: %s", entry.path, exc)
    return total


def measure_old_temp(snapshot, home: Optional[str] = None, now: Optional[float] = None,
                     is_cancelled: Callable[[], bool] = lambda: False,
                     excluded: Optional[Callable[[str], bool]] = None) -> Optional[int]:
    """Bytes in the snapshot's Temp locations older than TEMP_OLD_DAYS, per file,
    honouring the user's exclusions. None when no Temp location could be read."""
    if excluded is None:
        from modules.cleanup.cleanup_scanner import exclusions
        rules = exclusions.list_exclusions()
        excluded = (lambda p: exclusions.is_excluded(p, rules)) if rules else (lambda p: False)
    total, measured = 0, False
    for r in _rows(snapshot, home):
        if not is_temp(r) or not r.is_dir:
            continue
        got = older_than_bytes(r.path, TEMP_OLD_DAYS, now, is_cancelled, excluded)
        if got is not None:
            measured = True
            total += got
    return total if measured else None


def gather_inputs(snapshot, *, history=None, summaries=None, drives=None,
                  pending_reboot="read", is_cancelled: Callable[[], bool] = lambda: False
                  ) -> RecoInputs:
    """Collect every input that is not supplied. Blocking: call it on a Worker."""
    from modules.cleanup import cleanup_history
    from modules.cleanup.cleanup_scanner.scan_snapshot import read_summaries
    inp = RecoInputs(snapshot=snapshot, now=datetime.now())
    inp.history = history if history is not None else cleanup_history.recent_deleted(200)
    inp.summaries = summaries if summaries is not None else read_summaries()
    if drives is None:
        from modules.cleanup.cleanup_scanner import drives as _drives
        import shutil
        drives = []
        for root in _drives.fixed_drive_roots():
            try:
                usage = shutil.disk_usage(root)
                drives.append((root, usage.free, usage.total))
            except OSError as exc:
                logger.warning("cannot read free space on %s: %s", root, exc)
    inp.drives = drives
    if pending_reboot == "read":
        from core import pending_reboot as pr
        try:
            state = pr.check()
            inp.pending_reboot = True if state.needed else (None if state.unreadable else False)
        except OSError as exc:
            logger.warning("cannot read pending restart state: %s", exc)
            inp.pending_reboot = None
    else:
        inp.pending_reboot = pending_reboot
    if is_cancelled():
        return inp
    inp.running = running_exe_names()
    inp.model_caches = measure_model_caches()
    rows = _rows(snapshot)
    inp.newest_dump_age_days = newest_age_days([r.path for r in rows if is_dump(r)])
    try:
        from modules.cleanup import disk_cleanup_sageset as dcs
        cats = dcs.configured_categories()
        inp.disk_cleanup_categories = None if cats is None else len(cats)
    except (ImportError, OSError) as exc:
        logger.warning("cannot read Disk Cleanup configuration: %s", exc)
    if not is_cancelled() and needs_old_temp_probe(snapshot):
        inp.old_temp_bytes = measure_old_temp(snapshot, is_cancelled=is_cancelled)
    return inp


# ── Tooltip (rich text, every dynamic string escaped) ─────────────────────

def tooltip_html(rec: Recommendation, snapshot=None, max_targets: int = 3) -> str:
    """The card's tooltip: why the rule fired, what it is based on, and for each
    target the research entry (or the catalog description marked 'Not yet
    researched' -- never invented text)."""
    from html import escape
    from modules.cleanup.cleanup_scanner.knowledge import entry_for
    parts = [f"<b>{escape(rec.title)}</b> [{escape(SEVERITY_WORD.get(rec.severity, rec.severity))}]"]
    if rec.why:
        parts.append(f"<p><b>Why:</b> {escape(rec.why)}</p>")
    parts.append(f"<p><b>Based on:</b> {escape(rec.evidence)}</p>")
    if rec.note:
        parts.append(f"<p><b>Note:</b> {escape(rec.note)}</p>")
    by_id: Dict[str, object] = {}
    for r in getattr(snapshot, "rows", ()) or ():
        if r.id not in by_id or r.size > by_id[r.id].size:
            by_id[r.id] = r
    for rec_id in rec.target_ids[:max_targets]:
        row = by_id.get(rec_id)
        label = getattr(row, "label", rec_id)
        entry = entry_for(rec_id)
        if entry is not None:
            text = entry.what or entry.used_for
            risk = f" <i>Risk:</i> {escape(entry.risk)}" if entry.risk else ""
            parts.append(f"<p><b>{escape(str(label))}:</b> {escape(text)}{risk}</p>")
        else:
            desc = getattr(row, "description", "") or ""
            parts.append(f"<p><b>{escape(str(label))}:</b> {escape(desc)} <i>Not yet researched.</i></p>")
    if len(rec.target_ids) > max_targets:
        parts.append(f"<p>…and {len(rec.target_ids) - max_targets} more locations.</p>")
    if rec.navigates:
        parts.append("<p>The button only opens the tab and ticks these rows; "
                     "nothing is cleaned until you confirm there.</p>")
    elif rec.id.startswith("model_caches"):
        parts.append("<p>Information only: Cleanup never offers these files for deletion.</p>")
    return "".join(parts)
