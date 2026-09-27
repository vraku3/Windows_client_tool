import logging
import subprocess
from typing import List, Dict, Optional

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QTableWidget,
    QHeaderView, QLabel, QProgressBar, QLineEdit,
    QComboBox, QMessageBox, QTabWidget, QGroupBox, QFormLayout,
    QScrollArea, QTextEdit, QStackedWidget, QApplication,
    QListWidget, QListWidgetItem,
)
from PyQt6.QtCore import Qt, QThreadPool
from PyQt6.QtGui import QColor

from core.base_module import BaseModule
from core.module_groups import ModuleGroup
from core.run import run
from core.semantic_colors import semantic
from core.windows_utils import ps_quote
from core.table_ui import centered_item, center_header
from core.worker import COMWorker, Worker
from ui.error_banner import ErrorBanner
from modules.services_manager import service_audit
from modules.services_manager import service_view

CREATE_NO_WINDOW = 0x08000000


def _with_startup_flags(services: List[Dict]) -> List[Dict]:
    """Merge in DelayedAutostart / trigger-start (a registry read, not a WMI
    one) so the chips have them. A refused registry read leaves the flags
    simply absent rather than failing the whole service list -- the chips
    then read as "0 found", which is the honest answer for "could not check",
    same as any other read this pane could not do."""
    flags = service_audit.read_startup_flags()
    if flags is None:
        return services
    for svc in services:
        found = flags.get((svc.get("Name") or "").lower(), {})
        svc["DelayedAutostart"] = found.get("delayed", False)
        svc["TriggerStart"] = found.get("trigger", False)
    return services


# ----------------------------------------------------------------------
# Impact scoring
# ----------------------------------------------------------------------
_HIGH_IMPACT_NAMES = {
    "EventLog", "PlugPlay", "RpcSs", "RpcEptMapper", "RpcLocator",
    "DcomLaunch", "SamSs", "Lsa", "SecurityHealthService",
    "wscsvc", "WinDefend", "WdFilter", "WdNisDrv", "WdNisSvc",
    "MsMpEng", "NisSrv", "SgrmBroker", "CoreMessaging", "FontCache",
    "Dhcp", "Dnscache", "LanmanServer", "LanmanWorkstation",
    "Netman", "NlaSvc", "Tcpip", "NetBT", "Afd", "IpHelper",
    "Power", "ProfSvc", "UserMgr", "BFE", "MpsSvc", "PolicyAgent",
    "Netlogon", "KtmRm", "TrkWks", "SysMain", "Themes", "Winmgmt",
    "Audiosrv", "ShellServiceHost", "Schedule", "Spooler",
    "W32Time", "WSearch", "WERSvc", "Wecsvc", "WinRM",
}
_HIGH_IMPACT_KEYWORDS = [
    "system", "kernel", "security", "lsass", "smss", "csrss",
    "winlogon", "services", "services.exe",
]
_LOW_IMPACT_KEYWORDS = [
    "update", "updater", "google", "adobe", "onedrive", "dropbox",
    "box", "backup", "sync", "helper", "monitor", "tray", "daemon",
    "client", "cloud", "drive", "edge", "browser", "slack", "zoom",
    "teams", "discord", "spotify", "spotlight",
]
#: Status and impact both map onto the app's semantic palette. These are
#: functions rather than dicts because semantic() resolves against the
#: theme in force NOW — a module-level dict froze whichever theme happened
#: to be active when this module was first imported.
_STATUS_MEANING = {
    "Running": "success",
    "Stopped": "error",
    "Paused": "warning",
}
_IMPACT_MEANING = {
    "High": "error",
    "Medium": "warning",
    "Low": "success",
}


def status_colour(status: str):
    """The colour for a service state, or None for one we do not rank."""
    meaning = _STATUS_MEANING.get(status)
    return semantic(meaning) if meaning else None


def impact_colour(impact: str):
    """The colour for an impact rating, or None for one we do not rank."""
    meaning = _IMPACT_MEANING.get(impact)
    return semantic(meaning) if meaning else None

# ----------------------------------------------------------------------
# Service data fetchers
# ----------------------------------------------------------------------


def _score_impact(name: str, display_name: str, description: str) -> str:
    """Return 'High', 'Medium', or 'Low' based on service criticality."""
    dn = display_name.lower()
    d = description.lower()
    n = name.lower()

    if name in _HIGH_IMPACT_NAMES:
        return "High"
    for kw in _HIGH_IMPACT_KEYWORDS:
        if kw in d or kw in dn:
            return "High"
    for kw in _LOW_IMPACT_KEYWORDS:
        if kw in n or kw in dn:
            return "Low"
    return "Medium"


def get_services() -> List[Dict]:
    import wmi
    c = wmi.WMI()
    services = []
    for svc in c.Win32_Service():
        name = svc.Name or ""
        disp = svc.DisplayName or ""
        desc = getattr(svc, "Description", "") or ""
        services.append({
            "Name": name,
            "Display Name": disp,
            "Status": svc.State or "",
            "Start Type": svc.StartMode or "",
            "PID": str(svc.ProcessId) if svc.ProcessId else "",
            "Description": desc,
            "Impact": _score_impact(name, disp, desc),
            "Path": getattr(svc, "PathName", "") or "",
            "ServiceType": getattr(svc, "ServiceType", "") or "",
            "StartName": getattr(svc, "StartName", "") or "",
        })
    return sorted(services, key=lambda s: s["Display Name"].lower())


def query_service_config(name: str) -> Dict:
    """Run 'sc.exe qc <name>' and parse the output into a dict."""
    # A config read: sc answers at once or the SCM is wedged, and a wedged
    # SCM must not hold this worker thread for the life of the process.
    result = run(["sc.exe", "qc", name], timeout=15)
    cfg = {
        "service_name": name,
        "display_name": "",
        "type": "",
        "start_type": "",
        "error_control": "",
        "binary_path": "",
        "load_order_group": "",
        "dependencies": [],
        "tag_id": "",
    }
    if result.returncode != 0:
        return cfg
    cfg.update(service_audit.parse_qc(result.stdout))
    return cfg


def query_required_by(name: str) -> List[Dict]:
    """Run 'sc.exe enumdepend <name>' and return dependent services."""
    result = subprocess.run(
        ["sc.exe", "enumdepend", name, "pipe=out"],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        creationflags=CREATE_NO_WINDOW, timeout=30,
    )
    dependents = []
    current = {}
    for line in result.stdout.splitlines():
        line = line.strip()
        if line.startswith("SERVICE_NAME"):
            if current:
                dependents.append(current)
            current = {"name": line.split("=", 1)[1].strip().strip('"'), "display": ""}
        elif line.startswith("DISPLAY_NAME") and current:
            current["display"] = line.split("=", 1)[1].strip().strip('"')
    if current:
        dependents.append(current)
    return dependents


def service_action(name: str, action: str, check_dependents: bool = False) -> tuple[bool, List[Dict]]:
    """Perform a service action. Returns (ok, list_of_running_dependents])."""
    import win32serviceutil

    running_dependents: List[Dict] = []

    if action == "stop":
        # Pre-check: look for running dependent services
        dependents = query_required_by(name)
        running_dependents = [
            d for d in dependents
            if d.get("name") and _is_service_running(d["name"])
        ]

    if action == "start":
        win32serviceutil.StartService(name)
    elif action == "stop":
        win32serviceutil.StopService(name)
    elif action == "restart":
        win32serviceutil.RestartService(name)
    elif action == "enable":
        run(["powershell", "-NoProfile", "-NonInteractive", "-Command",
             f"Set-Service -Name '{ps_quote(name)}' -StartupType Automatic"],
            timeout=30, check=True)
    elif action == "disable":
        run(["powershell", "-NoProfile", "-NonInteractive", "-Command",
             f"Set-Service -Name '{ps_quote(name)}' -StartupType Disabled"],
            timeout=30, check=True)

    return True, running_dependents


def _is_service_running(name: str) -> bool:
    """Quick check whether a service is currently running."""
    result = run(["sc.exe", "query", name], timeout=15)
    return "RUNNING" in result.stdout.upper()


# ----------------------------------------------------------------------
# Group definitions for service filtering
# ----------------------------------------------------------------------
_NETWORK_KEYWORDS = [
    "dns", "dhcp", "network", "netman", "tcpip", "nla", "ip",
    "firewall", "bridge", "wan", "lan", "wlan", "wifi", "bluetooth",
    "remoteaccess", "ras", "vpn", "netbt", "afd", "ndis", "tunnel",
    "winhttp", "webclient", "iis", "w3svc", "was", "msmq", "cert",
    "smtp", "pop3", "imap", "ipp", "print", " spool", "upnp",
    "lltdio", "rspndr", "NetworkService", "NetworkProvider",
]
_SYSTEM_KEYWORDS = [
    "event", "log", "plug", "play", "power", "rpc", "lsass",
    "security", "audit", "dcom", "kernel", "smbs", "server",
    "workstation", "lanman", "user", "manager", "policy", "crypto",
    "crypt", "cert", "trust", "bitlocker", "secure", "boot",
    "Wdi", "diagnostic", "sysreset", "recovery", "ERSvc",
    "W32Time", "WinDefend", "SecurityHealthService", "wscsvc",
    "ProfSvc", "Themes", "ThemesService", "Shell", "ShellServiceHost",
]
_APPLICATION_KEYWORDS = [
    "update", "updater", "google", "adobe", "onedrive", "dropbox",
    "box", "backup", "sync", "helper", "monitor", "tray", "daemon",
    "client", "cloud", "drive", "edge", "browser", "slack", "zoom",
    "teams", "discord", "spotify", "spotlight", "ccm", "sms",
    "intel", "nvidia", "amd", "realtek", "qualcomm", "audio",
    "print", "spool", " fax", "faxservice",
]


def _service_group(name: str, display_name: str) -> str:
    """Return 'Network', 'System', 'Application', or 'Other'."""
    n = name.lower()
    d = display_name.lower()
    combined = f"{n} {d}"

    for kw in _NETWORK_KEYWORDS:
        if kw in combined:
            return "Network"
    for kw in _SYSTEM_KEYWORDS:
        if kw in combined:
            return "System"
    for kw in _APPLICATION_KEYWORDS:
        if kw in combined:
            return "Application"
    return "Other"


logger = logging.getLogger(__name__)

#: (key, label) for the "worth a second look" filter. Keys other than
#: unquoted/crashed are service_view's own.
_AUDIT_FILTERS = (
    ("any", "Any"),
    ("unquoted", "Unquoted path"),
    ("crashed", "Crashed (30 d)"),
    ("autostopped", "Auto, not running"),
    ("thirdparty", "Third-party"),
    ("account", "Custom account"),
    ("disabled", "Disabled"),
    ("delayed", "Delayed start"),
    ("triggerstart", "Trigger-start"),
)

# ----------------------------------------------------------------------
# Module
# ----------------------------------------------------------------------
class ServicesModule(BaseModule):
    name = "Services"
    icon = "⚙️"
    description = "View and control Windows services"
    requires_admin = True
    group = ModuleGroup.MANAGE

    def __init__(self):
        super().__init__()
        self._refreshing = False
        self._failures: Optional[Dict[str, int]] = None
        self._failures_requested = False

    def create_widget(self) -> QWidget:
        outer = QWidget()
        layout = QVBoxLayout(outer)
        layout.setContentsMargins(8, 8, 8, 8)

        self._error_banner = ErrorBanner()
        self._error_banner.hide()
        layout.addWidget(self._error_banner)

        # ---- Toolbar ----
        toolbar = QHBoxLayout()
        self._refresh_btn = QPushButton("Refresh")
        self._start_btn = QPushButton("Start")
        self._stop_btn = QPushButton("Stop")
        self._restart_btn = QPushButton("Restart")
        self._enable_btn = QPushButton("Enable")
        self._disable_btn = QPushButton("Disable")
        self._filter_edit = QLineEdit()
        self._filter_edit.setPlaceholderText("Filter by name...")
        self._filter_edit.setMaximumWidth(200)
        self._status_combo = QComboBox()
        self._status_combo.addItems(["All", "Running", "Stopped"])
        self._group_combo = QComboBox()
        self._group_combo.addItems(["All Groups", "Network", "System", "Application", "Other"])
        self._audit_combo = QComboBox()
        self._audit_combo.setToolTip("Show only services worth a second look")
        for key, label in _AUDIT_FILTERS:
            self._audit_combo.addItem(label, key)
        self._status_label = QLabel("Click Refresh to load.")
        for btn in (self._start_btn, self._stop_btn, self._restart_btn,
                    self._enable_btn, self._disable_btn):
            btn.setEnabled(False)
        for w in (self._refresh_btn, self._start_btn, self._stop_btn,
                  self._restart_btn, self._enable_btn, self._disable_btn):
            toolbar.addWidget(w)
        toolbar.addWidget(QLabel("Filter:"))
        toolbar.addWidget(self._filter_edit)
        toolbar.addWidget(self._status_combo)
        toolbar.addWidget(self._group_combo)
        toolbar.addWidget(self._audit_combo)
        toolbar.addStretch()
        toolbar.addWidget(self._status_label)
        layout.addLayout(toolbar)

        self._progress = QProgressBar()
        self._progress.setRange(0, 0)
        self._progress.setFixedHeight(4)
        self._progress.hide()
        layout.addWidget(self._progress)

        # ---- Tab widget: List | Details ----
        self._tabs = QTabWidget()
        self._list_tab = QWidget()
        self._detail_tab = QWidget()
        self._tabs.addTab(self._list_tab, "Service List")
        self._tabs.addTab(self._detail_tab, "Details")
        self._detail_tab.setEnabled(False)

        self._setup_list_tab()
        self._setup_detail_tab()

        layout.addWidget(self._tabs, 1)

        self._all_services: List[Dict] = []
        self._outer = outer

        self._refresh_btn.clicked.connect(self._do_refresh)
        self._filter_edit.textChanged.connect(self._apply_filter)
        self._status_combo.currentTextChanged.connect(self._apply_filter)
        self._group_combo.currentTextChanged.connect(self._apply_filter)
        self._audit_combo.currentIndexChanged.connect(self._apply_filter)
        self._table.itemSelectionChanged.connect(self._on_selection_changed)
        self._table.itemDoubleClicked.connect(self._on_double_click)
        self._start_btn.clicked.connect(lambda: self._do_action("start"))
        self._stop_btn.clicked.connect(lambda: self._do_action("stop"))
        self._restart_btn.clicked.connect(lambda: self._do_action("restart"))
        self._enable_btn.clicked.connect(lambda: self._do_action("enable"))
        self._disable_btn.clicked.connect(lambda: self._do_action("disable"))

        return outer

    def _setup_list_tab(self):
        """Build the service list table inside the list tab."""
        layout = QVBoxLayout(self._list_tab)
        layout.setContentsMargins(0, 4, 0, 0)

        # Table stacked with empty state
        self._table_stack = QStackedWidget()
        cols = ["Display Name", "Name", "Status", "Start Type", "Impact"]
        self._table = QTableWidget(0, len(cols))
        self._table.setHorizontalHeaderLabels(cols)
        self._table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        for i in range(1, len(cols)):
            self._table.horizontalHeader().setSectionResizeMode(
                i, QHeaderView.ResizeMode.ResizeToContents)
        center_header(self._table)
        self._table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self._table.setAlternatingRowColors(True)
        self._table_stack.addWidget(self._table)
        empty_lbl = QLabel("No services match filter")
        empty_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        empty_lbl.setStyleSheet("color: #888; font-size: 13px;")
        self._table_stack.addWidget(empty_lbl)
        layout.addWidget(self._table_stack)

    def _setup_detail_tab(self):
        """Build the service details panel inside the detail tab."""
        layout = QVBoxLayout(self._detail_tab)
        layout.setContentsMargins(0, 4, 0, 0)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        content = QWidget()
        self._detail_layout = QVBoxLayout(content)
        self._detail_layout.setSpacing(10)
        scroll.setWidget(content)
        layout.addWidget(scroll)

        # Summary section (always shown once a service is selected)
        self._detail_name_label = QLabel("Select a service to view details.")
        self._detail_name_label.setStyleSheet("font-size: 15px; font-weight: bold;")
        self._detail_layout.addWidget(self._detail_name_label)

        self._detail_info_group = QGroupBox("General Information")
        info_layout = QFormLayout(self._detail_info_group)
        info_layout.setSpacing(6)
        self._detail_type_value = QLabel("-")
        self._detail_start_type_value = QLabel("-")
        self._detail_error_value = QLabel("-")
        self._detail_account_value = QLabel("-")
        self._detail_path_value = QTextEdit()
        self._detail_path_value.setReadOnly(True)
        self._detail_path_value.setMaximumHeight(60)
        self._detail_path_value.setStyleSheet("background: transparent; border: none;")
        self._detail_load_group_value = QLabel("-")
        self._detail_name_value = QLabel("-")
        self._detail_disp_value = QLabel("-")
        info_layout.addRow("Service Name:", self._detail_name_value)
        info_layout.addRow("Display Name:", self._detail_disp_value)
        info_layout.addRow("Type:", self._detail_type_value)
        info_layout.addRow("Start Type:", self._detail_start_type_value)
        info_layout.addRow("Error Control:", self._detail_error_value)
        info_layout.addRow("Account:", self._detail_account_value)
        info_layout.addRow("Binary Path:", self._detail_path_value)
        info_layout.addRow("Load Order Group:", self._detail_load_group_value)
        self._detail_status_value = QLabel("-")
        self._detail_pid_value = QLabel("-")
        self._detail_impact_value = QLabel("-")
        self._detail_desc_value = QLabel("-")
        info_layout.addRow("Status:", self._detail_status_value)
        info_layout.addRow("PID:", self._detail_pid_value)
        info_layout.addRow("Impact:", self._detail_impact_value)
        info_layout.addRow("Description:", self._detail_desc_value)
        self._detail_layout.addWidget(self._detail_info_group)

        self._detail_audit_group = QGroupBox("Audit (security and reliability)")
        audit_layout = QVBoxLayout(self._detail_audit_group)
        self._detail_audit_value = QTextEdit()
        self._detail_audit_value.setReadOnly(True)
        self._detail_audit_value.setMaximumHeight(110)
        audit_layout.addWidget(self._detail_audit_value)
        self._detail_layout.addWidget(self._detail_audit_group)

        # Depends On section
        self._detail_deps_group = QGroupBox("Depends On (services this service requires)")
        deps_layout = QVBoxLayout(self._detail_deps_group)
        deps_layout.setSpacing(4)
        self._detail_deps_list = QListWidget()
        self._detail_deps_list.setMaximumHeight(100)
        self._detail_deps_list.itemDoubleClicked.connect(self._jump_to_dependency)
        deps_layout.addWidget(self._detail_deps_list)
        self._detail_layout.addWidget(self._detail_deps_group)

        # Required By section
        self._detail_rby_group = QGroupBox("Required By (services that depend on this)")
        rby_layout = QVBoxLayout(self._detail_rby_group)
        rby_layout.setSpacing(4)
        self._detail_rby_list = QListWidget()
        self._detail_rby_list.setMaximumHeight(100)
        self._detail_rby_list.itemDoubleClicked.connect(self._jump_to_dependency)
        rby_layout.addWidget(self._detail_rby_list)
        self._detail_layout.addWidget(self._detail_rby_group)

        # Action buttons
        btn_row = QHBoxLayout()
        btn_row.addStretch()
        self._detail_refresh_btn = QPushButton("Refresh Details")
        self._detail_copy_btn = QPushButton("Copy details")
        btn_row.addWidget(self._detail_copy_btn)
        btn_row.addWidget(self._detail_refresh_btn)
        self._detail_copy_btn.clicked.connect(self._copy_detail)
        self._detail_layout.addLayout(btn_row)

        self._detail_refresh_btn.clicked.connect(self._refresh_detail)

        self._detail_layout.addStretch()

    # ------------------------------------------------------------------
    # Public refresh (wired to toolbar button)
    # ------------------------------------------------------------------
    def _do_refresh(self):
        self._refresh_btn.setEnabled(False)
        self._status_label.setText("Loading...")
        self._progress.show()
        worker = COMWorker(lambda _w: _with_startup_flags(get_services()))
        worker.signals.result.connect(self._on_result)
        worker.signals.error.connect(self._on_error)
        self._workers.append(worker)
        QThreadPool.globalInstance().start(worker)

    # ------------------------------------------------------------------
    # Filtering
    # ------------------------------------------------------------------
    def _apply_filter(self):
        text = self._filter_edit.text().lower()
        sf = self._status_combo.currentText()
        group = self._group_combo.currentText()
        audit = self._audit_combo.currentData()
        rows = [
            s for s in self._all_services
            if service_view.matches(s, text)
            and self._passes_audit(s, audit)
            and (sf == "All" or s["Status"] == sf)
            and (group == "All Groups" or _service_group(s["Name"], s["Display Name"]) == group)
        ]
        self._table.setRowCount(len(rows))
        for r, svc in enumerate(rows):
            values = [
                svc["Display Name"], svc["Name"], svc["Status"],
                svc["Start Type"], svc["Impact"],
            ]
            for c, val in enumerate(values):
                item = centered_item(val)
                if c == 2:  # Status
                    color = status_colour(svc["Status"])
                    if color:
                        item.setForeground(QColor(color))
                elif c == 4:  # Impact
                    color = impact_colour(svc["Impact"])
                    if color:
                        item.setForeground(QColor(color))
                item.setData(Qt.ItemDataRole.UserRole, svc["Name"])
                self._table.setItem(r, c, item)
        self._table_stack.setCurrentIndex(0 if rows else 1)
        self._status_label.setText(f"{len(rows)} / {len(self._all_services)} service(s)")

    def _passes_audit(self, svc: Dict, key: str) -> bool:
        if key in (None, "any"):
            return True
        if key == "unquoted":
            return service_audit.is_unquoted_path(svc.get("Path", ""))
        if key == "crashed":
            return bool(service_audit.failures_for(self._failures, svc.get("Display Name", "")))
        return service_view.passes(key, svc)

    def _on_selection_changed(self):
        has = bool(self._table.selectedItems())
        for btn in (self._start_btn, self._stop_btn, self._restart_btn,
                    self._enable_btn, self._disable_btn):
            btn.setEnabled(has)
        if has:
            self._load_detail()

    def _on_double_click(self):
        self._tabs.setCurrentWidget(self._detail_tab)

    def _get_selected_name(self) -> Optional[str]:
        items = self._table.selectedItems()
        return items[0].data(Qt.ItemDataRole.UserRole) if items else None

    def _get_selected_service(self) -> Optional[Dict]:
        name = self._get_selected_name()
        for s in self._all_services:
            if s["Name"] == name:
                return s
        return None

    # ------------------------------------------------------------------
    # Detail panel
    # ------------------------------------------------------------------
    def _load_detail(self):
        svc = self._get_selected_service()
        if not svc:
            return
        self._detail_tab.setEnabled(True)

        # Basic fields from WMI data
        self._detail_name_label.setText(svc["Display Name"])
        self._detail_name_value.setText(svc["Name"])
        self._detail_disp_value.setText(svc["Display Name"])
        self._detail_status_value.setText(svc["Status"])
        self._detail_pid_value.setText(svc["PID"])
        self._detail_desc_value.setText(svc.get("Description", ""))
        self._detail_desc_value.setWordWrap(True)
        self._detail_impact_value.setText(svc.get("Impact", "-"))
        imp_color = impact_colour(svc.get("Impact", ""))
        if imp_color:
            self._detail_impact_value.setStyleSheet(f"color: {imp_color}; font-weight: bold;")
        else:
            self._detail_impact_value.setStyleSheet("")

        # Fetch full config via sc.exe qc in a worker
        self._detail_type_value.setText("Loading...")
        self._detail_start_type_value.setText("Loading...")
        self._detail_error_value.setText("Loading...")
        self._detail_account_value.setText(svc.get("StartName") or "-")
        self._detail_path_value.setPlainText("Loading...")
        self._detail_audit_value.setPlainText("Loading...")
        self._detail_load_group_value.setText("Loading...")
        self._detail_deps_list.clear()
        self._detail_deps_list.addItem("Loading dependencies...")
        self._detail_rby_list.clear()
        self._detail_rby_list.addItem("Loading required-by...")

        worker = Worker(lambda _w: self._fetch_detail(svc["Name"]))
        worker.signals.result.connect(self._apply_detail)
        worker.signals.error.connect(lambda e: self._apply_detail_error(str(e)))
        self._workers.append(worker)
        QThreadPool.globalInstance().start(worker)

    def _fetch_detail(self, name: str) -> Dict:
        cfg = query_service_config(name)
        req_by = query_required_by(name)
        return {"config": cfg, "required_by": req_by,
                "recovery": service_audit.read_recovery(name)}

    def _apply_detail(self, data: Dict):
        cfg = data["config"]
        req_by = data["required_by"]

        self._detail_type_value.setText(cfg.get("type", "-"))
        self._detail_start_type_value.setText(cfg.get("start_type", "-"))
        self._detail_error_value.setText(cfg.get("error_control", "-"))
        self._detail_path_value.setPlainText(cfg.get("binary_path", "-"))
        self._detail_load_group_value.setText(cfg.get("load_order_group") or "(none)")

        self._apply_audit(data.get("recovery"))
        deps = cfg.get("dependencies", [])
        self._fill_service_link_list(self._detail_deps_list, [(d, "") for d in deps],
                                     "(no dependencies)")
        self._fill_service_link_list(
            self._detail_rby_list, [(d["name"], d["display"]) for d in req_by],
            "(no dependent services)")

    def _fill_service_link_list(self, widget: QListWidget, rows, empty_text: str) -> None:
        """Each row is (service name, display name). Double-click jumps to it,
        if it is a service this pane actually has (a dependency can be a driver
        or a group, which never appear in the service table at all)."""
        widget.clear()
        if not rows:
            widget.addItem(empty_text)
            return
        known = {s["Name"].lower() for s in self._all_services}
        em_dash = chr(8212)
        for name, display in rows:
            label = f"{name}  {em_dash}  {display}" if display else name
            item = QListWidgetItem(label)
            if name.lower() in known:
                item.setData(Qt.ItemDataRole.UserRole, name)
                item.setToolTip("Double-click to jump to this service")
            else:
                item.setToolTip("Not a service in this list (a driver, group, or "
                                "one this session could not enumerate)")
            widget.addItem(item)

    def _jump_to_dependency(self, item: QListWidgetItem) -> None:
        name = item.data(Qt.ItemDataRole.UserRole)
        if name:
            self._select_service_by_name(name)

    def _select_service_by_name(self, name: str) -> None:
        for row in range(self._table.rowCount()):
            cell = self._table.item(row, 1)      # Name column
            if cell and cell.data(Qt.ItemDataRole.UserRole) == name:
                self._table.setCurrentCell(row, 0)
                self._table.scrollToItem(cell)
                self._tabs.setCurrentIndex(0)
                return
        self._status_label.setText(f"'{name}' is not in the current filter/search.")

    def _apply_audit(self, recovery: Optional[Dict]) -> None:
        svc = self._get_selected_service() or {}
        failures = service_audit.failures_for(self._failures, svc.get("Display Name", ""))
        lines = [f"[{sev}] {text}" for sev, text in service_audit.audit_lines(svc, failures)]
        if failures is None:
            lines.append("Failure history: could not be read (System log).")
        elif not failures:
            lines.append("Failure history: no crash events in the last 30 days.")
        lines.append("Recovery: " + service_audit.describe_recovery(recovery))
        self._detail_audit_value.setPlainText("\n".join(lines))

    def _copy_detail(self) -> None:
        svc = self._get_selected_service()
        if not svc:
            return
        text = service_view.detail_text(svc) + "\n" + self._detail_audit_value.toPlainText()
        QApplication.clipboard().setText(text)
        self._status_label.setText(f"Copied details of {svc['Name']}")

    def _load_failures(self) -> None:
        worker = Worker(lambda _w: service_audit.read_failure_counts())
        worker.signals.result.connect(self._on_failures)
        worker.signals.error.connect(lambda e: logger.warning("failure history: %s", e))
        self._workers.append(worker)
        QThreadPool.globalInstance().start(worker)

    def _on_failures(self, counts) -> None:
        self._failures = counts
        if hasattr(self, "_audit_combo"):
            self._apply_filter()

    def _apply_detail_error(self, err: str):
        self._detail_type_value.setText("-")
        self._detail_start_type_value.setText("-")
        self._detail_error_value.setText(f"Error: {err}")
        self._detail_deps_list.clear()
        self._detail_deps_list.addItem(f"Error loading dependencies: {err}")

    def _refresh_detail(self):
        self._load_detail()

    # ------------------------------------------------------------------
    # Actions
    # ------------------------------------------------------------------
    def _do_action(self, action: str):
        name = self._get_selected_name()
        if not name:
            return

        # Pre-stop dependency check
        if action == "stop":
            worker = Worker(lambda _w: service_action(name, action, check_dependents=True))
            worker.signals.result.connect(self._on_action_result)
            worker.signals.error.connect(lambda e: self._on_action_error(e, action, name))
            self._refresh_btn.setEnabled(False)
            self._status_label.setText(f"Checking dependencies for '{name}'...")
            self._workers.append(worker)
            QThreadPool.globalInstance().start(worker)
            return

        self._refresh_btn.setEnabled(False)
        self._status_label.setText(f"{action.capitalize()}ing {name}...")

        def _on_err(e):
            self._error_banner.set_error(f"Failed to {action} '{name}': {e}")
            self._do_refresh()

        worker = Worker(lambda _w: service_action(name, action))
        worker.signals.result.connect(lambda _: self._do_refresh())
        worker.signals.error.connect(_on_err)
        self._workers.append(worker)
        QThreadPool.globalInstance().start(worker)

    def _on_action_result(self, result: tuple):
        _ok, running_deps = result
        name = self._get_selected_name()
        svc = self._get_selected_service()
        display_name = svc["Display Name"] if svc else name

        if running_deps:
            dep_names = "\n".join(f"  - {d['name']}  ({d.get('display', '')})" for d in running_deps)
            reply = QMessageBox.warning(
                self._outer, "Service Has Dependencies",
                f"Stopping '{display_name}' will also stop these running dependent services:\n\n"
                f"{dep_names}\n\nDo you want to continue?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            )
            if reply != QMessageBox.StandardButton.Yes:
                self._refresh_btn.setEnabled(True)
                self._status_label.setText("Stop cancelled.")
                return
            # Proceed to stop in a new worker
            worker = Worker(lambda _w: service_action(name, "stop"))
            worker.signals.result.connect(lambda _: self._do_refresh())
            worker.signals.error.connect(
                lambda e: self._on_action_error(e, "stop", name)
            )
            self._workers.append(worker)
            QThreadPool.globalInstance().start(worker)
            return

        self._do_refresh()

    def _on_action_error(self, err: str, action: str, name: str):
        self._error_banner.set_error(f"Failed to {action} '{name}': {err}")
        self._do_refresh()

    def on_activate(self):
        if not getattr(self, "_loaded", False):
            self._loaded = True
            self._do_refresh()

    def on_start(self, app): self.app = app
    def on_stop(self): self.cancel_all_workers()
    def on_deactivate(self):
        self.cancel_all_workers()
        if self._failures is None:
            self._failures_requested = False

    def get_refresh_interval(self) -> Optional[int]:
        return 30_000

    def refresh_data(self) -> None:
        if not hasattr(self, "_refresh_btn"):
            # Now hostable as a composite child (System Management): the
            # host's auto-refresh timer can tick a tab whose widget was
            # never built because it has not been shown yet.
            return
        if self._refreshing:
            return
        self._refreshing = True
        self._do_refresh()

    def _on_result(self, services: List[Dict]):
        self._all_services = services
        if self._failures is None and not self._failures_requested:
            self._failures_requested = True
            self._load_failures()
        self._refresh_btn.setEnabled(True)
        self._progress.hide()
        self._apply_filter()
        self._status_label.setText(f"{len(services)} service(s) loaded")
        self._refreshing = False

    def _on_error(self, err: str):
        self._refresh_btn.setEnabled(True)
        self._progress.hide()
        self._status_label.setText(f"Error: {err}")
        self._refreshing = False
