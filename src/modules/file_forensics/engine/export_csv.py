"""Context-preserving CSV export, without Qt or spreadsheet formulas."""
import csv
from collections import Counter
from core.procengine.signatures import VALID, NOT_SIGNED, INVALID


def safe_cell(value):
    text = '' if value is None else str(value)
    # Whitespace/control prefixes are also interpreted by some spreadsheet readers.
    return "'" + text if text.lstrip().startswith(('=', '+', '-', '@')) else text


def signature_cell(facts, notes):
    if facts is not None:
        text = {VALID: 'Signed', NOT_SIGNED: 'Unsigned', INVALID: 'INVALID'}.get(facts.status, 'Could not check')
        return text + (': ' + facts.reason if facts.reason else '')
    if not notes:
        return 'Not checked yet'
    if any('not a signable file type' in note for note in notes):
        return 'Not a signable file type'
    return 'Could not check: ' + '; '.join(notes)


def inspection_cell(row):
    inspection = row.lock_inspection
    if inspection.reason.casefold() == 'not checked yet':
        return 'Not checked yet'
    state = {'complete': 'Complete', 'partial': 'Partial', 'could_not_check': 'Could not check'}.get(inspection.state, inspection.state)
    return state + (': ' + inspection.reason if inspection.reason else '')


def export_row(row, cache):
    meta = row.metadata
    notes = cache.get(meta.path, {})
    locked = ', '.join(p.process for p in row.locking_processes)
    if not locked:
        locked = ('None observed' if row.lock_inspection.state in ('complete', 'partial')
                  else inspection_cell(row))
    creator = (row.creator_candidates[0].name if row.creator_candidates else
               'No candidate' if row.creator_ready else 'Not checked yet')
    creator_sig = (signature_cell(row.top_creator_signature, notes.get('signatures')) if row.creator_candidates
                   else 'No candidate' if row.creator_ready else 'Not checked yet')
    return [meta.path, meta.size, meta.created, meta.owner or 'Could not check', locked, creator,
            signature_cell(row.own_signature, notes.get('signatures')), meta.modified,
            creator_sig, inspection_cell(row)]


def export_csv(path, rows, context, detail_cache=None):
    context = dict(context)
    for key in ('root', 'name filter', 'filters', 'recursion', 'start time', 'end time', 'outcome',
                'matched', 'skipped links', 'skipped unreadable'):
        context.setdefault(key, 'Not checked yet')
    counts = Counter(inspection_cell(row).split(':', 1)[0] for row in rows)
    context['handle-inspection coverage'] = '; '.join(f'{state}: {count}' for state, count in sorted(counts.items())) or 'No result rows'
    with open(path, 'w', newline='', encoding='utf-8') as stream:
        for key, value in context.items():
            for line in (f'{key}: {value}').splitlines():
                stream.write('# ' + safe_cell(line) + '\n')
        writer = csv.writer(stream)
        writer.writerow(['Path', 'Size', 'Created', 'Owner', 'Locked By', 'Likely Creator',
                         'File Signature', 'Modified', 'Creator signed', 'Lock inspection'])
        for row in rows:
            writer.writerow([safe_cell(cell) for cell in export_row(row, detail_cache or {})])
