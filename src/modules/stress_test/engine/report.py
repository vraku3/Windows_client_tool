"""Escaped, offline stability reports; no network content or executable markup."""
from datetime import datetime, timezone
import html
import json
import os
from pathlib import Path
import uuid


def default_report_dir():
    return Path(os.environ.get('APPDATA', str(Path.home()))) / 'WindowsTweaker' / 'stress'


def write_report(data, directory=None):
    directory = Path(directory) if directory is not None else default_report_dir()
    directory.mkdir(parents=True, exist_ok=True)
    stem = datetime.now(timezone.utc).strftime('stress-%Y%m%d-%H%M%S-') + uuid.uuid4().hex[:8]
    json_path, html_path = directory / f'{stem}.json', directory / f'{stem}.html'
    payload = json.dumps(data, indent=2, ensure_ascii=True, allow_nan=False)
    json_path.write_text(payload, encoding='utf-8')
    from core.semantic_colors import CHROME_PALETTES
    light = CHROME_PALETTES['light']
    title = html.escape(data['verdict'])
    # The app's own light palette, readable on paper -- no frozen colours.
    page = ('<!doctype html><html lang="en"><meta charset="utf-8">'
            '<meta http-equiv="Content-Security-Policy" content="default-src \'none\'; style-src \'unsafe-inline\'">'
            '<title>WinClientTool Stress Test</title><style>body{font:16px system-ui;'
            f"background:{light['surface']};color:{light['text']};margin:2em}}"
            'pre{white-space:pre-wrap;overflow-wrap:anywhere}</style>'
            f'<h1>{title}</h1><p>{html.escape(data["reason"])}</p>'
            '<p>Integer/hash/compression load -- not an AVX floating-point torture like Linpack. '
            'Only allocated memory is tested; Windows and other applications retain untested memory. '
            'MemTest86 at boot is the full memory test. Passing this run is not a guarantee of stability.</p>'
            f'<h2>Run evidence (JSON)</h2><pre>{html.escape(payload)}</pre></html>')
    html_path.write_text(page, encoding='utf-8')
    return json_path, html_path
