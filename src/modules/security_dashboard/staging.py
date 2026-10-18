"""Changes stage, then apply as one batch. Nothing here touches the machine.

Two rules this module enforces, both of which exist because of something that
already went wrong elsewhere in this project:

* **Staging never reads the machine when the caller already knows the value.**
  `add()` takes an optional `from_value`; a card that has just rendered a
  reading passes it. Reading per click is the per-field-PowerShell trap the
  snapshot layer exists to remove, reintroduced in the UI -- and it is not
  theoretical, since `bitlocker_encryption_detail.read()` costs 6.11s on the
  machine this was written on.

* **A control cannot be staged to no value.** `SecurityControl.steps_for(None)`
  raises rather than quietly returning `off_steps`, because the old behaviour
  would have turned Windows Search off on a control the catalog explicitly has
  no opinion about. Staging `None` as a TARGET would reach that at apply time,
  in a worker thread, with no user in front of it. It is refused here instead,
  where the message can still name the control.
"""
import logging
import re
from dataclasses import dataclass
from typing import Any, Dict, Optional, Tuple

from .catalog.audit_logging import migrate_legacy_value
from .catalog.model import Risk, SecurityControl

logger = logging.getLogger(__name__)

_LEGACY_SHRINK_GUARDED = ("security_log_size", "system_log_size")

#: Shown in the review dialog for a change whose prior setting the control's
#: revert probe could not capture.
NO_PROBE_NOTE = ("cannot be undone automatically: the setting before this "
                 "change could not be read, so nothing records what to put "
                 "back (an elevated apply reads it again first)")


class _Unread:
    """Sentinel: the caller did not say what the current value is.

    Distinct from None, which is a real answer meaning "the machine could not
    be read" -- a control may be staged precisely because it could not be.
    """

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return "<unread>"


_UNREAD = _Unread()


def _revert_command(template: str, values: Optional[Dict[str, str]],
                    from_value: Any) -> Optional[str]:
    """Fill `${old}` in a revert template, or return None if it cannot be."""
    if from_value is None:
        return None
    if values is not None:
        old = values.get(str(from_value))
    elif isinstance(from_value, bool):
        # A bool has no obvious cmdlet token -- $true/$false, Enabled/
        # Disabled and 1/0 are all plausible -- so it must be mapped.
        old = None
    elif isinstance(from_value, int):
        old = str(from_value)
    else:
        # A string read off the machine, going into a shell command. Not
        # without an explicit map.
        old = None
    return template.replace("${old}", old) if old is not None else None


def _probed_command(template: str, pattern: Optional[str],
                    token: Optional[str]) -> Optional[str]:
    """Fill `${old}` from a revert probe's token, only if the step's own
    `revert_pattern` matches it in full. No pattern, no command: a token off
    the machine never reaches a shell unchecked."""
    if not isinstance(token, str) or not pattern:
        return None
    if re.fullmatch(pattern, token) is None:
        logger.warning("revert token %r does not match %r; no revert command",
                       token, pattern)
        return None
    return template.replace("${old}", token)


def _probe(control: SecurityControl) -> Optional[str]:
    try:
        token = control.revert_probe()
    except Exception:
        logger.warning("control %r: revert probe raised; it cannot be undone "
                       "automatically", control.id, exc_info=True)
        return None
    return token if isinstance(token, str) else None


@dataclass(frozen=True)
class PendingChange:
    control_id: str
    control: SecurityControl
    from_value: Optional[Any]
    to_value: Any
    #: The control's `revert_probe()` answer, captured when staged.
    revert_token: Optional[str] = None

    @property
    def revert_note(self) -> Optional[str]:
        """Why this change cannot be undone, when its probe captured nothing."""
        if self.control.revert_probe is not None and not self.has_probed_revert:
            return NO_PROBE_NOTE
        return None

    @property
    def has_probed_revert(self) -> bool:
        return any(step.get("revert_command")
                   for step in self.resolved_steps()
                   if step.get("type") == "script")

    def resolved_steps(self) -> Tuple[Dict, ...]:
        """The steps to run, with `script` reverts computed from `from_value`.

        A static revert command in the catalog cannot know what it is
        reverting TO, and BackupService cannot work it out either: it records
        a before-value for a registry write or a service change, and nothing
        at all for a command. So a script step carries a `revert_template`
        with `${old}` in it, and this fills that in from the value the machine
        had before the change was staged.

        Three ways `${old}` is resolved, in order:

        * `revert_values` maps the old value (as a string) to the token the
          cmdlet accepts. Required for anything that is not a plain number.
        * A bare int reverts to itself, so a 0-50 parameter does not need a
          table of 51 entries.
        * Anything else yields **no revert command**. That is deliberate in
          three cases that all exist in the real catalog: an unreadable
          `from_value`; a string off the machine, which must never be pasted
          into a shell command unmapped; and a value the map does not cover,
          which is exactly the threat-action controls -- Get-MpPreference
          reports 0 for an unconfigured severity and 0 is not a member of the
          ThreatAction enum, so no command reverts to it.

        A control with a `revert_probe` takes none of these: its token,
        captured at staging, fills `${old}` only if the step's
        `revert_pattern` matches it in full; otherwise there is no revert
        command and `revert_note` says so.

        Steps are copied before anything is popped: the catalog's dicts are
        shared across every ChangeSet in the process.
        """
        resolved = []
        for step in self.control.steps_for(self.to_value):
            step = dict(step)
            template = step.pop("revert_template", None)
            values = step.pop("revert_values", None)
            pattern = step.pop("revert_pattern", None)
            if template and self.control.revert_probe is not None:
                step["revert_command"] = _probed_command(
                    template, pattern, self.revert_token)
            elif template:
                step["revert_command"] = _revert_command(
                    template, values, self.from_value)
            resolved.append(step)
        return tuple(resolved)

    def one_way_steps(self) -> Tuple[Dict, ...]:
        """The steps in this change that cannot be undone by this tool.

        BackupService reverts a `registry` step from its recorded
        before-value and a `service` step from its recorded start type. It
        records **nothing** for a `command`, and for a `script` only the
        revert command computed above -- which is absent when the machine's
        previous value has no command that restores it.

        So this is not a warning about risk, it is a statement of fact about
        the undo path, and the review dialog has to be able to show it.
        """
        one_way = []
        for step in self.resolved_steps():
            kind = step.get("type")
            if kind in ("registry", "registry_delete", "service"):
                continue
            if kind == "script" and step.get("revert_command"):
                continue
            one_way.append(step)
        return tuple(one_way)


class ChangeSet:
    """Staged changes, keyed by control id.

    Staging a control back to the value it already has removes it rather than
    queueing a no-op, so the pending count is always the number of things that
    would actually happen.
    """

    def __init__(self) -> None:
        self._changes: Dict[str, PendingChange] = {}

    def add(self, control: SecurityControl, to_value: Any,
            from_value: Any = _UNREAD) -> None:
        """Stage `control` to `to_value`.

        `from_value` is the current value if the caller already has it --
        pass it and the machine is not read again. Omit it and the control is
        read once, here.
        """
        legacy = to_value
        to_value = migrate_legacy_value(control.id, to_value)
        if (to_value is False and legacy is not False
                and control.id in _LEGACY_SHRINK_GUARDED):
            # A saved size below the target does not ask for a smaller log
            # than the one there now; never stage the shrinking step.
            self._changes.pop(control.id, None)
            return
        if to_value is None:
            raise ValueError(
                f"control {control.id!r} cannot be staged to no value: "
                "a target of None has no steps to apply")
        if not control.writable:
            raise ValueError(
                f"control {control.id!r} is read-only: {control.read_only_reason}")
        current = control.read() if isinstance(from_value, _Unread) else from_value
        if current == to_value:
            self._changes.pop(control.id, None)
            return
        control.validate_target(to_value)
        token = _probe(control) if control.revert_probe is not None else None
        self._changes[control.id] = PendingChange(
            control_id=control.id, control=control,
            from_value=current, to_value=to_value, revert_token=token)

    def remove(self, control_id: str) -> None:
        self._changes.pop(control_id, None)

    def clear(self) -> None:
        self._changes.clear()

    @property
    def changes(self) -> Tuple[PendingChange, ...]:
        """In the order they were staged -- dicts keep insertion order, and
        the review dialog shows them in the order the user clicked."""
        return tuple(self._changes.values())

    def __len__(self) -> int:
        return len(self._changes)

    def __contains__(self, control_id: object) -> bool:
        return control_id in self._changes

    @property
    def unread_before(self) -> Tuple[PendingChange, ...]:
        """Staged changes whose current value could not be read.

        Applying these is defensible -- the target says what they should be,
        and the apply path verifies afterwards -- but they are a different
        thing from "this differs from what you want", and the review dialog
        has to be able to say which is which. Against the real catalog,
        unelevated, a full baseline stages 60 changes of which 14 are these.
        """
        return tuple(c for c in self._changes.values() if c.from_value is None)

    @property
    def one_way_changes(self) -> Tuple[PendingChange, ...]:
        """Staged changes carrying at least one step that cannot be undone."""
        return tuple(c for c in self._changes.values() if c.one_way_steps())

    @property
    def needs_admin(self) -> bool:
        return any(c.control.requires_admin for c in self._changes.values())

    @property
    def needs_reboot(self) -> bool:
        return any(c.control.requires_reboot for c in self._changes.values())

    @property
    def highest_risk(self) -> Risk:
        order = {Risk.LOW: 0, Risk.MEDIUM: 1, Risk.HIGH: 2}
        return max((c.control.risk for c in self._changes.values()),
                   key=lambda r: order[r], default=Risk.LOW)


def diff_against(catalog: Dict[str, SecurityControl],
                 target: Dict[str, Any],
                 readings: Optional[Dict[str, Any]] = None) -> ChangeSet:
    """Stage everything in `target` that differs from the live machine.

    `readings` is what the caller already knows -- the pane holds a value for
    every card it has drawn. A control named there is not read again; one that
    is not, is. Without it this reads all 149 controls, which is 12.7s on the
    machine this was written on.

    Ids the catalog does not know are ignored, not an error: a profile from a
    newer build of the app names controls this one has not got. Read-only
    controls are skipped for the same reason -- a baseline may record what a
    firmware setting was without implying anyone can write it -- and so is a
    target of None, which is what a baseline records for a control the machine
    could not answer for.
    """
    changes = ChangeSet()
    known = readings if readings is not None else {}
    for control_id, desired in target.items():
        control = catalog.get(control_id)
        if control is None or not control.writable or desired is None:
            continue
        if control_id in known:
            changes.add(control, desired, from_value=known[control_id])
        else:
            changes.add(control, desired)
    return changes
