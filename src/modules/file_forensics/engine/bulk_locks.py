"""Attribute one handle sweep to many exact files, including their NTFS streams."""
from core.procengine.findref import find
from .analysis import LockInspection
from .device_paths import to_device_path
from .locking_processes import LockingProcess, is_same_file


def attribute_handles(targets, handles):
    index = {}
    for path, device in targets.items():
        if device:
            index.setdefault(device.lower(), []).append(path)
    result = {path: [] for path in targets}
    seen = set()
    for handle in handles:
        if handle.type_name != 'File':
            continue
        name = (handle.detail or '').lower()
        base = name.split(':', 1)[0]
        for path in index.get(base, ()):
            if (path, handle.pid) not in seen and is_same_file(name, targets[path]):
                result[path].append(LockingProcess(handle.pid, handle.process, 'File'))
                seen.add((path, handle.pid))
    return result


def bulk_locks(paths, processes, cancelled=lambda: False, progress=None):
    targets = {}
    drives = {}
    for index, path in enumerate(paths):
        if cancelled():
            return {}, {}, {}
        if len(path) >= 3 and path[1] == ':':
            drive = path[:2].upper()
            if drive not in drives:
                drives[drive] = to_device_path(drive + '\\')
            prefix = drives[drive]
            targets[path] = prefix[:-1] + path[2:] if prefix else None
        else:
            targets[path] = to_device_path(path)
        if progress and index % 1024 == 0:
            progress(f'Preparing one handle check: {index:,} / {len(paths):,} files')
    report = find('\\', handles=True, modules=False, should_stop=cancelled,
                  process_snapshot=processes, progress=(
                      lambda done, total: progress(f'Checking open handles once: {done:,} / {total:,} processes')
                  ) if progress else None)
    if cancelled():
        return {}, {}, {}
    result = attribute_handles(targets, report.matches)
    summaries = {path: (report.summary() + ('; ' + report.note if report.note else '')
                       if device else 'Path translation refused; locks could not be checked')
                 for path, device in targets.items()}
    coverage = report_inspection(report)
    inspection = {path: coverage if device else LockInspection(
        'could_not_check', 'Path translation refused; locks could not be checked')
        for path, device in targets.items()}
    return result, summaries, inspection


def report_inspection(report):
    reasons = []
    for field, label in (('refused_handles', 'processes refused handle inspection'),
                         ('blocked_handles', 'processes had blocked handle queries'),
                         ('unsearched', 'processes were never reached')):
        count = getattr(report, field, 0)
        if count:
            reasons.append(f'{count} {label}')
    if report.note:
        reasons.append(report.note)
    elif getattr(report, 'stopped_early', False):
        reasons.append('Handle sweep stopped before completion')
    return LockInspection('partial', '; '.join(reasons)) if reasons else LockInspection()
