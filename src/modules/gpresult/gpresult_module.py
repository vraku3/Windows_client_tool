"""Group Policy — the Resultant Set of Policy for this machine and user.

This is a *report*, not an editor: it answers "which GPOs won, and what did
they actually set". `gpedit.msc` is one button away for the editing side.

What the previous version of this pane got wrong, and what this one does
instead:

* It listed GPO names and nothing else, so double-clicking a row did nothing
  because the rows had no children to expand. The settings each GPO delivered
  were parsed into a dict key that no code ever read. Settings are now the
  bulk of the tree, nested by their own category path the way gpedit nests
  them.
* It filed user results under `computer_gpos`, because it walked the whole
  document with one `root.iter()` instead of walking each scope separately.
  Computer and User are now two separate roots and cannot be confused.
* Unelevated, it showed the user half of the report and said nothing about
  the computer half being refused. A scope that was not collected now says
  so, in a banner, with the reason.
"""

from __future__ import annotations

import logging
import os
import subprocess
from typing import List, Optional

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QBrush, QColor
from PyQt6.QtWidgets import (
    QFileDialog, QHBoxLayout, QHeaderView, QLabel, QLineEdit, QMessageBox,
    QProgressBar, QPushButton, QTreeWidget, QTreeWidgetItem, QVBoxLayout,
    QWidget,
)

from core.base_module import BaseModule
from core.module_groups import ModuleGroup
from core.semantic_colors import semantic
from core.worker import Worker
from ui.error_banner import ErrorBanner

from modules.gpresult.admx_catalog import get_catalog
from modules.gpresult.policy_drift import (
    APPLIED, DIFFERENT, DriftReport, MISSING, UNREADABLE, drift_report,
)
from modules.gpresult.pol_parser import PolFile, local_policy_files
from modules.gpresult.rsop_parser import local_read_time
from modules.gpresult.rsop_parser import (
    GpoInfo, PolicySetting, RsopResult, RsopScope,
)
from modules.gpresult.rsop_runner import (
    collect_rsop, export_html_report, mmc_console_path,
)
from modules.gpresult.tattooed import TattooedResult, find_tattooed
from modules.gpresult.tweak_conflicts import ConflictReport, find_conflicts

logger = logging.getLogger(__name__)

_COLS = ["Setting", "Value", "Source GPO"]
_COL_WIDTHS = [520, 300, 220]

#: The banner paints a fixed background, so it must paint its foreground too:
#: left to the theme, `dark.qss` would put #d4d4d4 on this pale yellow at
#: roughly 1.3:1. Same reasoning -- and the same pair of colours -- as the
#: Sysinternals banner in the Process Explorer pane; both are held to 4.5:1
#: by a test that computes the ratio.
BANNER_STYLE = (
    "background:#fff3cd;"
    "color:#664d03;"
    "padding:6px;"
    "border:1px solid #e0c877;"
    "border-radius:4px;"
)

#: Roles carried on tree items so the filter can tell structure from content.
_ROLE_SEARCH = Qt.ItemDataRole.UserRole + 1

#: How a local-policy row is painted once we know whether it took effect.
#: `unreadable` is deliberately not an error colour -- being unable to look is
#: not the same as finding something wrong, and colouring it like a fault
#: turns a permissions problem into a phantom policy problem.
_DRIFT_COLOURS = {
    APPLIED: "success",
    DIFFERENT: "error",
    MISSING: "warning",
    UNREADABLE: "info",
}


class GPResultModule(BaseModule):
    name = "Group Policy"
    icon = "📋"
    description = "Resultant Set of Policy: which GPOs applied and what they set"
    requires_admin = False
    group = ModuleGroup.MANAGE

    def __init__(self) -> None:
        super().__init__()
        self._busy = False
        self._loaded_once = False
        self._result: Optional[RsopResult] = None
        self._local_policy: List[PolFile] = []
        self._drift: Optional[DriftReport] = None
        self._drift_by_path = {}
        self._tattoo: Optional[TattooedResult] = None
        self._conflicts: Optional[ConflictReport] = None

    # ------------------------------------------------------------------
    # UI
    # ------------------------------------------------------------------

    def create_widget(self, parent=None) -> QWidget:
        outer = QWidget(parent)
        layout = QVBoxLayout(outer)
        layout.setContentsMargins(8, 8, 8, 8)
        self._outer = outer

        toolbar = QHBoxLayout()
        self._refresh_btn = QPushButton("Refresh")
        self._refresh_btn.setToolTip("Re-run gpresult and rebuild the report")
        self._expand_btn = QPushButton("Expand All")
        self._collapse_btn = QPushButton("Collapse All")
        self._gpupdate_btn = QPushButton("Refresh Policy...")
        self._gpupdate_btn.setToolTip(
            "Run gpupdate to reapply Group Policy, with live output")
        self._snapshot_btn = QPushButton("Snapshot")
        self._snapshot_btn.setToolTip(
            "Save this report so a later one can be compared against it")
        self._compare_btn = QPushButton("Compare...")
        self._compare_btn.setToolTip(
            "See what has changed since a saved report")
        self._export_btn = QPushButton("Export HTML")
        self._export_btn.setToolTip("Microsoft's own full RSOP report")
        self._gpedit_btn = QPushButton("Open gpedit.msc")
        self._rsop_btn = QPushButton("Open rsop.msc")
        self._search_edit = QLineEdit()
        self._search_edit.setPlaceholderText("Filter settings, GPOs, groups...")
        self._search_edit.setClearButtonEnabled(True)
        self._search_edit.setMaximumWidth(280)
        self._search_edit.setMinimumWidth(180)

        # gpedit.msc and rsop.msc ship with Pro and above. On Home they are
        # simply absent, and a button that opens nothing is worse than a
        # button that explains itself.
        for btn, console in ((self._gpedit_btn, "gpedit.msc"),
                             (self._rsop_btn, "rsop.msc")):
            if mmc_console_path(console) is None:
                btn.setEnabled(False)
                btn.setToolTip(
                    "%s is not installed on this edition of Windows "
                    "(it ships with Pro and above)." % console)

        for btn in (self._refresh_btn, self._gpupdate_btn, self._snapshot_btn,
                    self._compare_btn, self._expand_btn, self._collapse_btn,
                    self._export_btn, self._gpedit_btn, self._rsop_btn):
            toolbar.addWidget(btn)
        toolbar.addWidget(self._search_edit)
        toolbar.addStretch()
        self._status_lbl = QLabel("Loading Group Policy results...")
        toolbar.addWidget(self._status_lbl)
        layout.addLayout(toolbar)

        self._progress = QProgressBar()
        self._progress.setRange(0, 0)
        self._progress.setFixedHeight(4)
        self._progress.hide()
        layout.addWidget(self._progress)

        self._error_banner = ErrorBanner()
        self._error_banner.hide()
        layout.addWidget(self._error_banner)

        self._banner = QLabel()
        self._banner.setStyleSheet(BANNER_STYLE)
        self._banner.setWordWrap(True)
        self._banner.hide()
        layout.addWidget(self._banner)

        self._info_lbl = QLabel("")
        self._info_lbl.setStyleSheet("color: gray;")
        layout.addWidget(self._info_lbl)

        self._tree = QTreeWidget()
        self._tree.setColumnCount(len(_COLS))
        self._tree.setHeaderLabels(_COLS)
        self._tree.setAlternatingRowColors(True)
        self._tree.setUniformRowHeights(True)
        self._tree.setTextElideMode(Qt.TextElideMode.ElideMiddle)
        header = self._tree.header()
        for i, width in enumerate(_COL_WIDTHS):
            header.setSectionResizeMode(i, QHeaderView.ResizeMode.Interactive)
            self._tree.setColumnWidth(i, width)
        header.setStretchLastSection(True)
        layout.addWidget(self._tree, 1)

        self._refresh_btn.clicked.connect(self._do_refresh)
        self._expand_btn.clicked.connect(self._tree.expandAll)
        self._collapse_btn.clicked.connect(self._collapse_to_roots)
        self._export_btn.clicked.connect(self._export_html)
        self._gpupdate_btn.clicked.connect(self._open_gpupdate)
        self._snapshot_btn.clicked.connect(self._save_snapshot)
        self._compare_btn.clicked.connect(self._open_compare)
        self._gpedit_btn.clicked.connect(lambda: self._open_console("gpedit.msc"))
        self._rsop_btn.clicked.connect(lambda: self._open_console("rsop.msc"))
        self._search_edit.textChanged.connect(self._apply_filter)

        return outer

    # ------------------------------------------------------------------
    # Loading
    # ------------------------------------------------------------------

    def _do_refresh(self) -> None:
        if self._busy:
            self._status_lbl.setText("Already running — please wait.")
            return
        self._busy = True
        self._refresh_btn.setEnabled(False)
        self._progress.show()
        self._status_lbl.setText("Running gpresult...")

        def work(_worker):
            # All three in one worker: the RSOP call needs elevation for the
            # computer scope, the .pol files never do, and the pane wants to
            # show whichever it managed to get. The drift pass is file and
            # registry reads only -- about a millisecond -- so it costs
            # nothing to fold in here.
            pols = local_policy_files()
            # 0.16s to index 3,550 policy definitions from 224 ADMX files --
            # cheap, but not on the UI thread, and cached for the process
            # after this.
            try:
                get_catalog().ensure_loaded()
            except OSError:
                logger.warning("Could not build the ADMX catalogue",
                               exc_info=True)
            return (collect_rsop(), pols, drift_report(pol_files=pols),
                    find_tattooed(pol_files=pols), find_conflicts(pol_files=pols))

        worker = Worker(work)
        worker.signals.result.connect(self._on_loaded)
        worker.signals.error.connect(self._on_error)
        self._workers.append(worker)
        self.thread_pool.start(worker)

    def _on_loaded(self, payload) -> None:
        self._on_result(*payload)

    def _on_result(self, result: RsopResult,
                   local_policy: Optional[List[PolFile]] = None,
                   drift: Optional[DriftReport] = None,
                   tattoo: Optional[TattooedResult] = None,
                   conflicts: Optional[ConflictReport] = None) -> None:
        self._busy = False
        self._loaded_once = True
        self._refresh_btn.setEnabled(True)
        self._progress.hide()
        self._result = result
        self._local_policy = local_policy or []
        self._drift = drift
        self._tattoo = tattoo
        self._conflicts = conflicts
        self._rebuild_tree(result, self._local_policy, drift, tattoo, conflicts)

    def _on_error(self, err: str) -> None:
        self._busy = False
        self._refresh_btn.setEnabled(True)
        self._progress.hide()
        self._status_lbl.setText("Error: %s" % err)
        self._show_banner("Could not read Group Policy results: %s" % err)

    # ------------------------------------------------------------------
    # Tree
    # ------------------------------------------------------------------

    def _show_banner(self, text: str) -> None:
        self._banner.setText(text)
        self._banner.show()

    @staticmethod
    def _node(parent, label: str, value: str = "", gpo: str = "") -> QTreeWidgetItem:
        item = QTreeWidgetItem(parent, [label, value, gpo])
        item.setData(0, _ROLE_SEARCH,
                     ("%s %s %s" % (label, value, gpo)).lower())
        return item

    @staticmethod
    def _bold(item: QTreeWidgetItem) -> QTreeWidgetItem:
        font = item.font(0)
        font.setBold(True)
        item.setFont(0, font)
        return item

    @staticmethod
    def _paint(item: QTreeWidgetItem, meaning: str) -> QTreeWidgetItem:
        item.setForeground(0, QBrush(QColor(semantic(meaning))))
        return item

    def _rebuild_tree(self, result: RsopResult,
                      local_policy: Optional[List[PolFile]] = None,
                      drift: Optional[DriftReport] = None,
                      tattoo: Optional[TattooedResult] = None,
                      conflicts: Optional[ConflictReport] = None) -> None:
        self._tree.clear()
        self._banner.hide()

        by_scope = {pol.scope: pol for pol in (local_policy or [])}
        # Keyed on the full HIVE\key\value path, which is unique per record
        # and is what the local-policy rows are labelled with.
        self._drift_by_path = {d.full_path: d for d in (drift.results if drift
                                                        else [])}

        if result.error:
            self._status_lbl.setText("gpresult failed.")
            self._show_banner(result.error)
            return

        blocked = []
        for scope in result.scopes:
            if scope.available or not scope.unavailable_reason:
                continue
            reason = scope.unavailable_reason
            # A refusal we can work around is worth saying so: local policy
            # is readable by any user, and on a machine that is not
            # domain-joined it is the only policy there is.
            pol = by_scope.get(scope.scope)
            if pol is not None and pol.settings:
                reason += (" Showing this machine's local policy (%d setting(s) "
                           "read straight from Registry.pol) in the meantime."
                           % len(pol.settings))
            blocked.append(reason)
        if blocked:
            self._show_banner("  •  ".join(blocked))

        total_settings = 0
        for scope in result.scopes:
            total_settings += len(scope.settings)
            self._build_scope(scope, by_scope.get(scope.scope))

        self._build_audit(tattoo, conflicts)

        parts = []
        if result.read_time:
            parts.append("Collected %s" % local_read_time(result.read_time))
        if result.data_type:
            parts.append(result.data_type)
        self._info_lbl.setText("  |  ".join(parts))

        collected = [s.scope for s in result.scopes if s.available]
        self._status_lbl.setText(
            "%s scope(s): %s  |  %d setting(s)"
            % (len(collected), ", ".join(collected) or "none", total_settings))

        # Roots and their section headers open; the detail stays folded.
        for i in range(self._tree.topLevelItemCount()):
            root = self._tree.topLevelItem(i)
            root.setExpanded(True)
            for j in range(root.childCount()):
                root.child(j).setExpanded(True)
        self._apply_filter(self._search_edit.text())

    def _build_scope(self, scope: RsopScope,
                     pol: Optional[PolFile] = None) -> None:
        title = "%s Configuration" % scope.scope
        root = self._bold(self._node(self._tree, title))

        if not scope.available:
            root.setText(1, "not collected")
            self._paint(root, "warning")
            reason = self._node(root, scope.unavailable_reason
                                or "This scope was not collected.")
            self._paint(reason, "warning")
            # The RSOP call was refused, but the .pol file was not: show what
            # local policy configures rather than leaving the half blank.
            self._build_local_policy(root, pol)
            return

        root.setText(1, scope.name)

        summary = self._bold(self._node(root, "Summary"))
        for label, value in (("Name", scope.name),
                             ("Domain", scope.domain),
                             ("Scope of Management", scope.som),
                             ("Site", scope.site),
                             ("Slow link", scope.slow_link),
                             ("Version", scope.version)):
            if value:
                self._node(summary, label, value)

        applied = scope.applied_gpos
        node = self._bold(self._node(root, "Applied GPOs", str(len(applied))))
        if not applied:
            self._node(node, "No Group Policy Object applied to this scope.")
        for gpo in applied:
            self._build_gpo(node, gpo)

        denied = scope.denied_gpos
        if denied:
            node = self._bold(self._node(root, "Denied GPOs", str(len(denied))))
            self._paint(node, "warning")
            for gpo in denied:
                child = self._build_gpo(node, gpo)
                child.setText(1, gpo.denied_reason)
                self._paint(child, "warning")

        self._build_settings(root, scope.settings)
        self._build_local_policy(root, pol)

        node = self._bold(self._node(root, "Security Groups",
                                     str(len(scope.security_groups))))
        for sid, group_name in scope.security_groups:
            self._node(node, group_name or sid, sid)

        failed = [e for e in scope.extensions if e.failed]
        node = self._bold(self._node(root, "Extension Status",
                                     str(len(scope.extensions))))
        if failed:
            self._paint(node, "error")
        for ext in scope.extensions:
            label = ext.name or ext.identifier
            child = self._node(node, label, ext.logging_status)
            if ext.failed:
                child.setText(1, "%s (error %s)" % (ext.logging_status or "Failed",
                                                    ext.error))
                self._paint(child, "error")
            for detail_label, detail in (("Identifier", ext.identifier),
                                         ("Begin", ext.begin_time),
                                         ("End", ext.end_time)):
                if detail:
                    self._node(child, detail_label, detail)

    def _build_gpo(self, parent: QTreeWidgetItem, gpo: GpoInfo) -> QTreeWidgetItem:
        item = self._node(parent, gpo.name or "(unnamed GPO)", "", gpo.guid)
        for label, value in (("GUID", gpo.guid),
                             ("Link", gpo.som_path),
                             ("Applied order", gpo.applied_order),
                             ("Link order", gpo.link_order),
                             ("Enforced (No Override)",
                              "Yes" if gpo.no_override else "No"),
                             ("Version (directory/sysvol)",
                              "%s / %s" % (gpo.version_directory,
                                           gpo.version_sysvol)
                              if gpo.version_directory else "")):
            if value:
                self._node(item, label, value)
        return item

    def _build_audit(self, tattoo: Optional[TattooedResult],
                     conflicts: Optional[ConflictReport]) -> None:
        """Findings about policy that the RSOP report itself cannot tell you.

        Kept as its own root rather than folded into the two scopes: neither
        of these is something Windows reported, they are both this tool
        comparing two sources and noticing a discrepancy.
        """
        if tattoo is None and conflicts is None:
            return
        root = self._bold(self._node(self._tree, "Policy Audit"))

        if tattoo is not None:
            self._build_tattooed(root, tattoo)
        if conflicts is not None:
            self._build_conflicts(root, conflicts)

    def _build_tattooed(self, root: QTreeWidgetItem,
                        tattoo: TattooedResult) -> None:
        """Values living in Group Policy's branches that no .pol accounts for.

        These survive `gpupdate` forever and never appear in gpedit, which is
        what makes them worth a section of their own. Grouped by branch on
        purpose: a flat list reads as far more alarming than it is, because
        Windows' own shipped UAC defaults under CurrentVersion\\Policies\\System
        are technically tattooed too.
        """
        node = self._bold(self._node(root, "Set outside Group Policy",
                                     str(len(tattoo.tattooed))))
        node.setToolTip(
            0, "Values in Group Policy's own registry branches that no "
               "Registry.pol accounts for. gpedit will not show these, and a "
               "policy refresh will not remove them.")
        if tattoo.tattooed:
            self._paint(node, "warning")

        # "Nothing found" is only true if we managed to look everywhere.
        if not tattoo.complete:
            caveat = self._node(
                node, "This scan was incomplete: %d key(s) could not be read%s."
                % (tattoo.unreadable_key_count,
                   " and a limit was reached" if tattoo.capped else ""))
            self._paint(caveat, "info")
            for branch in tattoo.branches:
                for key in branch.unreadable_keys:
                    self._node(caveat, key, "access denied")

        by_branch = {}
        for value in tattoo.tattooed:
            by_branch.setdefault(value.branch, []).append(value)
        for branch in sorted(by_branch):
            found = by_branch[branch]
            group = self._node(node, branch, str(len(found)))
            for value in found:
                item = self._node(group, value.full_path, value.display())
                self._node(item, "Type", value.type_name)

        for warning in tattoo.warnings:
            self._paint(self._node(node, warning), "warning")

    def _build_conflicts(self, root: QTreeWidgetItem,
                         report: ConflictReport) -> None:
        """Tweaks this app can apply that sit on Group-Policy-managed keys.

        Worth surfacing because the Registry extension rewrites those branches
        when it processes, so such a tweak can be undone with no error and no
        trace.
        """
        node = self._bold(self._node(root, "Tweaks at risk from Group Policy",
                                     str(report.tweaks_at_risk)))
        node.setToolTip(0, report.headline())
        if report.conflicts:
            self._paint(node, "warning")

        self._node(node, "Summary", report.headline())
        stats = self._node(node, "Scope of the check")
        self._node(stats, "Tweaks examined", str(report.tweaks_examined))
        self._node(stats, "Registry steps", str(report.registry_steps))
        self._node(stats, "Steps writing into policy branches",
                   str(report.policy_branch_steps))
        self._node(stats, "Policy values on this machine",
                   str(report.policy_values))

        for tweak_id, items in report.by_tweak().items():
            first = items[0]
            item = self._node(node, first.tweak_name or tweak_id,
                              first.match_label, first.category)
            for conflict in items:
                detail = self._node(item, conflict.tweak_path,
                                    conflict.agreement_label)
                self._node(detail, "Policy", conflict.policy_path)
                self._node(detail, "Policy value", conflict.policy_display)
                self._node(detail, "Why", conflict.summary())

        for note in report.notes:
            self._paint(self._node(node, note), "warning")

    @staticmethod
    def _describe(key: str, value_name: str, scope_class: str):
        """The ADMX definition for a policy value, or None if nothing has one.

        A miss is normal and must stay silent: AppLocker/SrpV2 -- which is
        exactly what is configured on a stock machine -- has no ADMX at all,
        and neither does anything MDM-only. The caller falls back to the raw
        registry path.
        """
        if not value_name:
            return None
        try:
            return get_catalog().lookup(key, value_name, scope=scope_class)
        except OSError:
            logger.debug("ADMX lookup failed for %s\\%s", key, value_name,
                         exc_info=True)
            return None

    def _build_local_policy(self, root: QTreeWidgetItem,
                            pol: Optional[PolFile]) -> None:
        """This machine's own local GPO, read straight from `Registry.pol`.

        Shown as its own section even when the RSOP call succeeded, because
        it is a different source answering a different question: RSOP is what
        Windows computed as the winner, this is what is configured locally.
        Where they disagree, that disagreement is the interesting part.
        """
        if pol is None or not pol.exists:
            return

        node = self._bold(self._node(
            root, "Local Policy (Registry.pol)", str(len(pol.settings))))
        node.setToolTip(0, pol.path)

        if pol.error:
            self._paint(self._node(node, pol.error), "error")
            return
        if not pol.settings and not pol.values:
            self._node(node, "The file exists but configures nothing "
                             "(every setting is Not Configured).")
            return

        drifted = 0
        scope_class = "Machine" if pol.hive == "HKLM" else "User"
        for value in pol.values:
            path = "%s\\%s" % (pol.hive, value.full_path)

            # Windows ships the friendly names for its own policies in
            # PolicyDefinitions, so a raw key only has to be shown when
            # nothing defines it -- AppLocker and anything MDM-only, mostly.
            info = self._describe(value.key, value.value_name, scope_class)
            label = info.display_name if info is not None else path

            item = self._node(node, label, value.display())
            item.setToolTip(0, path)
            if value.directive:
                # A delete directive removes a value rather than setting one;
                # painting it like a setting would misread the policy.
                self._paint(item, "warning")
            if info is not None:
                self._node(item, "Where in gpedit", info.category_path)
                if info.supported_on:
                    self._node(item, "Supported on", info.supported_on)
                if info.explain_text:
                    explain = self._node(item, "Explain")
                    explain.setToolTip(1, info.explain_text)
                    explain.setText(1, " ".join(info.explain_text.split())[:400])
            self._node(item, "Key", "%s\\%s" % (pol.hive, value.key))
            if value.value_name:
                self._node(item, "Value name", value.value_name)
            self._node(item, "Type", value.type_name)

            # Configured is not the same as in effect: the answer comes from
            # reading the live registry back, not from the .pol file.
            state = self._drift_by_path.get(path)
            if state is None:
                continue
            if state.is_drift:
                drifted += 1
            self._paint(item, _DRIFT_COLOURS.get(state.state, "info"))
            effect = self._node(item, "In effect?", state.state)
            self._paint(effect, _DRIFT_COLOURS.get(state.state, "info"))
            self._node(effect, "Why", state.reason)
            if state.state == DIFFERENT:
                self._node(effect, "Policy expects", state.expected_display)
                self._node(effect, "Registry holds", state.live_display)

        if self._drift_by_path:
            node.setText(1, "%d (%s)" % (
                len(pol.settings),
                "%d not in effect" % drifted if drifted else "all in effect"))

    def _build_settings(self, root: QTreeWidgetItem,
                        settings: List[PolicySetting]) -> None:
        node = self._bold(self._node(root, "Settings", str(len(settings))))
        if not settings:
            self._node(
                node,
                "No configured settings were reported for this scope.")
            return

        # Categories arrive as gpedit-style paths
        # ("Windows Components/Windows Error Reporting"); nesting them the
        # same way is what makes a long list navigable.
        folders = {}

        def folder_for(category: str) -> QTreeWidgetItem:
            parent = node
            path = ""
            for part in [p for p in category.split("/") if p.strip()]:
                path = "%s/%s" % (path, part) if path else part
                if path not in folders:
                    folders[path] = self._node(parent, part)
                parent = folders[path]
            return parent

        for setting in settings:
            parent = folder_for(setting.category) if setting.category else node
            item = self._node(parent, setting.name, setting.value, setting.gpo)
            # Everything the named extraction did not use stays reachable
            # here, so an extension whose schema we did not anticipate is
            # still fully inspectable rather than silently thinned out.
            for key, value in setting.details:
                self._node(item, key, value)

        for path in sorted(folders):
            folders[path].setText(1, "%d" % folders[path].childCount())

    def _collapse_to_roots(self) -> None:
        self._tree.collapseAll()
        for i in range(self._tree.topLevelItemCount()):
            self._tree.topLevelItem(i).setExpanded(True)

    # ------------------------------------------------------------------
    # Filtering
    # ------------------------------------------------------------------

    def _apply_filter(self, text: str = "") -> None:
        needle = (text or "").strip().lower()
        matches = 0
        for i in range(self._tree.topLevelItemCount()):
            matches += self._filter_item(self._tree.topLevelItem(i), needle)

        if not needle:
            self._tree.collapseAll()
            for i in range(self._tree.topLevelItemCount()):
                root = self._tree.topLevelItem(i)
                root.setExpanded(True)
                for j in range(root.childCount()):
                    root.child(j).setExpanded(True)
            return

        self._status_lbl.setText("%d row(s) match %r" % (matches, needle))

    def _filter_item(self, item: QTreeWidgetItem, needle: str) -> int:
        """Show `item` if it or any descendant matches. Returns match count.

        A parent has to survive its children matching, or the tree would hide
        the path to every hit.
        """
        own = 0
        if needle:
            haystack = item.data(0, _ROLE_SEARCH) or ""
            own = 1 if needle in haystack else 0

        descendants = 0
        for i in range(item.childCount()):
            descendants += self._filter_item(item.child(i), needle)

        if not needle:
            item.setHidden(False)
            return 0

        visible = bool(own or descendants)
        item.setHidden(not visible)
        if descendants:
            item.setExpanded(True)
        return own + descendants

    # ------------------------------------------------------------------
    # External tools
    # ------------------------------------------------------------------

    def _open_console(self, console: str) -> None:
        path = mmc_console_path(console)
        if path is None:
            QMessageBox.information(
                self._outer, console,
                "%s is not installed on this edition of Windows. It ships "
                "with Windows Pro, Enterprise and Education." % console)
            return
        try:
            # mmc.exe, not os.startfile: .msc is registered to MMC anyway, but
            # naming it means a broken file association cannot silently do
            # nothing.
            subprocess.Popen(["mmc.exe", path])
            self._status_lbl.setText("Opened %s." % console)
        except OSError as exc:
            self._error_banner.set_error("Could not open %s: %s" % (console, exc))

    def _save_snapshot(self) -> None:
        """Keep the report on screen so a later one can be diffed against it."""
        from PyQt6.QtWidgets import QInputDialog
        from modules.gpresult.rsop_snapshot import save_snapshot

        if self._result is None:
            self._status_lbl.setText("Nothing to snapshot yet.")
            return
        label, ok = QInputDialog.getText(
            self._outer, "Save snapshot",
            "Name this snapshot (optional):")
        if not ok:
            return
        try:
            meta = save_snapshot(self._result, label=label.strip())
        except OSError as exc:
            logger.warning("Could not save the snapshot", exc_info=True)
            self._error_banner.set_error("Could not save the snapshot: %s" % exc)
            return
        self._status_lbl.setText(
            "Saved snapshot %r." % (meta.label or meta.snapshot_id))

    def _open_compare(self) -> None:
        from modules.gpresult.snapshot_dialog import SnapshotCompareDialog

        SnapshotCompareDialog(self._result, self._outer).exec()

    def _open_gpupdate(self) -> None:
        """Reapply policy, then reload the report if anything actually ran.

        Imported here rather than at module scope so the pane does not pull a
        dialog in at startup for a button most sessions never press.
        """
        from modules.gpresult.gpupdate_dialog import GpupdateDialog
        from modules.gpresult.gpupdate import STATUS_FAILURE, STATUS_CANCELLED

        dialog = GpupdateDialog(self._outer, thread_pool=self.thread_pool)
        dialog.exec()

        outcome = dialog.result_obj
        if outcome is None or outcome.status in (STATUS_FAILURE, STATUS_CANCELLED):
            return
        # Policy just changed underneath the report on screen, so what is
        # displayed is now stale by definition.
        self._status_lbl.setText("%s Reloading the report..." % outcome.summary)
        self._do_refresh()

    def _export_html(self) -> None:
        path, _ = QFileDialog.getSaveFileName(
            self._outer, "Export Group Policy report",
            "gpresult.html", "HTML (*.html)")
        if not path:
            return
        self._progress.show()
        self._status_lbl.setText("Writing HTML report...")

        def work(_worker):
            return export_html_report(path)

        def on_done(outcome):
            ok, message = outcome
            self._progress.hide()
            if not ok:
                self._status_lbl.setText("Export failed.")
                self._error_banner.set_error(
                    "gpresult would not write the report: %s" % message)
                return
            self._status_lbl.setText("HTML report written.")
            try:
                os.startfile(path)  # noqa: S606 - opening the user's own file
            except OSError:
                logger.debug("Could not open %s", path, exc_info=True)

        worker = Worker(work)
        worker.signals.result.connect(on_done)
        worker.signals.error.connect(self._on_error)
        self._workers.append(worker)
        self.thread_pool.start(worker)

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def on_start(self, app=None) -> None:
        self.app = app

    def on_activate(self) -> None:
        if not self._loaded_once and not self._busy:
            self._do_refresh()

    def on_deactivate(self) -> None:
        self.cancel_all_workers()

    def on_stop(self) -> None:
        self.cancel_all_workers()

    def refresh_data(self) -> None:
        self._do_refresh()

    def get_refresh_interval(self) -> Optional[int]:
        """No timer.

        Resultant Set of Policy is a snapshot the user asks for; Windows
        itself only refreshes policy every 90 minutes. The old 120-second
        timer re-ran a multi-second subprocess forever for a report that had
        not changed, which is the same shape as the Security Dashboard's
        auto-refresh relaunching its own unfinished sweep.
        """
        return None
