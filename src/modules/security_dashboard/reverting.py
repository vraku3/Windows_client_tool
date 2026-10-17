"""Revert is a write, and gets the same verification as any other.

`_ToggleCard.configure` defaulted `revert_fn` to `toggle_fn`, so Revert called
the setter with the opposite argument -- a guess about the previous value, and
simply wrong for anything multi-valued. BackupService already records the real
`before_value` per step and deletes a value that did not exist before, so the
revert itself is its job. What is left is knowing whether it took, and that is
this module.

The two rules from `applier.py` carry over unchanged, because a revert is a
write like any other:

* **BackupService reporting success is not evidence the machine moved.** The
  control is read afterwards. With a known prior value the reading is compared
  to it; without one, the weaker check is that the reading CHANGED -- a revert
  that reports success and leaves the setting exactly as it was has not been
  verified, whatever the writer says.
* **The snapshot caches are dropped before the check, once per batch.** 135 of
  the 149 readers answer out of `snapshots.py`, whose cache has no expiry.
"""
import logging
from typing import Any, Callable, Dict, Optional

from . import snapshots
from .applier import BatchResult, ControlResult
from .catalog.model import ControlState, SecurityControl

logger = logging.getLogger(__name__)


class _Unknown:
    """Sentinel: the caller does not know what the value was before."""

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return "<unknown>"


_UNKNOWN = _Unknown()


def _reason_of(outcome) -> str:
    return ("; ".join(getattr(outcome, "errors", []) or [])
            or "the revert reported failure")


def _verify(control: Optional[SecurityControl], control_id: str,
            before: Any, expected: Any) -> ControlResult:
    """Read the control back and say what the reading proves.

    Called only after the caches have been dropped -- never per control in a
    batch, or the snapshot warmed by the first check is stale for the rest.
    """
    if control is None:
        # A restore point written by a build that had a control this one does
        # not. The steps were reverted; nothing here can confirm it.
        return ControlResult(
            control_id, ControlState.APPLIED_UNVERIFIED, expected_or_none(expected),
            None, reason="this build has no such control, so the revert could "
                         "not be checked")

    observed = control.read()
    if control.requires_reboot:
        return ControlResult(control_id, ControlState.APPLIED_PENDING_REBOOT,
                             expected_or_none(expected), observed,
                             reason="takes effect after a restart")

    if not isinstance(expected, _Unknown):
        if observed == expected:
            return ControlResult(control_id, ControlState.APPLIED_VERIFIED,
                                 expected, observed)
        return ControlResult(
            control_id, ControlState.APPLIED_UNVERIFIED, expected, observed,
            reason=f"the revert reported success but the setting reads "
                   f"{observed!r}, not the {expected!r} it had before the "
                   "change")

    if observed is None:
        # A refused read differs from every value, so without this check
        # "could not look" was reported as "verified reverted".
        return ControlResult(
            control_id, ControlState.APPLIED_UNVERIFIED, None, None,
            reason="the revert reported success, but the setting could not be "
                   "read back to confirm it")
    if observed != before:
        return ControlResult(control_id, ControlState.APPLIED_VERIFIED,
                             None, observed)
    return ControlResult(
        control_id, ControlState.APPLIED_UNVERIFIED, None, observed,
        reason=f"the revert reported success but the setting still reads "
               f"{observed!r}. Either it was never actually changed, or "
               "something is overriding it.")


def expected_or_none(expected: Any) -> Any:
    return None if isinstance(expected, _Unknown) else expected


def revert_control(control_id: str, backup,
                   catalog: Dict[str, SecurityControl], *,
                   expected: Any = _UNKNOWN,
                   invalidate_reads: Optional[Callable[[], None]] = None
                   ) -> ControlResult:
    """Undo one control's most recent applied steps, then check the machine.

    `expected` is the value the control had before the change, when the caller
    knows it -- staging recorded it as `from_value` and the elevated batch file
    carries it across the UAC prompt. Without it the check is only that the
    reading moved, which cannot tell a multi-valued control landing on the
    right value from landing on some other one.
    """
    # Resolved at CALL time, not bound as a default. A default argument is
    # evaluated once, when the module is imported, so
    # `invalidate_reads=snapshots.invalidate` captures THAT function object
    # and any later swap of snapshots.invalidate -- a test's stub, or a
    # future replacement -- silently does nothing.
    invalidate = invalidate_reads or snapshots.invalidate
    control = catalog.get(control_id)
    before = control.read() if control is not None else None

    outcome = backup.revert_tweak(control_id)
    invalidate()

    if not getattr(outcome, "success", False):
        observed = control.read() if control is not None else None
        return ControlResult(control_id, ControlState.REFUSED,
                             expected_or_none(expected), observed,
                             reason=_reason_of(outcome))

    return _verify(control, control_id, before, expected)


def revert_batch(rp_id: str, backup, catalog: Dict[str, SecurityControl], *,
                 expected: Optional[Dict[str, Any]] = None,
                 invalidate_reads: Optional[Callable[[], None]] = None
                 ) -> BatchResult:
    """Undo a whole restore point, then check every control it named.

    `restore_point()` reverts every still-applied step in the session in one
    call, so this must NOT then revert each control again -- that would be a
    second revert of work that is no longer applied, unwinding an earlier
    session's steps for the same tweak at worst.

    `RestoreResult.reverted_ids` is what makes the check possible at all: a
    batch revert that cannot name what it reverted cannot verify anything.
    """
    # Resolved at CALL time, not bound as a default. A default argument is
    # evaluated once, when the module is imported, so
    # `invalidate_reads=snapshots.invalidate` captures THAT function object
    # and any later swap of snapshots.invalidate -- a test's stub, or a
    # future replacement -- silently does nothing.
    invalidate = invalidate_reads or snapshots.invalidate
    known = expected or {}
    before: Dict[str, Any] = {}
    ids = _ids_of(backup, rp_id)
    for control_id in ids:
        control = catalog.get(control_id)
        before[control_id] = control.read() if control is not None else None

    outcome = backup.restore_point(rp_id)
    invalidate()

    result = BatchResult(rp_id=rp_id)
    reverted = list(getattr(outcome, "reverted_ids", None) or ids)
    failed = set(getattr(outcome, "failed_ids", None) or [])
    if not getattr(outcome, "success", False) and not failed:
        # An older RestoreResult, or a failure it could not attribute: the
        # whole session is refused rather than silently reported as fine.
        failed = set(reverted)

    for control_id in reverted:
        if control_id in failed:
            control = catalog.get(control_id)
            result.results.append(ControlResult(
                control_id, ControlState.REFUSED,
                known.get(control_id), control.read() if control else None,
                reason=_reason_of(outcome)))
            continue
        result.results.append(_verify(
            catalog.get(control_id), control_id, before.get(control_id),
            known[control_id] if control_id in known else _UNKNOWN))
    return result


def _ids_of(backup, rp_id: str):
    """What the restore point covers, asked BEFORE it is reverted.

    Only used to read prior values; the authoritative list is what
    `restore_point()` reports it actually touched.
    """
    lookup = getattr(backup, "control_ids_in", None)
    if lookup is None:
        return []
    try:
        return list(lookup(rp_id))
    except Exception:
        logger.warning("could not list the controls in restore point %r",
                       rp_id, exc_info=True)
        return []
