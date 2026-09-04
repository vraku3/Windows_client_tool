"""Shared PyInstaller Analysis() inputs for all three .spec files
(WinClientTool.spec, WinClientTool-console.spec, WinClientTool-portable.spec).

Kept as plain data here instead of copy-pasted into each spec so the three
builds' datas/hiddenimports lists can't silently drift out of sync — the
folder/console/portable builds are meant to differ only in EXE/COLLECT
packaging options, not in what gets analyzed and bundled.

Each .spec file adds itself to sys.path (spec files aren't run as part of a
package) before importing this module — see any of the three for the pattern.
"""
import os


def get_main_script(project_root: str) -> str:
    return os.path.join(project_root, "src", "main.py")


def get_datas(project_root: str) -> list:
    return [
        (os.path.join(project_root, "config"), "config"),
        (os.path.join(project_root, "src", "ui", "styles"), "ui/styles"),
        (os.path.join(project_root, "src", "modules", "tweaks", "definitions"), "modules/tweaks/definitions"),
        # The Security Dashboard's baselines. JSON beside the catalog, loaded
        # by profile.py relative to its own __file__ -- so without this entry
        # the frozen build RUNS, the Baselines menu opens, and it says "No
        # baselines are installed". Same trap as the hidden imports below:
        # nothing looks broken, the feature is just quietly missing.
        (os.path.join(project_root, "src", "modules", "security_dashboard",
                      "catalog", "baselines"),
         "modules/security_dashboard/catalog/baselines"),
        # The cleanup scanner catalog: 461 of the ~540 scanners are DATA, not
        # code, and catalog.load_catalog() globs them from
        # `Path(__file__).parent / "definitions"`. Missing from this list
        # until 2026-09-03, so every frozen build since the catalog
        # conversion shipped with NONE of them -- the exe ran, the Cleanup
        # tabs opened, and they quietly offered only the ~80 hand-written
        # scanners. Third instance of the exact trap the two comments above
        # describe; tests/test_frozen_datas.py now walks the source for
        # __file__-relative data directories so there is not a fourth.
        (os.path.join(project_root, "src", "modules", "cleanup",
                      "cleanup_scanner", "definitions"),
         "modules/cleanup/cleanup_scanner/definitions"),
    ]


def read_version(project_root: str) -> str:
    """The application version, from the one file that holds it."""
    import re
    source = os.path.join(project_root, "src", "_version.py")
    with open(source, encoding="utf-8") as handle:
        match = re.search(r"__version__\s*=\s*[\"']([^\"']+)[\"']", handle.read())
    if not match:
        raise RuntimeError(f"no __version__ in {source}")
    return match.group(1)


def render_version_info(project_root: str) -> str:
    """version_info.txt.in with the real version filled in.

    Windows shows FileVersion/ProductVersion in the exe's Properties dialog
    and installers read them. Hand-maintaining that alongside
    src/_version.py meant three numbers agreeing by luck: the first release
    where one is bumped and the other forgotten ships an exe whose
    Properties disagree with its own About box.
    """
    version = read_version(project_root)
    template = os.path.join(project_root, "version_info.txt.in")
    with open(template, encoding="utf-8") as handle:
        text = handle.read()
    # Windows version resources are four-part; _version.py is three.
    return text.format(version=version,
                       version_tuple=", ".join(version.split(".") + ["0"]))


def write_version_info(project_root: str) -> str:
    """Render the resource next to the spec and return its path."""
    out = os.path.join(project_root, "version_info.txt")
    with open(out, "w", encoding="utf-8") as handle:
        handle.write(render_version_info(project_root))
    return out


HIDDEN_IMPORTS = [
    "PyQt6", "PyQt6.QtCore", "PyQt6.QtWidgets", "PyQt6.QtGui",
    "pywin32", "pywin32_bootstrap",
    "win32api", "win32con", "win32gui", "win32process", "win32service", "win32evtlog",
    "win32com", "win32com.client",
    # TreeSize. All of these are imported lazily, inside functions, and
    # win32com.shell is loaded dynamically by pywin32 -- PyInstaller finds
    # none of them by static analysis. Without them the frozen build still
    # RUNS, which is the trap: IFileOperation silently drops to the ctypes
    # fallback (no per-item errors), the remote targets report themselves
    # unavailable, owners come back blank, and the Excel and PDF exports
    # vanish from the menu. Nothing looks broken; things are just quietly
    # missing.
    "win32com.server", "win32com.server.util", "win32com.server.policy",
    "win32com.shell", "win32com.shell.shell", "win32com.shell.shellcon",
    "pythoncom", "pywintypes", "win32security",
    # The Dashboard's GPU panel. Imported inside GpuSampler._open(), so
    # static analysis never sees it, and the failure is the quiet kind
    # this list exists for: the panel opens, logs "win32pdh is
    # unavailable", and shows a GPU that is doing nothing at all.
    "win32pdh",
    # Process Explorer's System Information window, imported inside the
    # toolbar handler so the pane does not drag the performance and GPU
    # engines in at startup. Frozen without it, the button raises.
    "ui.system_information",
    # The menu bar's force-shutdown/restart/Safe-Mode buttons, imported
    # inside MainWindow._setup_menus() — every startup, not a lazy handler,
    # but still a function-scoped import this project's PyInstaller analysis
    # does not follow. Frozen without it, the app fails at MainWindow
    # construction, not quietly: this one is worth catching in review, not
    # discovering after a build.
    "ui.power_actions",
    # Composite children. A CompositeModule imports its children inside
    # __init__ so a host module's import does not drag four panes' worth of
    # Qt in at startup. main.py therefore names only the hosts, and these are
    # reachable only through those function-level imports. Listed explicitly
    # for the same reason as the TreeSize block above: if PyInstaller misses
    # one, the frozen build still runs and the tab is simply not there.
    "modules.store_apps.store_apps_module",
    "modules.boot_analyzer.boot_analyzer_module",
    "modules.power_boot.power_module",
    "modules.wifi_analyzer.wifi_module",
    "modules.hosts_editor.hosts_editor_module",
    "modules.network_extras.net_extras_module",
    "modules.event_viewer.event_viewer_module",
    "modules.cbs_log.cbs_module",
    "modules.dism_log.dism_module",
    "modules.windows_update.wu_module",
    "modules.reliability.reliability_module",
    "modules.crash_dumps.crash_dump_module",
    # Group Policy. The pane imports these four inside its button handlers,
    # so a pane that is only ever read does not drag two dialogs and the
    # snapshot machinery in with it. Same trap once more: without them the
    # frozen build opens the pane and reports policy perfectly well, and
    # Snapshot, Compare and Refresh Policy raise ImportError the moment
    # someone clicks them.
    "modules.gpresult.rsop_snapshot",
    "modules.gpresult.snapshot_dialog",
    "modules.gpresult.gpupdate",
    "modules.gpresult.gpupdate_dialog",
    # Log Viewer. The module imports these two dialogs inside button handlers,
    # so a log that is only being read does not drag the error lookup and
    # highlight editor in. Without them the frozen build opens the log and
    # filters it perfectly well, and Error Lookup and Highlight Rules raise
    # ImportError the moment someone clicks them.
    "modules.log_viewer.error_lookup_dialog",
    "modules.log_viewer.highlight_dialog",
    # Both reached only from the "Message colours..." button handler, so
    # the frozen build runs fine until someone clicks it.
    "modules.log_viewer.match_colour_dialog",
    "modules.log_viewer.match_colours",
    # Imported inside DashboardModule.__init__, so PyInstaller's static
    # analysis can miss them and the tabs would be silently absent.
    "modules.dashboard.details_module",
    "modules.dashboard.processes_module",
    "modules.dashboard.performance_module",
    "modules.dashboard.performance_tab",
    "ui.perf_graph",
    "core.procengine.cpuinfo",
    "core.procengine.meminfo",
    "modules.dashboard.processes_tab",
    "core.procengine.grouping",
    "modules.dashboard.details_tab",
    "modules.dashboard.details_model",
    "modules.dashboard.process_menu",
    "core.procengine.ntquery",
    "core.procengine.rates",
    "core.procengine.details",
    "core.procengine.snapshot",
    "core.procengine.columns",
    "core.procengine.actions",
    "core.procengine.signatures",
    "core.procengine.users",
    "modules.dashboard.users_tab",
    "modules.dashboard.users_module",
    "core.procengine.usage",
    "modules.dashboard.app_history_tab",
    "modules.dashboard.app_history_module",
    "modules.dashboard.startup_module",
    "modules.dashboard.startup_tab",
    "modules.dashboard.services_module",
    "modules.dashboard.services_tab",
    "modules.process_explorer.process_explorer_module",
    # PerfMon is a Dashboard tab (imported inside DashboardModule.__init__),
    # so PyInstaller's static analysis would otherwise drop it.
    "modules.perfmon.perfmon_module",
    "modules.perfmon.perfmon_collector",
    "modules.perfmon.perfmon_charts",
    "modules.perfmon.perfmon_alerts",
    "modules.perfmon.perfmon_search_provider",
    # The Security Dashboard's elevated helper. main.py imports it inside
    # main(), so that the ordinary GUI start does not pay for it, which means
    # PyInstaller cannot see it. The trap here is worse than a missing tab:
    # the frozen build launches its own exe with --apply-security-batch to get
    # a single UAC prompt, and without this the ELEVATED child is the process
    # that dies with ImportError — after the user has granted the prompt, in a
    # window they cannot read, having written no result file. The pane would
    # correctly report the outcome as unknown, forever.
    "modules.security_dashboard.elevated_helper",
    # Imported inside a function in all five of its consumers — Hardware
    # Info, Reliability, Security Dashboard, Services and System Report —
    # every one of them behind a `try:` that degrades silently. Same trap as
    # the TreeSize block above: miss it and the build still runs, with five
    # panes quietly empty and the Security Dashboard scoring a TPM it could
    # not read as absent.
    "wmi",
    "httpx", "paramiko",
    "openpyxl", "reportlab",
    "PIL", "PIL._imaging",
    "requests", "urllib3", "charset_normalizer", "idna",
]
