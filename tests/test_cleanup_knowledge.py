import ast
import json
from dataclasses import FrozenInstanceError
from pathlib import Path
from types import SimpleNamespace

import pytest

from modules.cleanup.cleanup_scanner import knowledge as kb


def test_schema_and_coverage(capsys):
    from modules.cleanup.cleanup_scanner.catalog import load_catalog
    catalog = load_catalog()
    handwritten = set()
    root = Path(kb.__file__).parent
    for path in root.glob('scanners_*.py'):
        handwritten.update(n.name.removeprefix('scan_') for n in ast.walk(ast.parse(path.read_text(encoding='utf-8')))
                           if isinstance(n, ast.FunctionDef) and n.name.startswith('scan_'))
    ids = set()
    for path in (root / 'definitions/knowledge').glob('*.json'):
        payload = json.loads(path.read_text(encoding='utf-8'))
        for id_, raw in payload.items():
            assert id_ not in ids
            ids.add(id_)
            assert id_ in catalog or id_ in handwritten or id_.startswith('browser:')
            assert set(raw) == set(kb.Knowledge.__dataclass_fields__)
            assert kb.entry_for(id_) is not None
            for key, value in raw.items():
                if isinstance(value, str):
                    assert value == value.strip()
                    assert len(value.split()) <= 65
            assert all(url.startswith('https://') for url in raw['sources'])
    missing = sorted(set(catalog) - ids)
    print(f'Catalog knowledge coverage: {len(set(catalog) & ids)}/{len(catalog)}; missing: {missing}')
    assert ids


@pytest.mark.parametrize('catalog,verdict,expected', [
    ('safe', 'caution', 'caution'), ('danger', 'safe', 'danger'),
    ('caution', 'danger', 'danger'), ('safe', None, 'safe'),
    ('unknown', 'safe', 'danger'),
])
def test_safety_never_relaxes(catalog, verdict, expected):
    entry = kb.Knowledge(verdict=verdict) if verdict else None
    assert kb.effective_safety(catalog, entry) == expected


def test_html_and_identity():
    entry = kb.Knowledge(what='<script>alert(1)</script>', sources=('https://example.com/?a="b"',))
    for rendered in (kb.tooltip_html(entry, '<img>', 'safe', '<size>'),
                     kb.detail_html(entry, {'label': '<img>', 'path': '<path>', 'keep_root': True,
                                            'is_dir': True, 'keep_newer_than_hours': 24})):
        assert '<script>' not in rendered
        assert '&lt;script&gt;' in rendered
        assert '<img>' not in rendered
    assert 'https://' not in kb.tooltip_html(entry, 'name', 'safe', '0 B')
    assert 'Not yet researched' in kb.tooltip_html(None, 'name', 'safe', '0 B')
    assert '24' in kb.detail_html(entry, {'keep_newer_than_hours': 24})
    assert kb.id_for_scanner(SimpleNamespace(spec=SimpleNamespace(id='catalog'))) == 'catalog'
    def scan_handwritten():
        return None
    assert kb.id_for_scanner(scan_handwritten) == 'handwritten'
    with pytest.raises(FrozenInstanceError):
        entry.what = 'changed'


def test_bad_file_is_logged_and_skipped(tmp_path, monkeypatch, caplog):
    (tmp_path / 'bad.json').write_text('{broken', encoding='utf-8')
    (tmp_path / 'valid.json').write_text(json.dumps({'ok': {'what': 'Test', 'verdict': 'safe'}}), encoding='utf-8')
    monkeypatch.setattr(kb, '_DIRECTORY', tmp_path)
    monkeypatch.setattr(kb, '_entries', None)
    assert kb.entry_for('bad') is None
    assert kb.entry_for('ok').what == 'Test'
    assert caplog.records


def test_invalid_fields_rejected(tmp_path, monkeypatch):
    rows = {'bad_source': {'sources': ['http://example.com']}, 'bad_regen': {'regenerates': 'yes'},
            'bad_verdict': {'verdict': 'good'}, 'too_long': {'what': 'word ' * 80}}
    (tmp_path / 'rows.json').write_text(json.dumps(rows), encoding='utf-8')
    monkeypatch.setattr(kb, '_DIRECTORY', tmp_path)
    monkeypatch.setattr(kb, '_entries', None)
    assert all(kb.entry_for(id_) is None for id_ in rows)


def test_detail_uses_research_safety_and_missing_description():
    page = kb.detail_html(kb.Knowledge(verdict='danger'), {'safety': 'safe'})
    assert '<p>danger ·' in page
    assert 'Research rates this more cautiously' in page
    missing = kb.detail_html(None, {'description': '<catalog description>'})
    assert '&lt;catalog description&gt;' in missing
