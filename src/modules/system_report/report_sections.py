"""System Report sections (Qt-free): the asset, disk, restore and software
sections, the aggregate findings, and the HTML / Markdown renderers.

Each section reads independently.  A section that could not be read carries an
``error`` and renders as "could not be read: reason", never as an empty table
that looks like "nothing to report".
"""
from __future__ import annotations

import html
import logging
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Sequence, Tuple

logger = logging.getLogger(__name__)


@dataclass
class Section:
    title: str
    headers: List[str] = field(default_factory=list)
    rows: List[List[str]] = field(default_factory=list)
    error: str = ""
    note: str = ""


@dataclass
class ReportFinding:
    area: str
    severity: str
    title: str
    detail: str = ""


def _kv(title: str, pairs: Sequence[Tuple[str, str]], note: str = "") -> Section:
    return Section(title, ["Property", "Value"], [[str(k), str(v)] for k, v in pairs], note=note)


def _findings(area: str, found: Sequence) -> List[ReportFinding]:
    return [ReportFinding(area, f.severity, f.title, f.detail) for f in found]


# --------------------------------------------------------------------------
# Section builders.  Each returns (sections, findings); failures are contained.
# --------------------------------------------------------------------------

def hardware_sections() -> Tuple[List[Section], List[ReportFinding]]:
    from modules.hardware_inventory import asset_parse as ap
    from modules.hardware_inventory import asset_reader as ar
    from modules.hardware_inventory import hardware_reader as hr
    sections: List[Section] = []
    findings: List[ReportFinding] = []
    try:
        record, found = ar.read_asset_record()
        sections.append(_kv("Asset record", list(record.items())))
        findings += _findings("Hardware", found)
    except Exception as e:  # WMI/registry can fail in many ways; contain it
        logger.warning("asset record failed: %s", e)
        sections.append(Section("Asset record", error=str(e)))
    try:
        mem = ar.read_memory_slots()
        rows = [[s.locator, s.bank, f"{s.capacity_bytes / 1024 ** 3:.0f} GB" if s.populated else "empty",
                 s.mem_type, f"{s.rated_mhz or ''}", f"{s.configured_mhz or ''}", s.part_number, s.serial]
                for s in mem.slots]
        sections.append(Section("Memory slots", ["Slot", "Bank", "Size", "Type", "Rated MHz",
                                                 "Configured MHz", "Part number", "Serial"], rows,
                                error=mem.error if not mem.slots else "", note=mem.error))
    except Exception as e:
        logger.warning("memory slots failed: %s", e)
        sections.append(Section("Memory slots", error=str(e)))
    try:
        monitors, err = ar.read_monitors()
        sections.append(Section("Monitors", ["Make", "Model", "Serial", "Manufactured", "Status"],
                                [[m.make, m.name, m.serial, m.manufactured,
                                  {True: "Connected", False: "Remembered", None: "Unknown"}[m.active]]
                                 for m in monitors], note=err))
    except Exception as e:
        logger.warning("monitors failed: %s", e)
        sections.append(Section("Monitors", error=str(e)))
    try:
        fw = ar.read_firmware()
        battery, note = ar.read_battery()
        sections.append(_kv("Firmware and security",
                            ap.firmware_rows(fw, battery, note, hr.get_bios_info())))
    except Exception as e:
        logger.warning("firmware failed: %s", e)
        sections.append(Section("Firmware and security", error=str(e)))
    return sections, findings


def disk_sections() -> Tuple[List[Section], List[ReportFinding]]:
    from modules.disk_health import disk_reader as dr
    try:
        rep = dr.read_disk_report()
    except dr.DiskReadError as e:
        return [Section("Disk health", error=str(e))], []
    drives = [[d.name, d.bus, d.size_text, d.health or "?", f"{d.temperature} C" if d.temperature else "n/a",
               "n/a" if d.wear_percent is None else f"{d.wear_percent}%", dr.power_on_hours_short(d.power_on_hours),
               dr.disk_verdict(d)[1]] for d in rep.disks if not d.is_virtual]
    vols = [[v.display, v.label, v.filesystem, dr.format_size(v.size_bytes),
             f"{dr.format_size(v.free_bytes)} ({v.free_percent:.0f}%)" if v.free_percent is not None else "n/a",
             v.bitlocker or "not read"] for v in rep.volumes if v.letter]
    return ([Section("Disk health", ["Drive", "Bus", "Size", "Health", "Temp", "Life used", "Power-on", "Verdict"],
                     drives),
             Section("Volumes", ["Volume", "Label", "FS", "Size", "Free", "BitLocker"], vols)],
            _findings("Disks", dr.all_findings(rep)))


def restore_sections() -> Tuple[List[Section], List[ReportFinding]]:
    from modules.restore_manager import restore_analysis as ra
    try:
        infos = ra.analyze_points(ra.read_restore_points())
    except ra.RestoreReadError as e:
        return [Section("System Restore", error=str(e))], []
    protection, _perr = ra.read_protection()
    storage, serr = ra.read_shadow_storage()
    found = ra.restore_findings(infos, protection, storage, serr, ra.read_policy_disabled(),
                                ra.read_frequency_minutes())
    rows = [[f"{p.created:%Y-%m-%d %H:%M}" if p.created else "unknown", p.kind, p.description,
             "n/a" if p.age_days is None else f"{p.age_days:.0f} d"] for p in reversed(infos)]
    sections = [Section("System Restore", ["Created", "Type", "Description", "Age"], rows)]
    if storage is not None:
        sections.append(Section("Shadow storage", ["Volume", "Used", "Allocated", "Maximum"],
                                [[s.volume, ra.format_bytes(s.used), ra.format_bytes(s.allocated),
                                  "unbounded" if s.unbounded else ra.format_bytes(s.maximum)] for s in storage]))
    else:
        sections.append(Section("Shadow storage", error=serr))
    return sections, _findings("Restore", found)


def software_sections() -> Tuple[List[Section], List[ReportFinding]]:
    from modules.software_inventory import software_analysis as sa
    from modules.software_inventory import software_reader as sr
    try:
        rows = sa.analyze(sr.fetch_software_inventory())
    except OSError as e:
        return [Section("Software", error=str(e))], []
    counts = sa.chip_counts(rows)
    summary = _kv("Software", [(k, str(v)) for k, v in counts.items()])
    flagged = [r for r in rows if r.family or r.superseded_by or "End of life" in r.tags]
    table = Section("Runtimes, duplicates and end-of-life software",
                    ["Name", "Version", "Family", "Note"],
                    [[r.name, r.entry.version, r.family, sa._note(r)] for r in flagged])
    return [summary, table], _findings("Software", sa.software_findings(rows))


def stability_sections(days: int = 30, reader: Optional[Callable] = None
                       ) -> Tuple[List[Section], List[ReportFinding]]:
    """Unexpected shutdowns and blue screens, one row per INCIDENT.

    The first thing asked about a machine on a ticket is "does it crash", and
    the report had no answer. Measured here: 7 raw events in 30 days were 4
    incidents -- two power losses, one held power button, one with no detail.
    """
    from core import stability
    incidents, reason = (reader or stability.read_incidents)(days)
    title = f"Stability (last {days} days)"
    if incidents is None:
        return [Section(title, error=reason)], [
            ReportFinding("Stability", "info", "Crash history could not be read", reason)]
    rows = [[f"{i.when:%Y-%m-%d %H:%M}", i.summary, ", ".join(str(e) for e in i.event_ids)]
            for i in incidents]
    section = Section(title, ["When", "What happened", "Events"], rows,
                      note="" if rows else "No unexpected shutdowns or blue screens.")
    if not incidents:
        return [section], []
    counts: Dict[str, int] = {}
    for i in incidents:
        counts[i.cause] = counts.get(i.cause, 0) + 1
    labels = {stability.BUGCHECK: "blue screen", stability.POWER_LOSS: "power loss/hard reset",
              stability.POWER_BUTTON: "power button held", stability.SLEEP: "died in sleep",
              stability.UNKNOWN: "no detail recorded"}
    breakdown = ", ".join(f"{n} {labels.get(c, c)}" for c, n in sorted(counts.items(), key=lambda kv: -kv[1]))
    severity = "error" if counts.get(stability.BUGCHECK) else "warning"
    return [section], [ReportFinding(
        "Stability", severity, f"{len(incidents)} unexpected shutdown(s) in {days} days",
        f"{breakdown}; most recent {incidents[0].when:%Y-%m-%d %H:%M}")]


def health_sections(reader: Optional[Callable] = None) -> Tuple[List[Section], List[ReportFinding]]:
    """The System Health pane's own checks: pending restart and why, time sync,
    WMI repository, commit, CBS corruption, stopped auto-start services,
    WHEA hardware errors. Reused as-is so the report and the pane never disagree."""
    from modules.system_health import findings as health
    found = (reader or health.full_findings)()
    rows = [[f.severity, f.title, f.detail] for f in found]
    return ([Section("System health checks", ["Severity", "Finding", "Detail"], rows,
                     note="" if rows else "Every check passed.")],
            [ReportFinding("Health", f.severity, f.title, f.detail) for f in found])


BUILDERS: List[Callable[[], Tuple[List[Section], List[ReportFinding]]]] = [
    hardware_sections, stability_sections, health_sections, disk_sections,
    restore_sections, software_sections,
]


def collect_sections(builders: Optional[Sequence[Callable]] = None) -> Tuple[List[Section], List[ReportFinding]]:
    """Run every builder; one failing builder costs its own sections only."""
    sections: List[Section] = []
    findings: List[ReportFinding] = []
    for build in (builders if builders is not None else BUILDERS):
        try:
            s, f = build()
        except Exception as e:  # a builder bug must not lose the other sections
            logger.exception("report section %s failed", getattr(build, "__name__", build))
            sections.append(Section(getattr(build, "__name__", "section"), error=str(e)))
            continue
        sections += s
        findings += f
    return sections, findings


# --------------------------------------------------------------------------
# Rendering
# --------------------------------------------------------------------------

_ORDER = {"error": 0, "warning": 1, "info": 2}


def _attention(findings: Sequence[ReportFinding]) -> List[ReportFinding]:
    return sorted((f for f in findings if f.severity in ("error", "warning")),
                  key=lambda f: _ORDER.get(f.severity, 3))


def findings_html(findings: Sequence[ReportFinding]) -> str:
    items = _attention(findings)
    if not items:
        return "<h2>Needs attention</h2><p>Nothing flagged by the computed checks.</p>"
    lis = "".join(f"<li><b>[{html.escape(f.severity)}] {html.escape(f.area)}: {html.escape(f.title)}</b> "
                  f"{html.escape(f.detail)}</li>" for f in items)
    return f"<h2>Needs attention</h2><ul>{lis}</ul>"


def sections_html(sections: Sequence[Section]) -> str:
    out: List[str] = []
    for s in sections:
        out.append(f"<h2>{html.escape(s.title)}</h2>")
        if s.error:
            out.append(f"<p>Could not be read: {html.escape(s.error)}</p>")
            continue
        if s.note:
            out.append(f"<p>{html.escape(s.note)}</p>")
        if not s.rows:
            if not s.note:
                out.append("<p>None.</p>")
            continue
        head = "".join(f"<th>{html.escape(h)}</th>" for h in s.headers)
        body = "".join("<tr>" + "".join(f"<td>{html.escape(str(c))}</td>" for c in row) + "</tr>"
                       for row in s.rows)
        out.append(f"<table><tr>{head}</tr>{body}</table>")
    return "\n".join(out)


def _md_cell(text) -> str:
    return (str(text) or "-").replace("|", "\\|").replace("\n", " ")


def sections_markdown(sections: Sequence[Section]) -> str:
    out: List[str] = []
    for s in sections:
        out += [f"#### {s.title}", ""]
        if s.error:
            out += [f"_Could not be read: {s.error}_", ""]
            continue
        if s.note:
            out += [f"_{s.note}_", ""]
        if not s.rows:
            if not s.note:
                out += ["None.", ""]
            continue
        out.append("| " + " | ".join(s.headers) + " |")
        out.append("|" + "---|" * len(s.headers))
        out += ["| " + " | ".join(_md_cell(c) for c in row) + " |" for row in s.rows]
        out.append("")
    return "\n".join(out)


def findings_markdown(findings: Sequence[ReportFinding]) -> str:
    items = _attention(findings)
    if not items:
        return "**Needs attention**\n\n- Nothing flagged by the computed checks.\n"
    lines = ["**Needs attention**", ""]
    lines += [f"- [{f.severity}] {f.area}: {f.title}. {f.detail}".rstrip() for f in items]
    return "\n".join(lines) + "\n"


def build_markdown(data: Dict) -> str:
    """Ticket-ready Markdown for a whole report dict (see report_module)."""
    lines = [f"## System report: {data.get('hostname', '')}", "",
             f"Generated {data.get('generated', '')}  |  {data.get('os_name', '')} "
             f"({data.get('architecture', '')}), build {data.get('os', '')}  |  "
             f"uptime {data.get('uptime_hours', '?')} h", "",
             "| | |", "|---|---|",
             f"| CPU | {_md_cell(data.get('cpu_name', ''))} ({data.get('cpu_cores', '?')} cores / "
             f"{data.get('cpu_threads', '?')} threads) |",
             f"| Memory | {data.get('ram_total_gb', '?')} GB, {data.get('ram_percent', '?')}% used |",
             f"| Antivirus | {_md_cell(data.get('antivirus', ''))} |",
             f"| Firewall | {_md_cell(data.get('firewall', ''))} |",
             f"| Installed packages | {data.get('software_count', '?')} |", ""]
    lines.append(findings_markdown(data.get("findings", [])))
    lines.append(sections_markdown(data.get("sections", [])))
    return "\n".join(lines).rstrip() + "\n"
