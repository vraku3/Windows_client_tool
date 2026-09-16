import subprocess
from typing import List, Dict, Optional

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QTableWidget,
    QHeaderView, QLabel, QProgressBar, QLineEdit,
    QComboBox, QMessageBox, QTabWidget, QGroupBox, QFormLayout,
    QScrollArea, QTextEdit, QStackedWidget,
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

CREATE_NO_WINDOW = 0x08000000

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

    # Parse key=value lines from sc output
    depends_lines = []
    capturing_deps = False
    for line in result.stdout.splitlines():
        line = line.strip()
        if line.startswith("DISPLAY_NAME"):
            cfg["display_name"] = line.split("=", 1)[1].strip().strip('"')
        elif line.startswith("TYPE"):
            cfg["type"] = line.split("=", 1)[1].strip()
        elif line.startswith("START_TYPE"):
            cfg["start_type"] = line.split("=", 1)[1].strip()
        elif line.startswith("ERROR_CONTROL"):
            cfg["error_control"] = line.split("=", 1)[1].strip()
        elif line.startswith("BINARY_PATH_NAME"):
            cfg["binary_path"] = line.split("=", 1)[1].strip().strip('"')
        elif line.startswith("LOAD_ORDER_GROUP"):
            cfg["load_order_group"] = line.split("=", 1)[1].strip().strip('"')
        elif line.startswith("TAG"):
            cfg["tag_id"] = line.split("=", 1)[1].strip()
        elif line.startswith("DEPENDENCIES"):
            raw = line.split("=", 1)[1].strip()
            capturing_deps = True
            depends_lines = [raw]
        elif capturing_deps:
            depends_lines.append(line)

    # Merge continuation lines and split by comma
    full_deps = " ".join(depends_lines)
    raw_deps = full_deps.strip()
    if raw_deps:
        deps = [d.strip().strip('"') for d in raw_deps.split("  ") if d.strip()]
        cfg["dependencies"] = deps

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

    def create_widget(self) -> QWidget:
        outer = QWidget()
        layout = QVBoxLayout(outer)
        layout.setContentsMargins(8, 8, 8, 8)

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

        # Depends On section
        self._detail_deps_group = QGroupBox("Depends On (services this service requires)")
        deps_layout = QVBoxLayout(self._detail_deps_group)
        deps_layout.setSpacing(4)
        self._detail_deps_list = QTextEdit()
        self._detail_deps_list.setReadOnly(True)
        self._detail_deps_list.setMaximumHeight(100)
        self._detail_deps_list.setStyleSheet(
            "background: transparent; border: 1px solid #444; border-radius: 3px;")
        deps_layout.addWidget(self._detail_deps_list)
        self._detail_layout.addWidget(self._detail_deps_group)

        # Required By section
        self._detail_rby_group = QGroupBox("Required By (services that depend on this)")
        rby_layout = QVBoxLayout(self._detail_rby_group)
        rby_layout.setSpacing(4)
        self._detail_rby_list = QTextEdit()
        self._detail_rby_list.setReadOnly(True)
        self._detail_rby_list.setMaximumHeight(100)
        self._detail_rby_list.setStyleSheet(
            "background: transparent; border: 1px solid #444; border-radius: 3px;")
        rby_layout.addWidget(self._detail_rby_list)
        self._detail_layout.addWidget(self._detail_rby_group)

        # Action buttons
        btn_row = QHBoxLayout()
        btn_row.addStretch()
        self._detail_refresh_btn = QPushButton("Refresh Details")
        btn_row.addWidget(self._detail_refresh_btn)
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
        worker = COMWorker(lambda _w: get_services())
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
        rows = [
            s for s in self._all_services
            if (not text or text in s["Display Name"].lower() or text in s["Name"].lower())
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
        self._detail_account_value.setText("Loading...")
        self._detail_path_value.setPlainText("Loading...")
        self._detail_load_group_value.setText("Loading...")
        self._detail_deps_list.setPlainText("Loading dependencies...")
        self._detail_rby_list.setPlainText("Loading required-by...")

        worker = Worker(lambda _w: self._fetch_detail(svc["Name"]))
        worker.signals.result.connect(self._apply_detail)
        worker.signals.error.connect(lambda e: self._apply_detail_error(str(e)))
        self._workers.append(worker)
        QThreadPool.globalInstance().start(worker)

    def _fetch_detail(self, name: str) -> Dict:
        cfg = query_service_config(name)
        req_by = query_required_by(name)
        return {"config": cfg, "required_by": req_by}

    def _apply_detail(self, data: Dict):
        cfg = data["config"]
        req_by = data["required_by"]

        self._detail_type_value.setText(cfg.get("type", "-"))
        self._detail_start_type_value.setText(cfg.get("start_type", "-"))
        self._detail_error_value.setText(cfg.get("error_control", "-"))
        self._detail_account_value.setText(cfg.get("start_name", "-"))
        self._detail_path_value.setPlainText(cfg.get("binary_path", "-"))
        self._detail_load_group_value.setText(cfg.get("load_order_group") or "(none)")

        deps = cfg.get("dependencies", [])
        if deps:
            self._detail_deps_list.setPlainText(
                "\n".join(f"  {d}" for d in deps)
            )
        else:
            self._detail_deps_list.setPlainText("  (no dependencies)")

        if req_by:
            lines = [f"  {d['name']}  —  {d['display']}" for d in req_by]
            self._detail_rby_list.setPlainText("\n".join(lines))
        else:
            self._detail_rby_list.setPlainText("  (no dependent services)")

    def _apply_detail_error(self, err: str):
        self._detail_type_value.setText("-")
        self._detail_start_type_value.setText("-")
        self._detail_error_value.setText(f"Error: {err}")
        self._detail_deps_list.setPlainText(f"Error loading dependencies:\n{err}")

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
            QMessageBox.warning(
                self._outer, "Error",
                f"Failed to {action} '{name}':\n{e}"
            )
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
        QMessageBox.warning(
            self._outer, "Error",
            f"Failed to {action} '{name}':\n{err}"
        )
        self._do_refresh()

    def on_activate(self):
        if not getattr(self, "_loaded", False):
            self._loaded = True
            self._do_refresh()

    def on_start(self, app): self.app = app
    def on_stop(self): self.cancel_all_workers()
    def on_deactivate(self): self.cancel_all_workers()

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
