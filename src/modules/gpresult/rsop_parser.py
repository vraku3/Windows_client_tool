"""Turn `gpresult /x` RSOP XML into something a tree can show.

Kept separate from the pane so it can be tested against real XML without a
QApplication. Three things this file exists to get right, each of which the
previous parser got wrong:

* **Computer and user results are separate subtrees.** The old parser called
  ``root.iter()`` once and filed everything it found under ``computer_gpos``,
  so a user GPO was reported as a computer GPO. Here each of
  ``<ComputerResults>`` and ``<UserResults>`` is walked on its own.

* **"No settings" and "we never looked" are different answers.** Unelevated,
  ``gpresult /x`` returns a report containing *only* ``<UserResults>`` and
  exits 0 -- the computer half is refused, silently. A scope therefore
  carries `available`, set from whether its element was present at all, not
  from whether its lists came back empty.

* **Nothing is dropped just because it was not anticipated.** Group Policy
  client-side extensions each define their own settings schema and there are
  dozens. Known shapes are named properly; everything else still surfaces,
  as its own leaf text, under a generically-titled node.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

# gpresult writes UTF-16 with an XML declaration, so ElementTree picks the
# encoding up on its own; nothing here should decode by hand.

#: Element local-names that answer "what is this setting called", best first.
_NAME_KEYS = (
    "Name", "KeyPath", "SubcategoryName", "KeyName", "DisplayName",
    "Command", "Path", "PolicyName", "SettingName", "Id",
)

#: Element local-names that answer "what is it set to", best first.
_VALUE_KEYS = (
    "State", "SettingNumber", "SettingBoolean", "SettingString",
    "SettingValue", "Display", "Number", "String", "Value", "Parameters",
    "Mode", "Enabled",
)

#: Children of an <Extension> that are containers, not settings in themselves.
_SKIP_CHILDREN = {"Name", "Identifier", "Extension"}


def _local(tag: str) -> str:
    """`{namespace}Tag` -> `Tag`."""
    return tag.rsplit("}", 1)[-1] if "}" in tag else tag


def _text(elem: Optional[ET.Element]) -> str:
    if elem is None or elem.text is None:
        return ""
    return elem.text.strip()


def _first_text(elem: ET.Element, names: Tuple[str, ...]) -> str:
    """Text of the first descendant whose local-name is in `names`.

    Ordered by `names`, not by document order: a `<Name>` buried under a
    `<GPO>` must not win over the setting's own `<State>`.
    """
    by_name: Dict[str, List[ET.Element]] = {}
    for node in elem.iter():
        by_name.setdefault(_local(node.tag), []).append(node)
    for wanted in names:
        for node in by_name.get(wanted, []):
            value = _text(node)
            if value:
                return value
    return ""


def _prettify(name: str) -> str:
    """`RegistrySettings` -> `Registry Settings`, `q1:Scripts` -> `Scripts`."""
    name = name.rsplit(":", 1)[-1]
    out = []
    for i, ch in enumerate(name):
        if ch.isupper() and i and not name[i - 1].isupper():
            out.append(" ")
        out.append(ch)
    return "".join(out).strip()


def _leaf_pairs(elem: ET.Element, prefix: str = "") -> List[Tuple[str, str]]:
    """Every leaf under `elem` as (dotted path, text).

    This is the safety net: whatever the named extraction misses is still
    reachable in the UI rather than quietly discarded.
    """
    pairs: List[Tuple[str, str]] = []
    for child in elem:
        path = "%s.%s" % (prefix, _local(child.tag)) if prefix else _local(child.tag)
        if len(child):
            pairs.extend(_leaf_pairs(child, path))
        else:
            value = _text(child)
            if value:
                pairs.append((path, value))
    return pairs


# ------------------------------------------------------------------
# Model
# ------------------------------------------------------------------

@dataclass
class GpoInfo:
    name: str = ""
    guid: str = ""
    enabled: bool = True
    is_valid: bool = True
    filter_allowed: bool = True
    access_denied: bool = False
    som_path: str = ""
    applied_order: str = ""
    link_order: str = ""
    no_override: bool = False
    version_directory: str = ""
    version_sysvol: str = ""

    @property
    def applied(self) -> bool:
        """Windows lists GPOs it *considered*; only some of them won."""
        return (self.enabled and self.is_valid and self.filter_allowed
                and not self.access_denied)

    @property
    def denied_reason(self) -> str:
        if self.access_denied:
            return "Access denied"
        if not self.filter_allowed:
            return "Denied by security filtering"
        if not self.is_valid:
            return "GPO is not valid"
        if not self.enabled:
            return "Link disabled"
        return ""


@dataclass
class ExtensionStatus:
    name: str = ""
    identifier: str = ""
    begin_time: str = ""
    end_time: str = ""
    logging_status: str = ""
    error: str = ""

    @property
    def failed(self) -> bool:
        """Error 0 is success. Anything else, including a blank, is not
        something to paint green."""
        return self.error not in ("0", "")


@dataclass
class PolicySetting:
    category: str = ""
    name: str = ""
    value: str = ""
    gpo: str = ""
    details: List[Tuple[str, str]] = field(default_factory=list)


@dataclass
class RsopScope:
    """One half of the report -- Computer or User."""

    scope: str = ""
    available: bool = False
    unavailable_reason: str = ""
    name: str = ""
    domain: str = ""
    som: str = ""
    site: str = ""
    slow_link: str = ""
    version: str = ""
    gpos: List[GpoInfo] = field(default_factory=list)
    security_groups: List[Tuple[str, str]] = field(default_factory=list)
    extensions: List[ExtensionStatus] = field(default_factory=list)
    settings: List[PolicySetting] = field(default_factory=list)

    @property
    def applied_gpos(self) -> List[GpoInfo]:
        return [g for g in self.gpos if g.applied]

    @property
    def denied_gpos(self) -> List[GpoInfo]:
        return [g for g in self.gpos if not g.applied]


def local_read_time(raw: str) -> str:
    """`2026-09-25T07:26:49.97Z` -> `2026-09-25 10:26:49` in local time.

    gpresult stamps its report in UTC (the trailing Z) and the pane printed it
    as though it were local, so on this machine (UTC+3) "Collected" read three
    hours before the clock. Only the DISPLAY is converted: the stored value is
    left exactly as gpresult wrote it, so saved snapshots stay comparable. An
    unparseable value is shown as it came rather than guessed at."""
    import datetime
    text = (raw or "").strip()
    if not text:
        return ""
    try:
        # Python 3.12 reads the trailing Z and truncates gpresult's 7-digit
        # fraction itself.
        moment = datetime.datetime.fromisoformat(text)
    except ValueError:
        return text.replace("T", " ")[:19]
    if moment.tzinfo is None:
        return text.replace("T", " ")[:19]          # no zone stated: leave alone
    return moment.astimezone().strftime("%Y-%m-%d %H:%M:%S")


@dataclass
class RsopResult:
    computer: RsopScope = field(default_factory=lambda: RsopScope("Computer"))
    user: RsopScope = field(default_factory=lambda: RsopScope("User"))
    read_time: str = ""
    data_type: str = ""
    error: str = ""

    @property
    def scopes(self) -> List[RsopScope]:
        return [self.computer, self.user]


# ------------------------------------------------------------------
# Parsing
# ------------------------------------------------------------------

def _parse_gpo(elem: ET.Element) -> GpoInfo:
    gpo = GpoInfo()
    gpo.name = _text(elem.find("{*}Name"))
    # The GUID lives at Path/Identifier -- the old parser looked for
    # `Identifier/Identifier` and then a `GUID` element, neither of which
    # exists, so the column was blank on every row.
    gpo.guid = (_text(elem.find("{*}Path/{*}Identifier"))
                or _text(elem.find("{*}Identifier")))
    gpo.enabled = _text(elem.find("{*}Enabled")).lower() != "false"
    gpo.is_valid = _text(elem.find("{*}IsValid")).lower() != "false"
    gpo.filter_allowed = _text(elem.find("{*}FilterAllowed")).lower() != "false"
    gpo.access_denied = _text(elem.find("{*}AccessDenied")).lower() == "true"
    gpo.version_directory = _text(elem.find("{*}VersionDirectory"))
    gpo.version_sysvol = _text(elem.find("{*}VersionSysvol"))
    link = elem.find("{*}Link")
    if link is not None:
        gpo.som_path = _text(link.find("{*}SOMPath"))
        gpo.applied_order = _text(link.find("{*}AppliedOrder"))
        gpo.link_order = _text(link.find("{*}LinkOrder"))
        gpo.no_override = _text(link.find("{*}NoOverride")).lower() == "true"
    return gpo


def _parse_extension_status(elem: ET.Element) -> ExtensionStatus:
    return ExtensionStatus(
        name=_text(elem.find("{*}Name")),
        identifier=_text(elem.find("{*}Identifier")),
        begin_time=_text(elem.find("{*}BeginTime")),
        end_time=_text(elem.find("{*}EndTime")),
        logging_status=_text(elem.find("{*}LoggingStatus")),
        error=_text(elem.find("{*}Error")),
    )


def _extension_title(elem: ET.Element) -> str:
    """Name an <Extension> from its xsi:type, falling back to its own Name."""
    xsi_type = elem.get("{http://www.w3.org/2001/XMLSchema-instance}type", "")
    if xsi_type:
        return _prettify(xsi_type)
    named = _text(elem.find("{*}Name"))
    return _prettify(named) if named else "Settings"


def _setting_name(child: ET.Element, searchable: ET.Element) -> str:
    """What to call this setting in the tree.

    A registry setting is identified by key *and* value name -- "AllowWindows"
    alone appears five times under different SrpV2 keys and tells the reader
    nothing about which one it is.
    """
    if _local(child.tag) == "RegistrySetting":
        key = _text(child.find("{*}KeyPath"))
        value_name = _text(child.find("{*}Value/{*}Name"))
        if key and value_name:
            return "%s\\%s" % (key, value_name)
        if key:
            return key
    return _first_text(searchable, _NAME_KEYS) or _prettify(_local(child.tag))


def _summarise(details: List[Tuple[str, str]], name: str) -> str:
    """A readable stand-in when no known value element matched.

    User rights assignments, for instance, carry their members as repeated
    sub-elements and no `<State>` anywhere; showing the members beats showing
    an empty cell.
    """
    values = [text for _, text in details
              if text != name and text not in name]
    if not values:
        return ""
    shown = ", ".join(values[:4])
    return shown + (", ..." if len(values) > 4 else "")


def _parse_settings(scope_elem: ET.Element) -> List[PolicySetting]:
    """Every configured setting under this scope, from every extension.

    Each direct child of an ``<Extension>`` is one setting. Its name, value
    and winning GPO are pulled by well-known local-names; the whole subtree is
    kept alongside as `details` so an extension whose schema is not
    anticipated still shows all of its data instead of an empty row.
    """
    settings: List[PolicySetting] = []
    for ext in scope_elem.iter():
        if _local(ext.tag) != "Extension":
            continue
        title = _extension_title(ext)
        for child in ext:
            if _local(child.tag) in _SKIP_CHILDREN:
                continue
            gpo_elem = child.find("{*}GPO")
            gpo_name = _text(gpo_elem.find("{*}Name")) if gpo_elem is not None else ""

            # A GPO block carries its own <Name>; excluding it keeps the
            # setting from being named after the policy that set it.
            searchable = ET.Element("wrapper")
            for grandchild in child:
                if _local(grandchild.tag) != "GPO":
                    searchable.append(grandchild)

            details = _leaf_pairs(child)
            name = _setting_name(child, searchable)
            value = (_first_text(searchable, _VALUE_KEYS)
                     or _summarise(details, name))
            category = _text(child.find("{*}Category")) or title
            settings.append(PolicySetting(
                category=category,
                name=name,
                value=value,
                gpo=gpo_name,
                details=details,
            ))
    return settings


def _parse_scope(scope_elem: ET.Element, scope_name: str) -> RsopScope:
    scope = RsopScope(scope=scope_name, available=True)
    scope.name = _text(scope_elem.find("{*}Name"))
    scope.domain = _text(scope_elem.find("{*}Domain"))
    scope.som = _text(scope_elem.find("{*}SOM"))
    scope.site = _text(scope_elem.find("{*}Site"))
    scope.slow_link = _text(scope_elem.find("{*}SlowLink"))
    scope.version = _text(scope_elem.find("{*}Version"))

    for child in scope_elem:
        tag = _local(child.tag)
        if tag == "GPO":
            scope.gpos.append(_parse_gpo(child))
        elif tag == "SecurityGroup":
            scope.security_groups.append((
                _text(child.find("{*}SID")), _text(child.find("{*}Name"))))
        elif tag == "ExtensionStatus":
            scope.extensions.append(_parse_extension_status(child))

    scope.settings = _parse_settings(scope_elem)
    return scope


def parse_rsop_xml(source) -> RsopResult:
    """Parse `gpresult /x` output. `source` is a path or an XML string/bytes.

    A scope that is absent from the document stays `available=False`, which
    is how the pane tells "this machine has no user policy" apart from "we
    were never allowed to ask".
    """
    result = RsopResult()
    try:
        if isinstance(source, (bytes, bytearray)):
            root = ET.fromstring(bytes(source))
        elif isinstance(source, str) and source.lstrip()[:1] == "<":
            root = ET.fromstring(source)
        else:
            root = ET.parse(source).getroot()
    except ET.ParseError as exc:
        result.error = "Could not parse the gpresult report: %s" % exc
        return result
    except OSError as exc:
        result.error = "Could not read the gpresult report: %s" % exc
        return result

    result.read_time = _text(root.find("{*}ReadTime"))
    result.data_type = _text(root.find("{*}DataType"))

    for tag, scope_name in (("ComputerResults", "Computer"),
                            ("UserResults", "User")):
        elem = root.find("{*}%s" % tag)
        if elem is None:
            continue
        parsed = _parse_scope(elem, scope_name)
        if scope_name == "Computer":
            result.computer = parsed
        else:
            result.user = parsed

    return result
