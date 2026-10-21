"""Lazy, Qt-free research metadata. Research informs presentation, never deletion.

JSON keys are catalog ids, handwritten names without ``scan_``, or browser:type.
Missing research is explicit rather than inferred from a folder name.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from html import escape
from pathlib import Path
from urllib.parse import urlsplit

logger = logging.getLogger(__name__)
_DIRECTORY = Path(__file__).parent / 'definitions' / 'knowledge'
_entries: dict[str, Knowledge] | None = None
_LEVELS = {'safe': 0, 'caution': 1, 'danger': 2}


@dataclass(frozen=True)
class Knowledge:
    what: str = ''
    used_for: str = ''
    verdict: str = 'caution'
    risk: str = ''
    before_cleaning: str = ''
    regenerates: bool = False
    sources: tuple[str, ...] = ()
    catalog_concern: str = ''

    def __post_init__(self):
        for key in ('what', 'used_for', 'risk', 'before_cleaning', 'catalog_concern'):
            value = getattr(self, key)
            if not isinstance(value, str) or len(value.split()) > 65:
                raise ValueError(f'{key}: expected text of at most 65 words')
            object.__setattr__(self, key, value.strip())
        if self.verdict not in _LEVELS:
            raise ValueError('invalid verdict')
        if type(self.regenerates) is not bool:
            raise ValueError('regenerates must be boolean')
        if not isinstance(self.sources, (list, tuple)):
            raise ValueError('sources must be a sequence')
        urls = []
        for url in self.sources:
            if not isinstance(url, str):
                raise ValueError('source must be text')
            url = url.strip()
            parsed = urlsplit(url)
            if parsed.scheme != 'https' or not parsed.netloc or any(c.isspace() for c in url):
                raise ValueError('sources must be https URLs')
            urls.append(url)
        object.__setattr__(self, 'sources', tuple(urls))


def entry_for(id: str) -> Knowledge | None:
    """Return cached metadata; bad files/rows are logged and skipped."""
    global _entries
    if _entries is None:
        entries = {}
        try:
            paths = sorted(_DIRECTORY.glob('*.json'))
        except OSError as exc:
            logger.warning('Cannot enumerate cleanup knowledge: %s', exc)
            paths = []
        for path in paths:
            try:
                payload = json.loads(path.read_text(encoding='utf-8'))
                if not isinstance(payload, dict):
                    raise ValueError('expected an id-keyed object')
                for key, raw in payload.items():
                    try:
                        if key in entries or not isinstance(raw, dict):
                            raise ValueError('duplicate id or non-object entry')
                        entries[key] = Knowledge(**raw)
                    except (TypeError, ValueError) as exc:
                        logger.warning('Invalid cleanup knowledge %s:%s: %s', path.name, key, exc)
            except (OSError, ValueError) as exc:
                logger.warning('Cannot load cleanup knowledge %s: %s', path, exc)
        _entries = entries
    return _entries.get(id)


def id_for_scanner(fn) -> str:
    spec = getattr(fn, 'spec', None)
    return spec.id if spec is not None else getattr(fn, '__name__', '').removeprefix('scan_')


def effective_safety(catalog: str, kb: Knowledge | None) -> str:
    """Unknown catalog safety fails closed; research can only increase caution."""
    return max((catalog if catalog in _LEVELS else 'danger', kb.verdict if kb else 'safe'),
               key=_LEVELS.__getitem__)


def _sections(entry):
    if entry is None:
        return '<p>Not yet researched</p>'
    sections = [('What', entry.what), ('Used for', entry.used_for), ('Risk', entry.risk),
                ('Before cleaning', entry.before_cleaning),
                ('Comes back', 'Yes, automatically' if entry.regenerates else 'No. Deleted data does not come back')]
    return ''.join(f'<p><b>{title}:</b> {escape(value)}</p>' for title, value in sections if value)


def tooltip_html(entry: Knowledge | None, label: str, safety: str, size_text: str) -> str:
    return (f'<b>{escape(str(label))}</b> [{escape(str(safety))}]'
            + _sections(entry) + f'<p>{escape(str(size_text))}</p><p>ⓘ for sources and paths</p>')


def detail_html(entry: Knowledge | None, item_facts) -> str:
    """Render an injected mapping (or dataclass); never reads paths itself.

    Accepted facts: label, description, size_text, share, safety, path/paths,
    is_dir, keep_root, keep_newer_than_hours, id and category.
    """
    facts = item_facts if isinstance(item_facts, dict) else vars(item_facts)
    def e(key, default=''):
        return escape(str(facts.get(key, default)))
    shown_safety = effective_safety(facts.get('safety', 'danger'), entry)
    body = f'<h2>{e("label")}</h2><p>{escape(shown_safety)} · {e("size_text")} · {e("share")}</p>'
    body += _sections(entry)
    if entry is None:
        body += f'<p>{e("description")}</p>'
    operation = ('Contents deleted; root kept.' if facts.get('keep_root', True) else 'Folder and contents deleted.')
    if not facts.get('is_dir', True):
        operation = 'This file is deleted.'
    body += '<h3>What this tool will do</h3><p>' + operation + '</p>'
    if facts.get('keep_newer_than_hours'):
        body += f'<p>Files newer than {e("keep_newer_than_hours")} hours are kept.</p>'
    body += '<p>Links are unlinked, never followed. Cloud-only files are skipped.</p><h3>Paths</h3>'
    paths = facts.get('paths', [facts['path']] if facts.get('path') else [])
    if isinstance(paths, str):
        paths = [paths]
    body += ''.join(f'<p>{escape(str(path))}</p>' for path in paths)
    if entry and entry.sources:
        body += '<h3>Sources</h3>' + ''.join(
            f'<p><a href="{escape(url, quote=True)}">{escape(urlsplit(url).netloc)}</a></p>' for url in entry.sources)
    body += f'<p>Catalog id: {e("id")} · Category: {e("category")}</p>'
    if entry and entry.catalog_concern:
        body += f'<p>{escape(entry.catalog_concern)}</p>'
    if entry and facts.get('safety') in _LEVELS and effective_safety(facts['safety'], entry) != facts['safety']:
        body += '<p>Research rates this more cautiously than the catalog.</p>'
    return body
