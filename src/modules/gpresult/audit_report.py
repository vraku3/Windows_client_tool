"""One shareable report over everything this pane knows, not just RSOP.

The "Export HTML" button already on this pane calls `gpresult /h` -- it is
Microsoft's own report and it is ONLY the resultant set of policy. Nothing it
writes mentions local-policy drift, a tattooed registry value, a tweak this
app applied that a future GPO would silently take back, or a per-user local
GPO -- every one of which is this tool's own finding, lives only in the
"Policy Audit" tree root, and has no export of its own. An admin handing this
machine's policy posture to someone else, or filing it for later comparison,
today has to screenshot a tree.

This module turns the same data the pane already holds (`RsopResult`, the
local `Registry.pol` files, a `DriftReport`, a `TattooedResult`, a
`ConflictReport`, the per-user local policies) into one self-contained HTML
file. No new scan, no new elevation requirement -- it is a renderer over
results the pane computed on the last Refresh, the same way `rsop_snapshot`
freezes the same shapes to JSON instead of HTML.

Three rules carried over from the rest of this package, because a report is
the worst place to quietly drop them:

* **An incomplete scan says so, loudly, instead of looking clean.** A
  `TattooedResult` that hit an access-denied key is NOT reported as "74
  tattooed values, nothing else here" -- `tattoo.complete` gates a visible
  caveat naming exactly which keys were refused. The same is true of
  `DriftReport`: an `unreadable` result is listed separately from `missing`,
  never folded into "drift found" or "nothing wrong".
* **A scope that was never collected says so.** `RsopScope.available` decides
  whether a scope's section reads "N GPOs, M settings" or the refusal reason
  -- the same distinction `rsop_parser` draws, carried into the one place an
  admin might print this out and act on it later without the live pane open.
* **Nothing here is alarmist.** `ConflictReport.headline()` and
  `TweakConflict.summary()` already word every finding carefully ("can be
  reverted without warning", never "is reverted every 90 minutes"); this
  module reuses those strings verbatim rather than re-summarising them into
  something louder.

No Qt here -- plain string building, `html.escape` on every piece of
machine-sourced text (a GPO name, a registry value, an account name can
contain `<`/`&`/`"` and this is going into an HTML file). Colours come from
`core.semantic_colors.semantic()`/`chrome()` so the report picks up the
app's OWN theme at the moment it is generated, rather than freezing a colour
literal that would read correctly in only one of the two themes -- the same
reasoning `tests/test_no_frozen_colours.py` holds the rest of this codebase
to.
"""

from __future__ import annotations

import html as _html
from dataclasses import dataclass
from datetime import datetime
from typing import List, Optional, Sequence

from core.semantic_colors import PANE_BACKGROUND, chrome, current_theme, semantic

from modules.gpresult.policy_drift import APPLIED, DriftReport
from modules.gpresult.pol_parser import PerUserLocalPolicy, PolFile
from modules.gpresult.rsop_parser import RsopResult, RsopScope, local_read_time
from modules.gpresult.tattooed import TattooedResult
from modules.gpresult.tweak_conflicts import ConflictReport


def _esc(value: object) -> str:
    """Anything -> escaped text. `None` becomes "", never the string "None"."""
    if value is None:
        return ""
    return _html.escape(str(value))


def _tag(name: str, text: object, css_class: str = "") -> str:
    cls = ' class="%s"' % css_class if css_class else ""
    return "<%s%s>%s</%s>" % (name, cls, _esc(text), name)


class _Raw(str):
    """A cell value that is ALREADY safe markup -- `_row` must not re-escape
    it (it would turn a built `<span class="state-missing">` into visible
    `&lt;span...&gt;` text). Every other cell is plain, untrusted,
    machine-sourced text and must go through `_esc`."""


def _row(*cells: object) -> str:
    parts = []
    for cell in cells:
        if isinstance(cell, _Raw):
            parts.append("<td>%s</td>" % cell)
        else:
            parts.append(_tag("td", cell))
    return "<tr>" + "".join(parts) + "</tr>"


def _state_cell(state: str) -> _Raw:
    return _Raw('<span class="state-%s">%s</span>' % (state, _esc(state)))


@dataclass(frozen=True)
class _Theme:
    """The handful of colours the report's own CSS needs, read once."""

    bg: str
    text: str
    muted: str
    border: str
    card: str


def _report_theme() -> _Theme:
    """The handful of colours the report needs, read from the live theme.

    `PANE_BACKGROUND`/`chrome()`/`semantic()` are the same lookups the rest
    of the app uses to stay theme-correct -- no colour literal belongs here,
    the same rule `tests/test_no_frozen_colours.py` holds every other pane
    to. `current_theme()` names which palette is active; the phosphor themes
    (derived from dark, see `core/phosphor.py`) resolve through the same
    dicts automatically.
    """
    return _Theme(
        bg=PANE_BACKGROUND[current_theme()],
        text=chrome("text"),
        muted=chrome("text_muted"),
        border=chrome("outline"),
        card=chrome("surface"),
    )


def _style(theme: _Theme) -> str:
    return (
        "body{background:%s;color:%s;font-family:Segoe UI,Arial,sans-serif;"
        "margin:24px;}"
        "h1{font-size:20px;} h2{font-size:16px;border-bottom:1px solid %s;"
        "padding-bottom:4px;margin-top:28px;}"
        "h3{font-size:13px;color:%s;margin-top:18px;}"
        ".meta{color:%s;font-size:12px;margin-bottom:18px;}"
        ".card{background:%s;border:1px solid %s;border-radius:6px;"
        "padding:10px 14px;margin:8px 0;}"
        ".caveat{border-left:4px solid %s;padding-left:10px;}"
        "table{border-collapse:collapse;width:100%%;margin:6px 0;}"
        "td,th{border:1px solid %s;padding:4px 8px;font-size:12px;"
        "text-align:left;vertical-align:top;}"
        "th{color:%s;}"
        ".state-applied{color:%s;} .state-different{color:%s;}"
        ".state-missing{color:%s;} .state-unreadable{color:%s;}"
        % (theme.bg, theme.text, theme.border, theme.muted, theme.muted,
           theme.card, theme.border, semantic("warning"), theme.border,
           theme.muted, semantic("success"), semantic("error"),
           semantic("warning"), semantic("info")))


# ---------------------------------------------------------------------------
# RSOP section
# ---------------------------------------------------------------------------

def _scope_section(scope: RsopScope) -> str:
    out = ["<h2>%s Configuration</h2>" % _esc(scope.scope)]
    if not scope.available:
        out.append('<p class="card caveat">Not collected: %s</p>'
                   % _esc(scope.unavailable_reason or "no reason given"))
        return "".join(out)

    applied = scope.applied_gpos
    denied = scope.denied_gpos
    out.append("<p>%d GPO(s) applied, %d denied, %d setting(s).</p>"
               % (len(applied), len(denied), len(scope.settings)))

    if applied:
        out.append("<h3>Applied GPOs</h3><table><tr><th>Name</th>"
                   "<th>GUID</th></tr>")
        out.extend(_row(g.name or "(unnamed)", g.guid) for g in applied)
        out.append("</table>")
    if denied:
        out.append("<h3>Denied GPOs</h3><table><tr><th>Name</th>"
                   "<th>Reason</th></tr>")
        out.extend(_row(g.name or "(unnamed)", g.denied_reason) for g in denied)
        out.append("</table>")

    failed = [e for e in scope.extensions if e.failed]
    if failed:
        out.append("<h3>Client-side extensions reporting an error</h3>"
                   "<table><tr><th>Extension</th><th>Error</th></tr>")
        out.extend(_row(e.name, e.error) for e in failed)
        out.append("</table>")
    return "".join(out)


def _rsop_section(result: RsopResult) -> str:
    out = ["<h2>Resultant Set of Policy</h2>"]
    if result.error:
        out.append('<p class="card caveat">%s</p>' % _esc(result.error))
        return "".join(out)
    if result.read_time:
        out.append("<p class=\"meta\">Collected %s</p>"
                   % _esc(local_read_time(result.read_time)))
    out.append(_scope_section(result.computer))
    out.append(_scope_section(result.user))
    return "".join(out)


# ---------------------------------------------------------------------------
# Local policy drift
# ---------------------------------------------------------------------------

def _drift_section(drift: Optional[DriftReport]) -> str:
    out = ["<h2>Local Policy Drift</h2>"]
    if drift is None:
        out.append("<p>Not checked on this run.</p>")
        return "".join(out)
    if drift.errors:
        out.append('<p class="card caveat">Could not read: %s</p>'
                   % _esc("; ".join(drift.errors)))
    if not drift.results:
        out.append("<p>No local Group Policy registry settings were found "
                   "on this machine to compare.</p>")
        return "".join(out)
    out.append("<p>%s.</p>" % _esc(drift.summary()))

    interesting = [r for r in drift.results if r.state != APPLIED]
    if not interesting:
        out.append("<p>Every local policy setting is in effect as written.</p>")
        return "".join(out)
    out.append("<table><tr><th>Setting</th><th>State</th><th>Why</th></tr>")
    for item in interesting:
        out.append(_row(item.full_path or item.key,
                        _state_cell(item.state), item.reason))
    out.append("</table>")
    return "".join(out)


# ---------------------------------------------------------------------------
# Tattooed policy
# ---------------------------------------------------------------------------

def _tattoo_section(tattoo: Optional[TattooedResult]) -> str:
    out = ["<h2>Set Outside Group Policy (Tattooed)</h2>"]
    if tattoo is None:
        out.append("<p>Not checked on this run.</p>")
        return "".join(out)
    if not tattoo.complete:
        out.append('<p class="card caveat">This scan is INCOMPLETE -- treat '
                   "\"nothing found\" below as \"nothing found in what could "
                   'be read". %s</p>' % _esc(tattoo.summary()))
    else:
        out.append("<p>%s</p>" % _esc(tattoo.summary()))
    if not tattoo.tattooed:
        out.append("<p>No values in the managed policy branches are "
                   "unaccounted for.</p>")
        return "".join(out)
    out.append("<table><tr><th>Key</th><th>Value</th><th>Data</th></tr>")
    for value in sorted(tattoo.tattooed, key=lambda v: v.full_path):
        out.append(_row(value.key_path, value.value_name or "(default)",
                        value.display()))
    out.append("</table>")
    return "".join(out)


# ---------------------------------------------------------------------------
# Tweak conflicts
# ---------------------------------------------------------------------------

def _conflicts_section(conflicts: Optional[ConflictReport]) -> str:
    out = ["<h2>Tweaks At Risk From Group Policy</h2>"]
    if conflicts is None:
        out.append("<p>Not checked on this run.</p>")
        return "".join(out)
    out.append("<p>%s</p>" % _esc(conflicts.headline()))
    direct = conflicts.direct_conflicts
    if not direct:
        return "".join(out)
    out.append("<table><tr><th>Tweak</th><th>Writes</th><th>Policy</th>"
               "<th>Why</th></tr>")
    for c in direct:
        out.append(_row(c.tweak_name or c.tweak_id, c.tweak_path,
                        c.policy_path, c.summary()))
    out.append("</table>")
    return "".join(out)


# ---------------------------------------------------------------------------
# Per-user local policy
# ---------------------------------------------------------------------------

def _per_user_section(entries: Sequence[PerUserLocalPolicy]) -> str:
    out = ["<h2>Per-User Local Policies</h2>"]
    if not entries:
        out.append("<p>Multiple Local GPO is not configured on this "
                   "machine.</p>")
        return "".join(out)
    out.append("<table><tr><th>Account</th><th>SID</th>"
               "<th>Settings</th></tr>")
    for entry in entries:
        label = entry.account_name if entry.resolved else "(SID did not resolve)"
        count = len(entry.pol.settings) if entry.pol else 0
        out.append(_row(label, entry.sid, count))
    out.append("</table>")
    return "".join(out)


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def build_audit_report_html(
    result: RsopResult,
    local_policy: Sequence[PolFile] = (),
    drift: Optional[DriftReport] = None,
    tattoo: Optional[TattooedResult] = None,
    conflicts: Optional[ConflictReport] = None,
    per_user_policy: Sequence[PerUserLocalPolicy] = (),
    generated_at: Optional[datetime] = None,
) -> str:
    """One self-contained HTML file covering everything this pane found.

    Every argument is a result this pane already computed on its last
    Refresh (see `GPResultModule._on_result`) -- this function runs no
    command and touches no registry, so it costs nothing beyond string
    building and is safe to call from the UI thread if a caller ever wants
    to.
    """
    theme = _report_theme()
    stamp = (generated_at or datetime.now()).strftime("%Y-%m-%d %H:%M:%S")
    local_policy = list(local_policy)
    unreadable_pol = [p for p in local_policy if p.error]

    body: List[str] = [
        "<h1>Group Policy Audit Report</h1>",
        '<p class="meta">Generated %s by WinClientTool</p>' % _esc(stamp),
    ]
    if unreadable_pol:
        body.append('<p class="card caveat">Could not read: %s</p>' % _esc(
            "; ".join("%s (%s)" % (p.path, p.error) for p in unreadable_pol)))

    body.append(_rsop_section(result))
    body.append(_drift_section(drift))
    body.append(_tattoo_section(tattoo))
    body.append(_conflicts_section(conflicts))
    body.append(_per_user_section(per_user_policy))

    return (
        "<!DOCTYPE html><html><head><meta charset=\"utf-8\">"
        "<title>Group Policy Audit Report</title>"
        "<style>%s</style></head><body>%s</body></html>"
        % (_style(theme), "".join(body))
    )


def write_audit_report(
    path: str,
    result: RsopResult,
    local_policy: Sequence[PolFile] = (),
    drift: Optional[DriftReport] = None,
    tattoo: Optional[TattooedResult] = None,
    conflicts: Optional[ConflictReport] = None,
    per_user_policy: Sequence[PerUserLocalPolicy] = (),
) -> None:
    """Render and write the report. Raises `OSError` on a write failure --
    the caller is asking for a file and has to hear about a disk that said
    no, the same contract `rsop_snapshot.save_snapshot` follows."""
    document = build_audit_report_html(
        result, local_policy, drift, tattoo, conflicts, per_user_policy)
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(document)
