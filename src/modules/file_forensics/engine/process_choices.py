"""Resolve holder identities and safety before offering a destructive action. Qt-free."""
import logging
import math
import ntpath
from dataclasses import dataclass
from datetime import datetime

import psutil
from core.procengine.actions import UNKILLABLE
from core.procengine.details import resolve
from core.procengine.mitigations import read_mitigations
from core.procengine.signatures import _SIGNATURE_CACHE, VALID, NOT_SIGNED, INVALID

logger = logging.getLogger(__name__)
CRITICAL = {'csrss', 'wininit', 'services', 'lsass', 'smss', 'system', 'registry', 'msmpeng'}


@dataclass(frozen=True)
class ProcessChoice:
    pid: int
    name: str
    created: float | None
    path: str
    user: str
    started: str
    signature: str
    reason: str = ''


def blocked_reason(pid, name, details, report):
    names = (name, ntpath.basename(details.path or ''))
    if pid in UNKILLABLE or any(n.casefold().removesuffix('.exe') in CRITICAL for n in names):
        return 'Critical Windows or security process; cannot be ended here.'
    if details.integrity == 'Protected' or report.protection not in (None, 'None'):
        return 'Protected process: ' + (report.protection or 'protected integrity')
    if report.protection is None:
        return 'Could not check process protection: ' + (report.error or 'Windows refused the read')
    return ''


def resolve_choices(processes, cancelled=lambda: False):
    choices, seen = [], set()
    for proc in processes:
        if cancelled():
            break
        if proc.pid in seen:
            continue
        seen.add(proc.pid)
        created, identity_error = None, ''
        try:
            created = psutil.Process(proc.pid).create_time()
            if not math.isfinite(created) or created <= 0:
                identity_error = 'Process could not be identified; nothing can be ended.'
        except psutil.Error as error:
            logger.warning('Could not identify holder PID %s: %s', proc.pid, error)
            identity_error = 'Process exited or could not be identified; nothing can be ended.'
        details = resolve(proc.pid)
        report = read_mitigations(proc.pid)
        try:
            if created is not None and abs(psutil.Process(proc.pid).create_time() - created) >= 0.01:
                identity_error = 'Process changed while reading details; search again.'
        except psutil.Error as error:
            logger.warning('Holder PID %s changed during inspection: %s', proc.pid, error)
            identity_error = 'Process exited or could not be identified; search again.'
        sig = _SIGNATURE_CACHE.get(details.path)
        signature = ({VALID: 'Signed', NOT_SIGNED: 'Unsigned', INVALID: 'INVALID'}.get(sig.status, 'Could not check')
                     if sig else 'Not checked yet')
        reason = blocked_reason(proc.pid, proc.process, details, report) or identity_error
        choices.append(ProcessChoice(proc.pid, proc.process, created,
            details.path or 'Could not check: ' + (details.path_error or 'path unavailable'),
            details.user or 'Could not check: ' + (details.user_error or 'user unavailable'),
            datetime.fromtimestamp(created).isoformat(sep=' ', timespec='seconds') if created and not identity_error else 'Could not check',
            signature, reason))
    return choices
