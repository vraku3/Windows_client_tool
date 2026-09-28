"""Cross-reference this app's own tweaks against the keys local Group Policy manages.

Why this exists: 286 of the ~2500 registry steps in `modules/tweaks/definitions`
write straight into Windows' *managed* policy branches (a key containing
`\\Policies\\`). Those branches do not belong to whoever wrote them last -- they
belong to the Registry client-side extension, which deletes and rewrites them
when it processes a GPO. A tweak this app applied by poking a policy value can
therefore be undone with no error, no prompt and no trace in the app's own
revert log.

Be careful about how loudly that is stated. With default settings the Registry
CSE *skips* processing when no GPO version has changed, so a hand-written policy
value can sit there untouched for weeks. The honest claim is "can be reverted
without warning" -- on the next `gpupdate /force`, GPO version bump or policy
edit -- and NOT "is reverted every 90 minutes". Every string this module
produces is worded to that standard.

The other half of the finding matters just as much: a tweak that writes the
same data the policy already sets is *duplicating* policy, which is untidy but
harmless, while one that writes different data is *fighting* policy, which it
will lose. Those two are reported separately and must never be collapsed.

Both inputs are injectable so the analysis is testable without touching this
machine: pass parsed tweak definitions and `PolFile`s, or let the real loaders
run. Nothing is loaded at import time -- ~800 tweak definitions is not a cost to
pay for `import`.
"""

from __future__ import annotations

import bisect
import glob
import json
import logging
import os
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from modules.gpresult.pol_parser import PolFile, PolicyValue, local_policy_files

logger = logging.getLogger(__name__)

#: How tight the overlap between a tweak step and a policy record is.
MATCH_DIRECT = "direct"       # same key AND same value name -- policy overwrites it
MATCH_SAME_KEY = "same_key"   # same key, a different value name under it
MATCH_BRANCH = "branch"       # the tweak's key sits under (or above) a policy key

#: Whether the tweak wants what the policy already says.
AGREE_DUPLICATE = "duplicates"      # same data -- redundant, not dangerous
AGREE_CONFLICT = "conflicts"        # different data -- the policy wins
AGREE_UNCOMPARABLE = "uncomparable"  # different values, or data we cannot line up

#: Sort order for a pane: tightest match first, real conflicts above duplicates.
_MATCH_RANK = {MATCH_DIRECT: 0, MATCH_SAME_KEY: 1, MATCH_BRANCH: 2}
_AGREE_RANK = {AGREE_CONFLICT: 0, AGREE_UNCOMPARABLE: 1, AGREE_DUPLICATE: 2}

MATCH_LABELS = {
    MATCH_DIRECT: "Same key and value",
    MATCH_SAME_KEY: "Same key",
    MATCH_BRANCH: "Same branch",
}
AGREE_LABELS = {
    AGREE_CONFLICT: "Conflicts with policy",
    AGREE_DUPLICATE: "Duplicates policy",
    AGREE_UNCOMPARABLE: "Not comparable",
}

#: Registry step types that name a key we can compare. Service/appx/command
#: steps touch no registry path, so they cannot collide with a policy record.
REGISTRY_STEP_TYPES = ("registry", "registry_delete")

_HIVE_ALIASES = {
    "HKLM": "HKLM",
    "HKEY_LOCAL_MACHINE": "HKLM",
    "HKCU": "HKCU",
    "HKEY_CURRENT_USER": "HKCU",
    "HKCR": "HKCR",
    "HKEY_CLASSES_ROOT": "HKCR",
    "HKU": "HKU",
    "HKEY_USERS": "HKU",
    "HKCC": "HKCC",
    "HKEY_CURRENT_CONFIG": "HKCC",
}

#: Definition files under `definitions/` that are not tweak lists. They are
#: skipped by shape rather than by name as well, but naming them keeps the
#: "skipped a file" notes free of noise a user cannot act on.
_NON_TWEAK_FILES = frozenset({"app_catalog.json"})

#: `**del.Foo` and `**soft.Foo` name a real value; the rest of the CSE
#: directives are statements about the key as a whole.
_VALUE_DIRECTIVE_PREFIXES = {"delete_value": "**del.", "set_if_absent": "**soft."}


# --------------------------------------------------------------------------
# normalisation
# --------------------------------------------------------------------------

def normalise_key(key: str) -> str:
    """A registry path in one comparable form: upper case, canonical hive.

    The registry is case-insensitive, and the tweak definitions are not
    consistent about it -- `HKLM\\SOFTWARE\\Policies` and
    `HKLM\\Software\\Policies` both appear, and `Registry.pol` writes a third
    casing again. Comparing raw strings is how a matcher silently finds nothing.
    """
    text = (key or "").strip().strip("\\")
    if not text:
        return ""
    head, _, rest = text.partition("\\")
    hive = _HIVE_ALIASES.get(head.upper())
    if hive:
        text = hive + ("\\" + rest if rest else "")
    return text.upper()


def policy_key_path(pol: PolFile, value: PolicyValue) -> str:
    """The hive-qualified key a `Registry.pol` record refers to.

    `Registry.pol` stores keys hive-*relative* (`Software\\Policies\\...`); the
    hive comes from which file the record was in -- Machine is HKLM, User is
    HKCU. Losing that mapping is the difference between matching everything and
    matching nothing.
    """
    hive = (pol.hive or "").strip("\\") or ("HKCU" if pol.scope == "User" else "HKLM")
    return "%s\\%s" % (hive, (value.key or "").strip("\\"))


def effective_value_name(value: PolicyValue) -> str:
    """The value a policy record actually talks about.

    `**del.NoDriveTypeAutoRun` is not a value named `**del.NoDriveTypeAutoRun`;
    it is policy saying "delete `NoDriveTypeAutoRun`". Matching the raw name
    would file the tightest kind of conflict there is -- policy deleting the
    exact value a tweak writes -- as an unrelated same-key match.
    """
    prefix = _VALUE_DIRECTIVE_PREFIXES.get(value.directive)
    if prefix and value.value_name.lower().startswith(prefix):
        return value.value_name[len(prefix):]
    if value.directive:
        return ""      # a key-level directive names no value
    return value.value_name


def is_policy_managed_key(key: str) -> bool:
    """True for the managed branches the Registry CSE owns and rewrites."""
    return "\\POLICIES\\" in "\\" + normalise_key(key) + "\\"


# --------------------------------------------------------------------------
# loading (never at import time)
# --------------------------------------------------------------------------

def default_definitions_dir() -> str:
    """`src/modules/tweaks/definitions`, resolved from this file's location."""
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(here, "tweaks", "definitions")


def load_tweak_definitions(
    definitions_dir: Optional[str] = None,
    notes: Optional[List[str]] = None,
) -> List[Dict[str, Any]]:
    """Every tweak definition on disk, each tagged with the file it came from.

    Reads `definitions/*.json` non-recursively -- `definitions/builtins/` holds
    presets (name/tweaks dicts), not tweaks. A file whose shape is not a list of
    step-bearing dicts is skipped with a note rather than crashing the report;
    a report that lists 19 categories instead of 20 and says why is far more
    useful than a traceback.
    """
    directory = definitions_dir or default_definitions_dir()
    collected: List[Dict[str, Any]] = []
    if not os.path.isdir(directory):
        message = "Tweak definitions directory not found: %s" % directory
        logger.warning(message)
        if notes is not None:
            notes.append(message)
        return collected

    for path in sorted(glob.glob(os.path.join(directory, "*.json"))):
        name = os.path.basename(path)
        if name in _NON_TWEAK_FILES:
            continue
        try:
            with open(path, "r", encoding="utf-8") as handle:
                data = json.load(handle)
        except (OSError, ValueError) as exc:
            # Carry the reason. A definition file we cannot read means the
            # report is incomplete, and the user has to be told which one.
            message = "Could not read %s: %s" % (name, exc)
            logger.warning(message)
            if notes is not None:
                notes.append(message)
            continue

        if not isinstance(data, list):
            continue
        for entry in data:
            if isinstance(entry, dict) and isinstance(entry.get("steps"), list):
                entry = dict(entry)
                entry.setdefault("_source_file", name)
                collected.append(entry)
    return collected


# --------------------------------------------------------------------------
# results
# --------------------------------------------------------------------------

@dataclass
class TweakConflict:
    """One tweak step overlapping one thing local Group Policy manages."""

    tweak_id: str = ""
    tweak_name: str = ""
    category: str = ""
    source_file: str = ""

    step_index: int = 0
    step_type: str = ""            # "registry" or "registry_delete"
    tweak_key: str = ""            # exactly as the JSON writes it
    tweak_value: str = ""
    tweak_data: Any = None
    tweak_kind: str = ""

    match: str = MATCH_BRANCH      # MATCH_DIRECT / MATCH_SAME_KEY / MATCH_BRANCH
    agreement: str = AGREE_UNCOMPARABLE

    scope: str = ""                # "Computer" or "User"
    hive: str = ""                 # "HKLM" or "HKCU"
    policy_key: str = ""           # hive-qualified
    policy_value: str = ""         # the value policy really names
    policy_data: Any = None
    policy_display: str = ""       # already-rendered, for a table cell
    policy_directive: str = ""     # "delete_value", "set_if_absent", ...
    policy_source: str = ""        # the Registry.pol it came from

    @property
    def tweak_path(self) -> str:
        return "%s\\%s" % (self.tweak_key, self.tweak_value) if self.tweak_value else self.tweak_key

    @property
    def policy_path(self) -> str:
        return "%s\\%s" % (self.policy_key, self.policy_value) if self.policy_value else self.policy_key

    @property
    def match_label(self) -> str:
        return MATCH_LABELS.get(self.match, self.match)

    @property
    def agreement_label(self) -> str:
        return AGREE_LABELS.get(self.agreement, self.agreement)

    @property
    def sort_key(self) -> Tuple[int, int, str, int]:
        """Tightest match first, real conflicts before duplicates."""
        return (_MATCH_RANK.get(self.match, 9),
                _AGREE_RANK.get(self.agreement, 9),
                self.tweak_id, self.step_index)

    def summary(self) -> str:
        """One sentence for a details panel. Deliberately not alarmist.

        The Registry CSE skips processing while no GPO has changed, so "can be
        reverted" is the truth and "is reverted every 90 minutes" is not.
        """
        where = "%s policy" % (self.scope or "Local")
        if self.match == MATCH_DIRECT:
            if self.step_type == "registry_delete":
                # A delete step wants the value *absent*, so "policy sets it to
                # the value this tweak writes" would be nonsense here.
                if self.agreement == AGREE_DUPLICATE:
                    return ("%s already removes %s, which is what this tweak does. "
                            "The tweak is redundant here." % (where, self.policy_path))
                return ("This tweak removes %s, and %s sets it to %s. Whenever the "
                        "Registry client-side extension processes, policy writes the "
                        "value back and this tweak can be undone without warning."
                        % (self.tweak_path, where, self.policy_display))
            if self.agreement == AGREE_DUPLICATE:
                return ("%s already sets %s to the same value this tweak writes "
                        "(%s). The tweak is redundant here, and policy keeps the "
                        "setting in place." % (where, self.policy_path, self.policy_display))
            if self.policy_directive:
                return ("%s carries a '%s' instruction for %s, the exact value this "
                        "tweak targets. Whenever the Registry client-side extension "
                        "processes, policy wins and this tweak can be undone without "
                        "warning." % (where, self.policy_directive.replace("_", " "),
                                      self.policy_path))
            return ("%s sets %s to %s, the exact value this tweak writes (%s). "
                    "Whenever the Registry client-side extension processes -- a "
                    "gpupdate, a GPO version change, a policy edit -- the policy "
                    "value is rewritten and this tweak can be undone without "
                    "warning." % (where, self.policy_path, self.policy_display,
                                  _render(self.tweak_data)))
        if self.match == MATCH_SAME_KEY:
            return ("%s manages %s under the same key this tweak writes to (%s). "
                    "The Registry client-side extension rewrites the managed key "
                    "as a whole, so values the app added here can be removed "
                    "without warning." % (where, self.policy_path, self.tweak_path))
        return ("%s manages %s, in the same branch as this tweak's %s. Group "
                "Policy owns this part of the registry, so changes written here "
                "outside policy can be undone without warning."
                % (where, self.policy_path, self.tweak_path))


@dataclass
class ConflictReport:
    """Everything a pane needs to render the cross-reference."""

    conflicts: List[TweakConflict] = field(default_factory=list)
    tweaks_examined: int = 0
    registry_steps: int = 0
    policy_branch_steps: int = 0    # steps writing under a `\Policies\` key
    policy_values: int = 0          # records read from Registry.pol
    scopes_read: List[str] = field(default_factory=list)   # scopes with a real file
    notes: List[str] = field(default_factory=list)         # why anything is missing

    @property
    def tweaks_at_risk(self) -> int:
        return len({c.tweak_id for c in self.conflicts})

    @property
    def direct_conflicts(self) -> List[TweakConflict]:
        """The ones policy will definitely overwrite and that disagree."""
        return [c for c in self.conflicts
                if c.match == MATCH_DIRECT and c.agreement == AGREE_CONFLICT]

    def by_tweak(self) -> Dict[str, List[TweakConflict]]:
        """Conflicts grouped by tweak id, each group in `sort_key` order."""
        grouped: Dict[str, List[TweakConflict]] = {}
        for conflict in self.conflicts:
            grouped.setdefault(conflict.tweak_id, []).append(conflict)
        for items in grouped.values():
            items.sort(key=lambda c: c.sort_key)
        return grouped

    def headline(self) -> str:
        """The honest one-liner for the top of the pane."""
        if not self.policy_values:
            return ("No local Group Policy registry settings were found, so none "
                    "of the %d tweak registry steps can be overwritten by local "
                    "policy today. %d of them still write into managed policy "
                    "branches, which any future GPO would take ownership of."
                    % (self.registry_steps, self.policy_branch_steps))
        if not self.conflicts:
            return ("None of the %d tweak registry steps overlap the %d setting(s) "
                    "local Group Policy manages on this machine."
                    % (self.registry_steps, self.policy_values))
        return ("%d tweak(s) overlap the %d setting(s) local Group Policy manages; "
                "%d of those overlaps write a different value than policy and can "
                "be undone without warning."
                % (self.tweaks_at_risk, self.policy_values,
                   sum(1 for c in self.conflicts if c.agreement == AGREE_CONFLICT)))


# --------------------------------------------------------------------------
# data comparison
# --------------------------------------------------------------------------

def _render(data: Any) -> str:
    if data is None:
        return "(none)"
    if isinstance(data, bytes):
        return data.hex()
    if isinstance(data, (list, tuple)):
        return ", ".join(str(part) for part in data)
    return str(data)


def _coerce(tweak_data: Any, policy_data: Any) -> Any:
    """Line the JSON-side value up with whatever the .pol decoder produced.

    JSON has no bytes and is loose about int-vs-string: `"data": 1` and
    `"data": "1"` both appear in the definitions for the same DWORD, and
    `pol_parser` hands back a real `int`. Comparing them raw reports a
    disagreement that does not exist.
    """
    if isinstance(policy_data, int) and isinstance(tweak_data, str):
        try:
            return int(tweak_data, 0)
        except ValueError:
            return tweak_data
    if isinstance(policy_data, str) and isinstance(tweak_data, int):
        return str(tweak_data)
    if isinstance(policy_data, bytes) and isinstance(tweak_data, str):
        try:
            text = tweak_data.strip()
            return bytes(int(b, 16) for b in text.split()) if " " in text else bytes.fromhex(text)
        except ValueError:
            return tweak_data
    if isinstance(policy_data, list) and isinstance(tweak_data, str):
        return [tweak_data]
    return tweak_data


def _agreement(step_type: str, step: Dict[str, Any], value: PolicyValue) -> str:
    """Does the tweak want what policy says? Only answerable for a direct hit."""
    deletes = value.directive in ("delete_value", "delete_all_values", "delete_values")
    if step_type == "registry_delete":
        # The tweak wants the value gone. Policy deleting it too is agreement;
        # policy setting it is the plainest disagreement there is.
        return AGREE_DUPLICATE if deletes else AGREE_CONFLICT
    if deletes:
        return AGREE_CONFLICT
    if value.directive:
        # "**soft." and the key-level directives set no data to compare against.
        return AGREE_UNCOMPARABLE
    if "data" not in step or step.get("data") in ("", None):
        # A key-existence step (no value, no data) has no intent to compare.
        return AGREE_UNCOMPARABLE
    expected = _coerce(step.get("data"), value.data)
    if isinstance(expected, str) and isinstance(value.data, str):
        return AGREE_DUPLICATE if expected.casefold() == value.data.casefold() else AGREE_CONFLICT
    try:
        return AGREE_DUPLICATE if expected == value.data else AGREE_CONFLICT
    except (TypeError, ValueError) as exc:
        # Two values of shapes that will not compare is a real, reportable
        # outcome -- not something to swallow into a false "duplicates".
        logger.debug("Could not compare %r with %r: %s", expected, value.data, exc)
        return AGREE_UNCOMPARABLE


# --------------------------------------------------------------------------
# the index
# --------------------------------------------------------------------------

class _PolicyIndex:
    """Normalised policy keys, arranged so matching is not O(steps x values).

    A local `Registry.pol` holds six records here, but a domain machine's holds
    thousands, and the tweak side is ~2500 steps. Exact keys go in a dict;
    branch matches use a sorted key list so "policy keys under this tweak key"
    is a bisect rather than a scan.
    """

    def __init__(self, pol_files: Sequence[PolFile]):
        self.by_key: Dict[str, List[Tuple[PolFile, PolicyValue, str]]] = {}
        self.value_count = 0
        self.scopes: List[str] = []
        self.notes: List[str] = []

        for pol in pol_files:
            if pol.error:
                # A file we could not parse is a hole in the report, not a
                # non-event: say so instead of reporting "no conflicts".
                logger.warning("Policy file unusable: %s", pol.error)
                self.notes.append(pol.error)
            if not pol.exists or not pol.values:
                continue
            self.scopes.append(pol.scope or pol.hive)
            for value in pol.values:
                key = normalise_key(policy_key_path(pol, value))
                if not key:
                    continue
                self.by_key.setdefault(key, []).append(
                    (pol, value, effective_value_name(value)))
                self.value_count += 1
        self.sorted_keys = sorted(self.by_key)

    def exact(self, key: str) -> List[Tuple[PolFile, PolicyValue, str]]:
        return self.by_key.get(key, [])

    def ancestors(self, key: str) -> List[str]:
        """Policy keys the tweak's key sits *under*."""
        found: List[str] = []
        parts = key.split("\\")
        for depth in range(1, len(parts)):
            parent = "\\".join(parts[:depth])
            if parent in self.by_key:
                found.append(parent)
        return found

    def descendants(self, key: str) -> List[str]:
        """Policy keys sitting *under* the tweak's key."""
        prefix = key + "\\"
        start = bisect.bisect_left(self.sorted_keys, prefix)
        found = []
        for candidate in self.sorted_keys[start:]:
            if not candidate.startswith(prefix):
                break
            found.append(candidate)
        return found


# --------------------------------------------------------------------------
# the analysis
# --------------------------------------------------------------------------

def registry_steps(tweaks: Iterable[Dict[str, Any]]) -> List[Tuple[Dict[str, Any], int, Dict[str, Any]]]:
    """`(tweak, step_index, step)` for every step that names a registry key."""
    found = []
    for tweak in tweaks:
        steps = tweak.get("steps")
        if not isinstance(steps, list):
            continue
        for index, step in enumerate(steps):
            if not isinstance(step, dict):
                continue
            if step.get("type") in REGISTRY_STEP_TYPES and step.get("key"):
                found.append((tweak, index, step))
    return found


def _conflict_for(
    tweak: Dict[str, Any], index: int, step: Dict[str, Any],
    pol: PolFile, value: PolicyValue, policy_value_name: str,
    match: str, agreement: str,
) -> TweakConflict:
    return TweakConflict(
        tweak_id=str(tweak.get("id", "")),
        tweak_name=str(tweak.get("name", "")),
        category=str(tweak.get("category", "")),
        source_file=str(tweak.get("_source_file", "")),
        step_index=index,
        step_type=str(step.get("type", "")),
        tweak_key=str(step.get("key", "")),
        tweak_value=str(step.get("value", "") or ""),
        tweak_data=step.get("data"),
        tweak_kind=str(step.get("kind", "") or ""),
        match=match,
        agreement=agreement,
        scope=pol.scope,
        hive=pol.hive,
        policy_key=policy_key_path(pol, value),
        policy_value=policy_value_name,
        policy_data=value.data,
        policy_display=value.display(),
        policy_directive=value.directive,
        policy_source=pol.path,
    )


def policy_managed_tweak_keys(
    tweaks: Optional[Sequence[Dict[str, Any]]] = None,
    definitions_dir: Optional[str] = None,
) -> Dict[str, List[str]]:
    """Tweak id -> the distinct registry keys, among that tweak's OWN steps,
    that sit inside a Group-Policy-managed branch (`\\Policies\\`).

    This needs no `Registry.pol` and no elevation -- it is a fact about the
    tweak DEFINITION, not about whether a live GPO exists today to fight over
    it. Measured on this real, non-domain-joined machine (2026-09-28):
    `find_conflicts()` reports zero conflicts because there is no local GPO
    to collide with, but 288 of the app's 814 registry steps still write into
    a `\\Policies\\` branch the Registry client-side extension owns --
    exactly the steps this maps by tweak id, so a machine with no policy
    today can still be told which of its tweaks a future GPO would silently
    take back. This is what `tweaks_module.py` surfaces before Apply; the
    aggregate count alone (`ConflictReport.policy_branch_steps`) told nobody
    WHICH tweak was at risk, and it lived only in the separate Group Policy
    pane, which `tweaks_module.py` never imported at all.
    """
    if tweaks is None:
        tweaks = load_tweak_definitions(definitions_dir)
    result: Dict[str, List[str]] = {}
    for tweak, _index, step in registry_steps(tweaks):
        key = step.get("key", "")
        if not is_policy_managed_key(key):
            continue
        tweak_id = str(tweak.get("id", ""))
        if not tweak_id:
            continue
        keys = result.setdefault(tweak_id, [])
        if key not in keys:
            keys.append(key)
    return result


def find_conflicts(
    tweaks: Optional[Sequence[Dict[str, Any]]] = None,
    pol_files: Optional[Sequence[PolFile]] = None,
    definitions_dir: Optional[str] = None,
) -> ConflictReport:
    """Cross-reference tweak definitions against local Group Policy.

    Both inputs are injectable. `tweaks=None` loads `definitions/*.json`;
    `pol_files=None` reads this machine's local GPO. Passing either makes the
    analysis independent of machine state, which is what the tests rely on.
    """
    notes: List[str] = []
    if tweaks is None:
        tweaks = load_tweak_definitions(definitions_dir, notes)
    if pol_files is None:
        pol_files = local_policy_files()

    index = _PolicyIndex(pol_files)
    notes.extend(index.notes)

    steps = registry_steps(tweaks)
    report = ConflictReport(
        tweaks_examined=len(list(tweaks)),
        registry_steps=len(steps),
        policy_branch_steps=sum(1 for _, _, s in steps if is_policy_managed_key(s.get("key", ""))),
        policy_values=index.value_count,
        scopes_read=list(index.scopes),
        notes=notes,
    )

    for tweak, position, step in steps:
        step_key = normalise_key(step.get("key", ""))
        if not step_key:
            continue
        step_value = str(step.get("value", "") or "")
        step_type = str(step.get("type", ""))

        # Exact key: the tightest two classes both live here.
        for pol, value, policy_value_name in index.exact(step_key):
            if step_value.casefold() == policy_value_name.casefold():
                report.conflicts.append(_conflict_for(
                    tweak, position, step, pol, value, policy_value_name,
                    MATCH_DIRECT, _agreement(step_type, step, value)))
            else:
                report.conflicts.append(_conflict_for(
                    tweak, position, step, pol, value, policy_value_name,
                    MATCH_SAME_KEY, AGREE_UNCOMPARABLE))

        # Branch, either direction. One row per related policy *key*, not per
        # value: five values under one managed key is one fact about the branch,
        # and five near-identical rows would bury the direct hits.
        for related in index.ancestors(step_key) + index.descendants(step_key):
            pol, value, policy_value_name = index.by_key[related][0]
            report.conflicts.append(_conflict_for(
                tweak, position, step, pol, value, policy_value_name,
                MATCH_BRANCH, AGREE_UNCOMPARABLE))

    report.conflicts.sort(key=lambda c: c.sort_key)
    return report
