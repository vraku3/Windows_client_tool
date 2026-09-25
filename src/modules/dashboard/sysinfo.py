"""System Information, gathered from the Hardware Info readers. No Qt.

One list of (section, rows) that the tab renders and the "Copy all" button
turns into plain text for a ticket. Each section is read on its own, so one
WMI class that refuses costs that section -- shown with its reason -- and never
the whole page.
"""
import logging
from typing import Callable, List, Tuple

logger = logging.getLogger(__name__)

Section = Tuple[str, List[Tuple[str, str]]]


def _rows_from_dicts(items, columns) -> List[Tuple[str, str]]:
    rows = []
    for n, item in enumerate(items, 1):
        head = str(item.get(columns[0], "")) or f"#{n}"
        rest = "   ".join(f"{c}: {item.get(c, '')}" for c in columns[1:] if item.get(c, "") != "")
        rows.append((head, rest))
    return rows


def _memory(hr) -> List[Tuple[str, str]]:
    summary, sticks = hr.get_memory_info()
    rows = list(summary)
    for stick in sticks:
        rows.append((stick.get("Bank") or "DIMM",
                     f"{stick.get('Capacity', '')}  {stick.get('Speed', '')}  "
                     f"{stick.get('Manufacturer', '')}  {stick.get('PartNumber', '')}".strip()))
    return rows


def _storage(hr) -> List[Tuple[str, str]]:
    drives, partitions = hr.get_storage_info()
    rows = _rows_from_dicts(drives, ["Model", "Size", "Interface", "Serial"])
    rows += _rows_from_dicts(partitions, ["Mount", "FS", "Total", "Used", "Free"])
    return rows


def collect_sections() -> List[Section]:
    """Every section, best effort. Needs a COM-initialised thread (WMI)."""
    from modules.hardware_inventory import hardware_reader as hr
    plan: List[Tuple[str, Callable[[], List[Tuple[str, str]]]]] = [
        ("System", lambda: hr.get_overview()),
        ("Processor", lambda: hr.get_cpu_info()),
        ("Memory", lambda: _memory(hr)),
        ("Graphics", lambda: _rows_from_dicts(
            hr.get_gpu_info(), ["Name", "RAM", "Driver Version", "Driver Date", "Resolution"])),
        ("Storage", lambda: _storage(hr)),
        ("Network adapters", lambda: _rows_from_dicts(
            hr.get_network_info(), ["Name", "IP", "MAC", "Speed", "Up"])),
        ("BIOS and board", lambda: hr.get_bios_info()),
    ]
    sections: List[Section] = []
    for title, read in plan:
        try:
            rows = read()
        except Exception as e:      # a WMI class can refuse; say which and why
            logger.warning("system info section %s failed: %s", title, e)
            rows = [("Could not be read", str(e))]
        sections.append((title, list(rows) or [("Nothing reported", "")]))
    return sections


def to_text(sections: List[Section]) -> str:
    out: List[str] = []
    for title, rows in sections:
        out.append(f"== {title} ==")
        out += [f"{label}: {value}" if value != "" else label for label, value in rows]
        out.append("")
    return "\n".join(out).rstrip() + "\n"
