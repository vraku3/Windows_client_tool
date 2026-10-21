"""Verify Cleanup redesign modules AND research JSON inside a built executable.

Usage: .venv/Scripts/python tools/check_frozen_cleanup.py [path/to/app.exe]
Reads the CArchive/PYZ without launching or rebuilding the executable.
Exit 1 means missing, corrupt, or unreadable content. No source fallback.
"""
from pathlib import Path
import json
import sys

ROOT = Path(__file__).resolve().parents[1]


def expected_content():
    base = ROOT / 'src'
    cleanup = base / 'modules' / 'cleanup'
    sources = [cleanup / path for path in (
        'cleanup_scanner/knowledge.py', 'cleanup_scanner/scan_snapshot.py',
        'cleanup_scanner/recommend.py', 'cleanup_reports.py',
        'components/item_rows.py', 'components/safety_delegate.py',
        'components/detail_pane.py', 'components/charts.py', 'components/kpi_tiles.py',
        'components/recommendations_panel.py', 'tabs/_duplicates_tab.py',
        'tabs/_duplicates_model.py')]
    # Include future duplicates submodules, without demanding obsolete,
    # unreachable legacy Cleanup tabs in a fresh build.
    sources += list((cleanup / 'duplicates').rglob('*.py'))
    sources += [base / 'modules' / 'game_mode' / 'engine' / (name + '.py')
                for name in ('optimise', 'space', 'app_usage')]
    sources.append(base / 'modules' / 'game_mode' / 'optimise_tab.py')
    modules = set()
    for path in sources:
        module = '.'.join(path.relative_to(base).with_suffix('').parts)
        modules.add(module.removesuffix('.__init__'))
    data = {p.relative_to(base).as_posix(): p for p in
            (cleanup / 'cleanup_scanner' / 'definitions' / 'knowledge').glob('*.json')}
    if not data:
        raise ValueError('No source knowledge JSON files found; cannot verify coverage')
    return modules, data


def check(exe):
    from PyInstaller.archive.readers import CArchiveReader
    wanted, data = expected_content()
    archive = CArchiveReader(str(exe))
    pyz_names = [name for name in archive.toc if name.endswith('.pyz')]
    if not pyz_names:
        raise ValueError('Executable has no embedded PYZ archive')
    have = set()
    for name in pyz_names:
        have.update(archive.open_embedded_archive(name).toc)
    problems = [f'MISSING module {name}' for name in sorted(wanted - have)]
    names = {name.replace('\\', '/'): name for name in archive.toc}
    for name, source in sorted(data.items()):
        stored = names.get(name)
        if stored is None:
            problems.append(f'MISSING knowledge {name}')
            continue
        # Presence alone is insufficient: stale/incomplete research silently
        # degrades tooltips. Compare decoded JSON, independent of whitespace.
        payload = archive.extract(stored)
        if not isinstance(payload, bytes):
            raise ValueError(f'Unexpected archive payload for {name}')
        if json.loads(payload.decode('utf-8')) != json.loads(source.read_text(encoding='utf-8')):
            problems.append(f'STALE knowledge {name}')
    print(f'{len(wanted)} required modules; {len(data)} research JSON files checked in {exe}')
    for problem in problems:
        print(problem)
    return 1 if problems else 0


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    exe = Path(argv[0]) if argv else ROOT / 'dist' / 'WinClientTool-Portable.exe'
    try:
        return check(exe)
    except Exception as exc:
        print(f'Cannot verify frozen Cleanup: {exc}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
