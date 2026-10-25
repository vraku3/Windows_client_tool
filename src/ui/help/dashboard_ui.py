"""Bind the Dashboard family's curated help without changing control behavior."""
from html import escape

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QAbstractButton, QComboBox, QGridLayout, QLabel, QLineEdit, QMenu,
    QSlider, QTableWidget, QTabWidget, QToolBar, QTreeWidget, QVBoxLayout,
    QWidget, QWidgetAction,
)

from ui.module_intro import add_module_intro
from ui.help.dashboard_text import CONTROLS, FILTERS, HEADERS, INTRO, TERMS


def _plain(text):
    return text.replace("&&", "&").replace("&", "").strip()


def action_help(text):
    """Resolve menu labels too, including labels containing a PID or column."""
    label = _plain(text)
    if label in CONTROLS:
        return CONTROLS[label]
    if label in HEADERS:
        return "Show or hide this column; only the view changes. " + HEADERS[label]
    if label.startswith("Copy"):
        return "Copy the selected information to the clipboard, replacing its previous text. This does not change Windows settings."
    if label.startswith("End process"):
        return CONTROLS["End process"]
    if label.startswith("End task"):
        return CONTROLS["End task"]
    if label.startswith("Go to process"):
        return "Request navigation to the service's owning process. This does not start, stop or modify the service."
    if label in ("Idle", "Below Normal", "Normal", "Above Normal", "High", "Realtime"):
        return (f"Set the selected process's scheduling priority to {label} immediately. "
                "Choose the previous level to change back; administrator rights may be needed. "
                "High and Realtime can starve other programs of CPU time.")
    if label in ("Start service", "Stop service", "Restart service"):
        return (f"{label} after confirmation, using Windows service control. Administrator permissions "
                "are required; dependent work can be interrupted and is not restored by changing back.")
    if label in ("Automatic", "Automatic (Delayed Start)", "Manual", "Disabled", "Startup type"):
        return ("Choose a service startup configuration; a change asks first and needs administrator "
                "permissions. It persists in Windows; choose the former type to change back.")
    return ""


def explain_menu(menu):
    """Called after dynamic menus are populated, before showing them."""
    for action in menu.actions():
        if action.isSeparator():
            continue
        text = action_help(action.text())
        if not text and _plain(action.toolTip()) != _plain(action.text()):
            text = action.toolTip()
        if isinstance(action, QWidgetAction) and action.defaultWidget() is not None:
            control = action.defaultWidget()
            text = control.toolTip() or _control_help(control, "Process Explorer")
            if isinstance(control, QLabel):
                text = "Process sampling interval, in seconds. Choose an interval to change monitoring frequency only."
        if text:
            if not action.statusTip():
                action.setStatusTip(text)
            # QAction invents a tooltip from its label when none was set.
            if not action.toolTip() or _plain(action.toolTip()) == _plain(action.text()):
                action.setToolTip(text)
        if action.menu() is not None:
            explain_menu(action.menu())


def _intro(root, pane):
    if pane in INTRO:
        add_module_intro(root, INTRO[pane])


def _combo_help(widget, pane):
    text = widget.currentText()
    if text.endswith("s") and text[:-1].replace(".", "", 1).isdigit():
        return "Choose the process sampling interval in seconds. This changes monitoring frequency only; shorter intervals increase this tool's overhead."
    if text.startswith("every "):
        return "Choose seconds between recorded samples. This affects new recording detail and file size, not the PC's workload."
    if text.endswith("x"):
        return "Choose recorded playback speed. This affects replay only, not recording frequency or process speed."
    if text.startswith("Last "):
        return "Choose how far back to read this app's stored performance history. Refresh reloads that range; Windows settings are unchanged."
    if pane == "Power & Freq":
        return "Select a Windows power plan to preview the choice. It is applied only by Switch plan after confirmation; the previous plan can be selected again."
    if pane == "Benchmarks":
        return "Choose the drive for the temporary disk benchmark file. Running tests writes and removes that file; choosing a drive alone changes nothing."
    return "Choose which rows or category this pane displays. This filters the view without changing Windows settings or running programs."


def _control_help(widget, pane):
    if isinstance(widget, QLineEdit):
        if pane == "Find Handle or DLL":
            return "Enter part of an object name or library path, then press Search. The read-only sweep can take a minute and cannot inspect every protected process."
        return ("Filter the displayed data using " + (widget.placeholderText().rstrip("….") or "the entered text")
                + ". This changes only the view; it does not execute the text or change Windows settings.")
    if isinstance(widget, QComboBox):
        return _combo_help(widget, pane)
    if isinstance(widget, QSlider):
        return "Move to a recorded sample in the trace. This changes playback position only and does not reproduce the workload."
    if isinstance(widget, QAbstractButton):
        label = widget.text()
        if label in FILTERS:
            return "Show only " + FILTERS[label] + ". This filters the view without changing Windows settings."
        if label == "Suspend" and pane in ("Processes", "Details"):
            return "Ask before suspending the selected processes. They stop responding until Resume; access to protected processes may need administrator rights."
        if not label:
            if isinstance(widget.parentWidget(), QLineEdit):
                return "Clear the entered filter text. This changes only the view."
            return "Scroll or open the toolbar's hidden controls. This changes the visible controls only, not Windows settings."
        return action_help(label)
    if isinstance(widget, QLabel):
        label = widget.text().replace("<b>", "").replace("</b>", "").rstrip(":")
        return TERMS.get(label, "")
    return ""


def _headers(widget, pane):
    if isinstance(widget, QTableWidget):
        items = [(i, widget.horizontalHeaderItem(i)) for i in range(widget.columnCount())]
    elif isinstance(widget, QTreeWidget):
        header = widget.headerItem()
        for i in range(widget.columnCount()):
            if not header.toolTip(i):
                header.setToolTip(i, HEADERS.get(header.text(i), ""))
        return
    else:
        return
    for column, item in items:
        if item is None or item.toolTip():
            continue
        text = HEADERS.get(item.text(), "")
        if pane == "Power & Freq" and item.text() == "CPU":
            text = "Logical processor number returned by Windows topology/power readers, not a CPU utilization percentage."
        elif pane == "Threads" and item.text() == "CPU%":
            text = "Thread CPU-time increase over a half-second sample, as a percentage of one logical processor. 100% means one processor fully busy, not the whole machine; new threads lack a baseline."
        elif pane == "Live Monitor" and item.text() == "CPU %":
            text = "Process CPU percentage from psutil's Windows process reader, where 100% means one logical processor. Multithreaded processes can exceed 100%; this differs from the machine-wide CPU bar."
        elif pane == "Installed Apps" and item.text() == "Size":
            text = "Estimated installed size supplied by the uninstall registry entry, formatted in powers of 1024. It may be absent or inaccurate; this pane does not scan the app's files."
        elif pane == "DLLs" and item.text() == "Size":
            text = "Loaded module image size reported by Windows module enumeration, formatted in powers of 1024. This is not necessarily the executable file size on disk."
        elif pane == "Memory Map" and item.text() == "Size":
            text = "Region size from psutil's memory-map provider when supplied, formatted in powers of 1024. The view shows a dash when the provider omits this field."
        elif pane == "Disk Space" and item.text() == "Size":
            text = "Drive total capacity or scanned folder logical size, formatted in powers of 1024. Scanned sizes exclude inaccessible files and need not equal physically allocated space."
        elif pane == "Services" and item.text() == "Status":
            text = "Current service state from Win32_Service, such as Running, Stopped or a pending transition. Stopped does not necessarily mean a fault."
        item.setToolTip(text)


def apply_help(root, pane):
    """Apply known definitions, preserving help already supplied by a pane."""
    _intro(root, pane)
    widgets = [root] + root.findChildren(QWidget)
    contexts = _tab_contexts(widgets, pane)
    _status_help(root)
    for widget in widgets:
        context = _context(widget, contexts, pane)
        if not widget.toolTip():
            text = _control_help(widget, context)
            if text:
                widget.setToolTip(text)
        _headers(widget, context)
        if isinstance(widget, (QMenu, QToolBar)):
            explain_menu(widget)
        if isinstance(widget, QTabWidget):
            for index in range(widget.count()):
                name = contexts[widget.widget(index)]
                if not widget.tabToolTip(index):
                    widget.setTabToolTip(index, INTRO.get(name, HEADERS.get(name, "")))
                _intro(widget.widget(index), name)
    # Toolbar buttons are Qt-created proxies for actions; share the action help.
    for button in root.findChildren(QAbstractButton):
        if button.toolTip() in ("Kill", "Suspend", "System Information", "Find Handle or DLL"):
            button.setToolTip(action_help(button.toolTip()))


def _tab_contexts(widgets, pane):
    contexts = {}
    for widget in widgets:
        if isinstance(widget, QTabWidget):
            for i in range(widget.count()):
                label = widget.tabText(i)
                name = next((key for key in sorted(INTRO, key=len, reverse=True)
                             if label.endswith(key) or label.endswith(key.replace("&", "&&"))), label)
                if pane == "Process properties" and label == "Performance":
                    name = "Process performance"
                contexts[widget.widget(i)] = name
    return contexts


def _context(widget, contexts, fallback):
    parent = widget
    while parent is not None:
        if parent in contexts:
            return contexts[parent]
        parent = parent.parentWidget()
    return fallback


def _status_help(root):
    for name, text in {
        "status": "Current read, scan or playback status for this pane. A refused or incomplete read is different from a measured zero or no matches.",
        "_attention_status": "Outcome of the Overview checks. Unknown means the app could not read enough information; findings are not fixed automatically.",
        "_topo_lbl": "CPU topology reported by Windows: physical cores, logical processors and memory-access nodes are different counts.",
        "_freq_lbl": "Frequency readings in MHz. Nominal is the base clock; effective frequency uses the Windows performance counter and may include boost.",
        "_new_label": "Programs first observed between recorded samples. This is a recording observation, not proof of the exact launch instant.",
        "_history_summary_label": "Minimum, average and maximum from the full stored sample range. Charts may be downsampled; the summary and CSV retain the stored readings.",
    }.items():
        label = getattr(root, name, None)
        if isinstance(label, QLabel) and not label.toolTip():
            label.setToolTip(text)


def row_help(table, row, fields):
    """Explain a row from its own facts, escaping all machine-supplied text."""
    text = "<br>".join(
        escape(f"{label}: {value}") for label, value in fields if value not in (None, "")
    )
    for column in range(table.columnCount()):
        item = table.item(row, column)
        if item is not None:
            existing = item.toolTip()
            prefix = escape(existing) + "<br>" if existing else ""
            item.setToolTip("<qt>" + prefix + text + "</qt>")
