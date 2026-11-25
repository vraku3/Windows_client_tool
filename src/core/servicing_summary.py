"""A summary strip and rich detail enricher for CBS and DISM logs. Qt-free.

Both logs contain tens of thousands of lines of routine chatter. This module
surfaces the essential servicing diagnostics:
- Component store corruption markers and SFC/CBS repair activity
- Missing payload files, missing manifests, corrupted store packages
- Package stage transitions (Staged, Installed, Superseded, Failed)
- DISM operations (CheckHealth, ScanHealth, RestoreHealth, Add/Remove-Package, StartComponentCleanup)
- Scratch directory configuration and space warnings
- Translated HRESULTs with actionable causes and remedies
"""
from __future__ import annotations

import html
import re
from collections import Counter
from typing import Dict, List, Optional, Tuple

from core.types import LogEntry
from core.semantic_colors import semantic, chrome
from modules.log_viewer.error_codes import advice, corruption_spans, describe, explain

_HRESULT = re.compile(r"\b0[xX]([0-9a-fA-F]{8})\b")
#: `[HRESULT = 0x800f0805 - CBS_E_INVALID_PACKAGE]`: Windows names the code itself.
_INLINE_NAME = re.compile(r"0[xX]([0-9a-fA-F]{8})\s*-\s*([A-Za-z][A-Za-z0-9_]{3,})")
_SR_ACTIVITY = re.compile(r"\[SR\]\s+(Repairing|Repaired|Repair complete|Verifying)", re.IGNORECASE)
_SR_REPAIRED = re.compile(r"\[SR\]\s+(Repairing|Repaired|Repair complete)", re.IGNORECASE)
_DISM_COMMAND = re.compile(r"Executing command line:\s*(.+)$", re.IGNORECASE)

CBS_E_INVALID_PACKAGE = 0x800F0805
_PROBE_LINE = re.compile(
    r"InternalOpenPackage failed for|Failed to internally open package|"
    r"Failed to create open package|Failed to OpenPackage using worker session",
    re.IGNORECASE,
)
_PROBED_PACKAGE = re.compile(r"InternalOpenPackage failed for (\S+?)~", re.IGNORECASE)


def _is_probe(message: str, code: int) -> bool:
    return code == CBS_E_INVALID_PACKAGE and bool(_PROBE_LINE.search(message))


def failing_codes(entries: List[LogEntry]) -> Counter:
    """Count of each failing HRESULT (sign bit set) across all messages,
    leaving out benign package-presence lookups."""
    counts: Counter = Counter()
    for e in entries:
        message = e.message or ""
        for m in _HRESULT.finditer(message):
            code = int(m.group(1), 16)
            if code & 0x80000000 and not _is_probe(message, code):
                counts[code] += 1
    return counts


def package_probes(entries: List[LogEntry]) -> Counter:
    """Package name -> how many times CBS looked for it and found it absent."""
    counts: Counter = Counter()
    for e in entries:
        message = e.message or ""
        m = _PROBED_PACKAGE.search(message)
        if m and any(_is_probe(message, int(h.group(1), 16)) for h in _HRESULT.finditer(message)):
            counts[m.group(1)] += 1
    return counts


def _inline_names(entries: List[LogEntry]) -> dict:
    names = {}
    for e in entries:
        for m in _INLINE_NAME.finditer(e.message or ""):
            names.setdefault(int(m.group(1), 16), m.group(2))
    return names


def code_meaning(code: int, names: dict) -> str:
    text = describe(code)
    if names.get(code):
        return f"{names[code]}" + (f": {text}" if text else "")
    return text


def corruption_markers(entries: List[LogEntry]) -> Counter:
    counts: Counter = Counter()
    for e in entries:
        for _s, _e, label in corruption_spans(e.message or ""):
            counts[label] += 1
    return counts


def package_stage_summary(entries: List[LogEntry]) -> Dict[str, object]:
    """Summary of package stage transitions across entries."""
    state_counts: Counter = Counter()
    packages_seen: set = set()
    failed_packages: list = []

    for e in entries:
        raw = e.raw or {}
        target = raw.get("target_state")
        pkg = raw.get("package")
        if target:
            state_counts[str(target).capitalize()] += 1
            if pkg:
                packages_seen.add(pkg)
                if str(target).capitalize() == "Failed" and pkg not in failed_packages:
                    failed_packages.append(pkg)

    return {
        "counts": state_counts,
        "total_transitions": sum(state_counts.values()),
        "unique_packages": len(packages_seen),
        "failed_packages": failed_packages,
    }


def servicing_failures_summary(entries: List[LogEntry]) -> Dict[str, object]:
    """Summary of specific servicing failures detected in the log entries."""
    failure_counts: Counter = Counter()
    affected_packages: list = []
    target_files: list = []

    for e in entries:
        raw = e.raw or {}
        ftype = raw.get("failure_type")
        if ftype:
            failure_counts[ftype] += 1
            pkg = raw.get("package")
            if pkg and pkg not in affected_packages:
                affected_packages.append(pkg)
            fp = raw.get("file_path")
            if fp and fp not in target_files:
                target_files.append(fp)

    return {
        "counts": failure_counts,
        "total_failures": sum(failure_counts.values()),
        "affected_packages": affected_packages,
        "target_files": target_files,
    }


def dism_operations_summary(entries: List[LogEntry]) -> Dict[str, object]:
    """Summary of DISM operations and scratch buffer telemetry."""
    op_counts: Counter = Counter()
    latest_op: Optional[str] = None
    scratch_dir: Optional[str] = None
    scratch_warnings: list = []
    has_errors: bool = False

    for e in entries:
        raw = e.raw or {}
        op = raw.get("operation")
        if op:
            op_counts[op] += 1
            latest_op = op
        if raw.get("scratch_dir"):
            scratch_dir = raw.get("scratch_dir")
        if raw.get("scratch_warning"):
            scratch_warnings.append(raw.get("scratch_warning"))
        if e.level.lower() == "error" or raw.get("hresult"):
            has_errors = True

    return {
        "counts": op_counts,
        "total_operations": sum(op_counts.values()),
        "latest_operation": latest_op,
        "scratch_directory": scratch_dir,
        "scratch_warnings": scratch_warnings,
        "has_errors": has_errors,
    }


def summarize(entries: List[LogEntry], kind: str = "cbs") -> str:
    """Detailed summary string for the summary strip."""
    if not entries:
        return "The log had no readable lines."
    levels = Counter(e.level for e in entries)
    first = min(e.timestamp for e in entries)
    last = max(e.timestamp for e in entries)
    parts = [
        f"{len(entries):,} lines, {first:%Y-%m-%d %H:%M} to {last:%Y-%m-%d %H:%M}; "
        f"{levels.get('Error', 0)} Error, {levels.get('Warning', 0)} Warning."
    ]

    # Servicing Failures & Corruption Markers
    markers = corruption_markers(entries)
    failures = servicing_failures_summary(entries)

    if kind == "cbs":
        if failures["total_failures"]:
            f_counts = failures["counts"]
            f_parts = []
            if f_counts.get("missing_payload"):
                f_parts.append(f"{f_counts['missing_payload']} missing payload(s)")
            if f_counts.get("manifest_not_found"):
                f_parts.append(f"{f_counts['manifest_not_found']} manifest not found")
            if f_counts.get("store_corruption"):
                f_parts.append(f"{f_counts['store_corruption']} store corruption")
            if f_counts.get("stage_failure"):
                f_parts.append(f"{f_counts['stage_failure']} stage failure(s)")
            parts.append(f"Servicing Failures Detected: {', '.join(f_parts)}.")

        if markers:
            parts.append("Corruption markers: " + ", ".join(f"{k} x{v}" for k, v in markers.most_common(4)) + ".")
        elif not failures["total_failures"]:
            parts.append("No component-store corruption markers.")

        # Package Stage Transitions
        stages = package_stage_summary(entries)
        if stages["total_transitions"]:
            c = stages["counts"]
            st_text = ", ".join(f"{k}: {v}" for k, v in c.most_common())
            parts.append(f"Package Stage Transitions ({stages['total_transitions']}): {st_text}.")
            if stages["failed_packages"]:
                parts.append(f"Failed Package(s): {', '.join(stages['failed_packages'][:3])}.")

        # Target files affected
        if failures["target_files"]:
            parts.append(f"Target file(s): {', '.join(failures['target_files'][:2])}.")

        # SFC Repair activity
        repaired = sum(1 for e in entries if _SR_REPAIRED.search(e.message or ""))
        if repaired:
            parts.append(f"SFC/CBS repair activity: {repaired} line(s).")

    elif kind == "dism":
        dism_ops = dism_operations_summary(entries)
        if dism_ops["total_operations"]:
            ops_text = ", ".join(f"{k} x{v}" for k, v in dism_ops["counts"].most_common(5))
            parts.append(f"DISM Operations ({dism_ops['total_operations']}): {ops_text}.")

        commands = _dism_commands(entries)
        if commands:
            parts.append("Recent commands: " + " | ".join(commands) + ".")

        if dism_ops["scratch_directory"]:
            scratch_text = f"Scratch Dir: {dism_ops['scratch_directory']}"
            if dism_ops["scratch_warnings"]:
                scratch_text += f" (⚠️ {dism_ops['scratch_warnings'][0]})"
            parts.append(scratch_text + ".")

    codes = failing_codes(entries)
    if codes:
        names = _inline_names(entries)
        shown = []
        for code, n in codes.most_common(4):
            meaning = code_meaning(code, names)
            shown.append(f"0x{code:08X} x{n}" + (f" ({meaning})" if meaning else ""))
        parts.append("Failing codes: " + "; ".join(shown) + (f"; +{len(codes) - 4} more" if len(codes) > 4 else "") + ".")
    else:
        parts.append("No failing HRESULTs.")

    probes = package_probes(entries)
    if probes:
        named = ", ".join(f"{name} x{n}" for name, n in probes.most_common(3))
        parts.append(
            f"Routine: {sum(probes.values())} lookup(s) of packages not installed here "
            f"({named}), logged as CBS_E_INVALID_PACKAGE -- not a failure."
        )

    # Actionable Recommendation
    if kind == "cbs" and (failures["total_failures"] or markers or codes):
        parts.append("Actionable Remedy: Run 'DISM /Online /Cleanup-Image /RestoreHealth' followed by 'sfc /scannow'.")
    elif kind == "dism" and (dism_ops.get("scratch_warnings") or codes):
        if dism_ops.get("scratch_warnings"):
            parts.append("Actionable Remedy: Specify '/ScratchDir:<path>' on a drive with >1GB free space.")
        else:
            parts.append("Actionable Remedy: Run 'DISM /Online /Cleanup-Image /RestoreHealth /Source:...'.")

    return " ".join(parts)


def _dism_commands(entries: List[LogEntry], limit: int = 3) -> List[str]:
    found: List[Tuple] = []
    for e in entries:
        m = _DISM_COMMAND.search(e.message or "")
        if m:
            found.append((e.timestamp, m.group(1).strip()))
    found.sort(key=lambda p: p[0])
    out = []
    for when, cmd in found[-limit:]:
        cmd = re.sub(r"^\"?[^ ]*[Dd]ism(\.exe)?\"?\s*", "", cmd)
        out.append(f"{when:%m-%d %H:%M} {cmd[:70]}")
    return out


def _servicing_failure_card(raw: dict, esc) -> Optional[str]:
    failure_type = raw.get("failure_type")
    if not failure_type:
        return None
    labels = {
        "missing_payload": "Missing Payload File(s)",
        "manifest_not_found": "CBS Manifest Not Found",
        "store_corruption": "Component Store Corruption",
        "stage_failure": "Package Stage Transition Failure",
    }
    title = labels.get(failure_type, failure_type.replace("_", " ").title())
    pkg = raw.get("package")
    fp = raw.get("file_path")
    cause = raw.get("cause")
    remedy = raw.get("remedy")

    sem_err = semantic("error")
    sem_warn = semantic("warning")
    sem_succ = semantic("success")
    sem_info = semantic("info")

    card_html = (
        f'<div style="background-color: rgba(231, 76, 60, 0.15); border-left: 4px solid {sem_err}; padding: 10px; margin-bottom: 8px; border-radius: 4px;">'
        f'<b style="color: {sem_err}; font-size: 13px;">⚠️ Servicing Failure: {esc(title)}</b><br>'
    )
    if pkg:
        card_html += f"<b>Affected Package:</b> <code style='color: {sem_warn};'>{esc(str(pkg))}</code><br>"
    if fp:
        card_html += f"<b>Target File:</b> <code style='color: {sem_info};'>{esc(str(fp))}</code><br>"
    if cause:
        card_html += f"<b>Probable Cause:</b> {esc(str(cause))}<br>"
    if remedy:
        card_html += (
            f"<div style='margin-top: 6px; padding: 6px; background-color: rgba(231, 76, 60, 0.2); border-radius: 3px;'>"
            f"<b>Actionable Remedy:</b><br><code style='color: {sem_succ}; font-weight: bold;'>{esc(str(remedy))}</code>"
            f"</div>"
        )
    card_html += "</div>"
    return card_html


def _package_stage_card(raw: dict, esc) -> Optional[str]:
    if not (raw.get("target_state") or raw.get("transition")):
        return None
    target = raw.get("target_state", "")
    trans = raw.get("transition", target)
    pkg = raw.get("package", "")
    is_failed = str(target).lower() == "failed"
    color = semantic("error") if is_failed else semantic("info")
    bg = "rgba(231, 76, 60, 0.15)" if is_failed else "rgba(52, 152, 219, 0.15)"

    pkg_line = f"<b>Package:</b> <code>{esc(str(pkg))}</code><br>" if pkg else ""
    return f"""
    <div style="background-color: {bg}; border-left: 4px solid {color}; padding: 8px; margin-bottom: 8px; border-radius: 4px;">
        <b style="color: {color};">📦 Package Stage Transition:</b> {esc(str(trans))}<br>
        {pkg_line}
        <b>Target State:</b> <b>{esc(str(target))}</b><br>
    </div>
    """


def _dism_operation_card(raw: dict, esc) -> Optional[str]:
    if not raw.get("operation"):
        return None
    op = raw.get("operation")
    cmd = raw.get("command")
    tp = raw.get("target_package")
    tf = raw.get("target_feature")
    sem_succ = semantic("success")

    cmd_line = f"<b>Command Line:</b> <code>{esc(str(cmd))}</code><br>" if cmd else ""
    tp_line = f"<b>Target Package:</b> <code>{esc(str(tp))}</code><br>" if tp else ""
    tf_line = f"<b>Target Feature:</b> <code>{esc(str(tf))}</code><br>" if tf else ""
    return f"""
    <div style="background-color: rgba(46, 204, 113, 0.15); border-left: 4px solid {sem_succ}; padding: 8px; margin-bottom: 8px; border-radius: 4px;">
        <b style="color: {sem_succ};">🔧 DISM Operation:</b> <b>{esc(str(op))}</b><br>
        {cmd_line}
        {tp_line}
        {tf_line}
    </div>
    """


def _scratch_dir_card(raw: dict, esc) -> Optional[str]:
    if not (raw.get("scratch_warning") or raw.get("scratch_dir")):
        return None
    s_dir = raw.get("scratch_dir", "Standard")
    s_warn = raw.get("scratch_warning")
    s_free = raw.get("scratch_free_mb")

    bg = "rgba(243, 156, 18, 0.15)" if s_warn else "rgba(52, 152, 219, 0.15)"
    color = semantic("warning") if s_warn else semantic("info")
    free_line = f"<b>Free Space:</b> {s_free} MB<br>" if s_free is not None else ""
    warn_line = f"<b style='color: {semantic('error')};'>Warning:</b> {esc(str(s_warn))}<br>" if s_warn else ""
    rem = raw.get("remedy")
    rem_line = f"<b>Remedy:</b> <code style='color: {semantic('success')};'>{esc(str(rem))}</code><br>" if (s_warn and rem) else ""

    return f"""
    <div style="background-color: {bg}; border-left: 4px solid {color}; padding: 8px; margin-bottom: 8px; border-radius: 4px;">
        <b style="color: {color};">📁 DISM Scratch Directory:</b> <code>{esc(str(s_dir))}</code><br>
        {free_line}
        {warn_line}
        {rem_line}
    </div>
    """


def _code_translation_card(entry: LogEntry, esc) -> Optional[str]:
    found = explain(entry.message or "")
    names = _inline_names([entry])
    code_rows = []

    for code_text, meaning in found:
        try:
            val = int(code_text, 16) if code_text.startswith("0x") else int(code_text)
            adv = advice(val)
        except Exception:
            adv = None

        row = f"<b>{esc(code_text)}</b> — {esc(meaning)}<br>"
        if adv:
            row += (
                f"<div style='margin-left: 10px; margin-top: 4px; font-size: 11px;'>"
                f"<b>Cause:</b> {esc(adv.cause)}<br>"
                f"<b>Remedy:</b> <code style='color: {semantic('success')};'>{esc(adv.remedy)}</code><br>"
                f"</div>"
            )
        code_rows.append(row)

    if not code_rows:
        for code, name in names.items():
            code_rows.append(f"<b>0x{code:08X}</b> — {esc(name)}<br>")

    if not code_rows:
        return None

    return (
        f"<div style='background-color: rgba(127, 140, 141, 0.15); border-left: 4px solid {chrome('outline')}; padding: 8px; margin-bottom: 8px; border-radius: 4px;'>"
        f"<b style='color: {chrome('text')};'>Codes in this line:</b><br>"
        + "".join(code_rows)
        + "</div>"
    )


def detail_html(entry: LogEntry) -> str:
    """Rich detail HTML formatting for the Detail Panel Overview tab."""
    esc = html.escape
    raw = entry.raw or {}
    cards = []
    for fn in (_servicing_failure_card, _package_stage_card, _dism_operation_card, _scratch_dir_card):
        c = fn(raw, esc)
        if c:
            cards.append(c)
    cc = _code_translation_card(entry, esc)
    if cc:
        cards.append(cc)
    if not cards:
        return ""
    return "".join(cards) + "<hr>"
