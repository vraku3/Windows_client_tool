"""Offline, escaped Cleanup exports. No scanning, deletion or browser launching.

Public API: scan_html(snapshot, ...), history_html(entries, ...),
export_scan(snapshot, directory=None, ...) -> (html_path, csv_path),
export_history(entries, directory=None, ...) -> html_path, list_reports(),
retain_reports(directory=None, limit=30). Callers may open the returned paths.
REPORT_CSS is shared with other report producers. Summary files and clean
history remain separate: scan observations are never counted as freed bytes.
"""
from __future__ import annotations

import csv
import math
import os
import platform
import re
from collections import defaultdict
from datetime import datetime
from html import escape
from pathlib import Path

from core.semantic_colors import CHROME_PALETTES, SEMANTIC_PALETTES
from .cleanup_scanner.knowledge import detail_html, entry_for


def chrome(role, theme):
    return CHROME_PALETTES[theme][role]


def semantic(role, theme):
    return SEMANTIC_PALETTES[theme][role]

# Values are resolved from the light semantic palette so the saved artifact
# remains readable independently of the app theme when opened later.
REPORT_CSS = f'''
body {{ font-family: system-ui, sans-serif; max-width: 1200px; margin: 2em auto;
padding: 0 1em; background: {chrome('surface', 'light')}; color: {chrome('text', 'light')}; }}
table {{ border-collapse: collapse; width: 100%; margin: 1em 0; }}
th, td {{ text-align: left; padding: .5em; border-bottom: 1px solid {chrome('outline', 'light')};
overflow-wrap: anywhere; vertical-align: top; }}
.kpis {{ display: flex; flex-wrap: wrap; gap: 1em; }}
.kpi {{ padding: 1em; border: 1px solid {chrome('outline', 'light')}; }}
svg {{ max-width: 100%; height: auto; }}
.safe {{ color: {semantic('success', 'light')}; }}
.caution {{ color: {semantic('warning', 'light')}; }}
.danger {{ color: {semantic('error', 'light')}; }}
'''
_REPORT_NAME = re.compile(r'cleanup-(scan|history)-\d{8}-\d{6}\.html\Z')


def _e(value):
    return escape(str(value), quote=True)


def _size(value):
    from core.formatting import format_size
    return format_size(value)


def slice_angles(values):
    """Positive slices only, degrees; final remainder makes the total 360."""
    values = [value for value in values if value > 0]
    if not values:
        return []
    total = sum(values)
    angles = [value / total * 360 for value in values]
    angles[-1] = 360 - sum(angles[:-1])
    return angles


def _donut_svg(rows):
    categories = defaultdict(int)
    for row in rows:
        categories[row.category] += row.size
    ranked = sorted(categories.items(), key=lambda pair: pair[1], reverse=True)
    if len(ranked) > 8:
        ranked = ranked[:8] + [(f'Other ({len(ranked)-8})', sum(size for _, size in ranked[8:]))]
    ranked = [(label, size) for label, size in ranked if size > 0]
    if not ranked:
        return '<p>No reclaimable space in this snapshot.</p>'
    pieces = ['<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 640 230" role="img" aria-label="Space by category">']
    start = -90.0
    colors = [semantic(role, 'light') for role in ('info', 'success', 'warning', 'error', 'match')]
    total = sum(size for _, size in ranked)
    for index, ((label, size), angle) in enumerate(zip(ranked, slice_angles([size for _, size in ranked]))):
        # Two arcs also handle a single 360-degree slice (an SVG arc with the
        # same start/end points is otherwise invisible).
        x0, y0 = 110 + 90 * math.cos(math.radians(start)), 110 + 90 * math.sin(math.radians(start))
        middle, end = start + angle / 2, start + angle
        xm, ym = 110 + 90 * math.cos(math.radians(middle)), 110 + 90 * math.sin(math.radians(middle))
        x1, y1 = 110 + 90 * math.cos(math.radians(end)), 110 + 90 * math.sin(math.radians(end))
        color = colors[index % len(colors)]
        pieces.append(f'<path d="M 110 110 L {x0:.6f} {y0:.6f} A 90 90 0 0 1 {xm:.6f} {ym:.6f} '
                      f'A 90 90 0 0 1 {x1:.6f} {y1:.6f} Z" fill="{color}"><title>{_e(label)}: {_e(_size(size))}</title></path>')
        y = 22 + index * 23
        pieces.append(f'<text x="220" y="{y}">{index + 1}. {_e(label)}: {_e(_size(size))} ({size/total:.1%})</text>')
        start = end
    pieces.append(f'<circle cx="110" cy="110" r="52" fill="{chrome("surface", "light")}"/>'
                  f'<text x="110" y="114" text-anchor="middle">{_e(_size(total))}</text></svg>')
    return ''.join(pieces)


def _bars_svg(rows):
    rows = sorted(rows, key=lambda row: row.size, reverse=True)[:10]
    if not rows:
        return ''
    maximum = max((row.size for row in rows), default=1) or 1
    pieces = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1000 {len(rows)*48}" role="img" aria-label="Top folders">']
    for index, row in enumerate(rows):
        y = index * 48
        color = semantic({'safe': 'success', 'caution': 'warning'}.get(row.effective_safety, 'error'), 'light')
        short = row.path if len(row.path) <= 90 else '…' + row.path[-89:]
        badge = {'safe': 'Safe', 'caution': 'Review'}.get(row.effective_safety, 'Risky')
        pieces.append(f'<text x="0" y="{y+15}">{_e(short)} · {_e(_size(row.size))} · {badge}</text>'
                      f'<rect x="0" y="{y+22}" width="{int(row.size/maximum*950)}" height="14" fill="{color}">'
                      f'<title>{_e(row.path)} — {badge}</title></rect>')
    return ''.join(pieces) + '</svg>'


def _trend_svg(entries, summaries=()):
    # Trend observations are independently dated; align both series on a
    # timestamp axis rather than assuming that every scan caused a clean.
    observations = [(str(e.get('at', '')), int(e.get('bytes', e.get('freed_bytes', 0)) or 0), 'clean') for e in entries]
    observations += [(str(e.get('at', '')), int(e.get('total_size', 0) or 0), 'scan') for e in summaries]
    observations.sort(key=lambda row: row[0])
    if len(observations) < 2:
        return '<p>Not enough history yet</p>'
    maximum = max(sum(max(0, value) for _, value, kind in observations if kind == 'clean'),
                  max(value for _, value, _ in observations), 1)
    pieces = ['<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1000 250" role="img" aria-label="Freed per clean, cumulative freed, reclaimable at scan">']
    cumulative = 0
    points, scans = [], []
    for index, (at, value, kind) in enumerate(observations):
        x = 30 + int(index * 940 / max(1, len(observations)-1))
        if kind == 'clean':
            cumulative += value
            height = int(value / maximum * 190)
            pieces.append(f'<rect x="{x-4}" y="{210-height}" width="8" height="{height}" fill="{semantic("success", "light")}">'
                          f'<title>{_e(at)}: Freed {_e(_size(value))}</title></rect>')
        else:
            scans.append(f'{x},{210-int(value/maximum*190)}')
            pieces.append(f'<circle cx="{x}" cy="{210-int(value/maximum*190)}" r="4" fill="{semantic("warning", "light")}">'
                          f'<title>{_e(at)}: Reclaimable {_e(_size(value))}</title></circle>')
        points.append(f'{x},{210-int(cumulative/maximum*190)}')
    pieces.append(f'<polyline points="{" ".join(points)}" fill="none" stroke="{semantic("info", "light")}" stroke-width="2"/>')
    if scans:
        pieces.append(f'<polyline points="{" ".join(scans)}" fill="none" stroke="{semantic("warning", "light")}" stroke-width="2"/>')
    pieces.append('<text x="20" y="245">Columns: freed per clean · Line: cumulative freed · Dots/line: reclaimable at scan</text></svg>')
    return ''.join(pieces)


def _document(title, body):
    return (f'<!doctype html>\n<html lang="en"><head><meta charset="utf-8"><title>{_e(title)}</title>'
            f'<style>{REPORT_CSS}</style></head><body><h1>{_e(title)}</h1>{body}</body></html>\n')


def scan_html(snapshot, *, machine=None, recommendations=(), not_offered=None):
    machine = platform.node() if machine is None else machine
    elevation = 'Unknown' if snapshot.elevated is None else ('Elevated' if snapshot.elevated else 'Unelevated')
    body = f'<p>{_e(machine)} · {_e(snapshot.at)} · Preset: {_e(snapshot.preset)} · {elevation}</p>'
    body += '<div class="kpis">' + ''.join(f'<div class="kpi">{label}<br><b>{_e(value)}</b></div>' for label, value in (
        ('Reclaimable', _size(snapshot.total_size)), ('Safe', _size(snapshot.safe_size)),
        ('Needs review', _size(snapshot.review_size)), ('Locations', len(snapshot.unique_rows)))) + '</div>'
    body += '<h2>Space by category</h2>' + _donut_svg(snapshot.unique_rows)
    body += '<h2>Top folders</h2>' + _bars_svg(snapshot.unique_rows)
    body += '<h2>Recommendations</h2>'
    for recommendation in recommendations:
        facts = recommendation if isinstance(recommendation, dict) else vars(recommendation)
        body += f'<h3>{_e(facts.get("title", ""))}</h3><p>{_e(facts.get("evidence", facts.get("description", "")))}</p>'
    if not recommendations:
        body += '<p>No recommendations supplied.</p>'
    tabs = dict.fromkeys(row.tab for row in snapshot.rows)
    for tab in tabs:
        body += f'<h2>{_e(tab)}</h2><table><tr><th>Location</th><th>Size</th><th>Safety</th><th>Verdict / what / paths</th></tr>'
        for row in snapshot.rows:
            if row.tab != tab:
                continue
            entry = entry_for(row.id)
            memberships = snapshot.tabs_for(row.path)
            note = 'Also listed in: ' + ', '.join(t for t in memberships if t != tab) if len(memberships) > 1 else ''
            facts = vars(row) | {'size_text': _size(row.size), 'paths': [row.path]}
            body += (f'<tr><td>{_e(row.label)}<p>{_e(note)}</p></td><td>{_e(_size(row.size))}</td>'
                     f'<td>{_e(row.effective_safety)}</td><td>{detail_html(entry, facts)}</td></tr>')
        body += '</table>'
    body += '<h2>Not offered</h2>'
    for item in snapshot.not_offered if not_offered is None else not_offered:
        facts = item if isinstance(item, dict) else {'label': str(item)}
        body += f'<p>{_e(facts.get("label", ""))}: {_e(facts.get("reason", ""))}</p>'
    return _document('Cleanup scan report', body)


def history_html(entries, *, machine=None, summaries=()):
    entries = list(entries)
    body = f'<p>{_e(platform.node() if machine is None else machine)}</p>' + _trend_svg(entries, summaries)
    for entry in entries:
        body += (f'<h2>{_e(entry.get("at", "Unknown time"))}</h2><p>Freed {_e(_size(entry.get("bytes", entry.get("freed_bytes", 0))))}'
                 f' · {_e(entry.get("count", 0))} items · {"Cancelled" if entry.get("cancelled") else "Completed"}</p>')
        for title, key in (('Largest paths', 'largest'), ('In use', 'locked'), ('Access denied', 'denied'), ('Refused', 'refused')):
            body += f'<h3>{title}</h3><ul>'
            for item in entry.get(key, ()) or ():
                text = ' · '.join(str(v) for v in item) if isinstance(item, (list, tuple)) else str(item)
                body += f'<li>{_e(text)}</li>'
            body += '</ul>'
        body += '<h3>In-use holders</h3><ul>' + ''.join(f'<li>{_e(name)}: {_e(count)}</li>' for name, count in (entry.get('owners') or {}).items()) + '</ul>'
        body += '<p>' + ' · '.join(f'{label}: {_e(entry.get(key, "Not recorded"))}' for label, key in (
            ('In use', 'locked_count'), ('Access denied', 'denied_count'), ('Recent kept', 'kept_recent'), ('Cloud-only skipped', 'cloud_only'))) + '</p>'
    return _document('Cleanup history report', body)


def reports_directory():
    return Path(os.environ.get('APPDATA') or Path.home()) / 'WindowsTweaker' / 'cleanup' / 'reports'


def list_reports(directory=None):
    folder = Path(directory) if directory is not None else reports_directory()
    if not folder.exists():
        return []
    return sorted((p for p in folder.iterdir() if _REPORT_NAME.fullmatch(p.name) and p.is_file() and not p.is_symlink()),
                  key=lambda p: (p.name[-20:], p.name), reverse=True)


def retain_reports(directory=None, limit=30):
    """Only exact report names in this folder are eligible; no recursive delete."""
    for path in list_reports(directory)[max(0, limit):]:
        path.unlink()


def _destination(kind, directory):
    from datetime import timedelta
    folder = Path(directory) if directory is not None else reports_directory()
    folder.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now()
    # Keep the exact retention grammar even for repeated exports in one second.
    while True:
        path = folder / f'cleanup-{kind}-{stamp:%Y%m%d-%H%M%S}.html'
        if not path.exists() and not path.with_suffix('.csv').exists():
            return path
        stamp += timedelta(seconds=1)


def export_scan(snapshot, *, directory=None, **options):
    path = _destination('scan', directory)
    path.write_text(scan_html(snapshot, **options), encoding='utf-8', newline='\n')
    csv_path = path.with_suffix('.csv')
    with csv_path.open('w', encoding='utf-8', newline='') as stream:
        writer = csv.writer(stream, lineterminator='\n')
        writer.writerow(('Id', 'Label', 'Tab', 'Category', 'Safety', 'Effective safety', 'Path', 'Size', 'Selected'))
        for row in snapshot.rows:
            values = (row.id, row.label, row.tab, row.category, row.safety, row.effective_safety, row.path, row.size, row.selected)
            writer.writerow("'" + value if isinstance(value, str) and value.lstrip().startswith(('=', '+', '-', '@')) else value for value in values)
    retain_reports(path.parent)
    return path, csv_path


def export_history(entries, *, directory=None, **options):
    path = _destination('history', directory)
    path.write_text(history_html(entries, **options), encoding='utf-8', newline='\n')
    retain_reports(path.parent)
    return path
