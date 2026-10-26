"""Apply the network help catalog without changing actions or data collection."""
from html import escape

from PyQt6.QtWidgets import (
    QAbstractButton, QLabel, QScrollArea, QTableWidget, QTabWidget,
    QVBoxLayout, QWidget,
)

from ui.module_intro import add_module_intro
from ui.help.network_text import (
    APIPA, BAND, BUTTON_HELP, CARDS, CONTROL_HELP, DNS, FIX_HELP, HEADERS,
    HOSTS, INTRO, MTU, QUICK_HELP, READ_ONLY, RDP, SMB, WIFI, WINRM, CONTEXT_HEADERS,
)


def control_help(widget, key):
    """Give unnamed input controls their specific explanation at construction."""
    widget.setToolTip(CONTROL_HELP[key])


def _label(text):
    return text[next((i for i, ch in enumerate(text) if ch.isalnum()), len(text)):].strip()


def _append_tip(widget, text):
    previous = widget.toolTip().strip()
    widget.setToolTip(previous + "\n" + text if previous and previous != text else text)


def _button_help(button, context):
    if button.text() == "×":
        return "Dismiss the error banner in this pane. Does not fix the reported issue or change Windows settings."
    label = _label(button.text())
    if context == "Quick Actions" and label in QUICK_HELP:
        return QUICK_HELP[label]
    for key, text in FIX_HELP.items():
        expected = {"flush_dns": "Flush DNS cache", "renew_dhcp": "Renew DHCP lease",
                    "reset_winsock": "Reset Winsock"}[key]
        if label.startswith(expected):
            return text
    if label == "Save":
        return BUTTON_HELP["Save hosts" if context == "hosts" else "Save proxy"]
    if label.startswith("Auto-refresh"):
        interval = " Scan repeats every 15 seconds." if context == "wifi" else " " + label + "."
        return BUTTON_HELP["Auto-refresh"] + interval
    if label in ("Refresh", "Scan", "Ping", "Trace", "Lookup", "Load Profiles", "Capture"):
        detail = CARDS.get(context, INTRO.get(context, READ_ONLY))
        return "Run this pane's read or probe again without a confirmation dialog. " + detail
    return BUTTON_HELP.get(label)


def _page_intro(page, text):
    add_module_intro(page, text)


def _owned_widgets(root, cls):
    """Leave nested tab pages and cards to their own contextual explanations."""
    for widget in root.findChildren(cls):
        parent = widget.parentWidget()
        while parent is not None and parent is not root:
            if isinstance(parent, QTabWidget) or parent.property("networkHelpCard"):
                break
            parent = parent.parentWidget()
        else:
            yield widget


def table_page(table, key):
    """An intro above a table used directly as a tab, preserving the table itself."""
    page = QWidget()
    layout = QVBoxLayout(page)
    add_module_intro(page, INTRO[key])
    layout.addWidget(table)
    return page


def explain(root, context, intro=True):
    """Describe built widgets, keeping existing explanations and disabled reasons."""
    text = CARDS.get(context, INTRO.get(context, READ_ONLY))
    if intro:
        _page_intro(root, text)
    for table in _owned_widgets(root, QTableWidget):
        for column in range(table.columnCount()):
            item = table.horizontalHeaderItem(column)
            if item is not None and not item.toolTip():
                item.setToolTip(CONTEXT_HEADERS.get((context, item.text()), HEADERS[item.text()]))
    for button in _owned_widgets(root, QAbstractButton):
        name = button.objectName()
        if name == "qt_tableview_cornerbutton":
            button.setToolTip("Select all rows in this table. Changes the selection only, not Windows settings.")
        elif name in ("ScrollLeftButton", "ScrollRightButton"):
            button.setToolTip("Scroll the tab strip to reveal more panes. Changes only the view.")
        elif name != "toolCardHeader":
            tip = _button_help(button, context)
            if tip:
                _append_tip(button, tip)
    for label in _owned_widgets(root, QLabel):
        if not label.toolTip():
            label.setToolTip(text)
    return root


def explain_tabs(tabs, context):
    """Explain each page in place; never remove or reinsert a live tab."""
    for index in range(tabs.count()):
        key = _label(tabs.tabText(index))
        text = INTRO.get(key, INTRO.get(context, READ_ONLY))
        previous = tabs.tabToolTip(index)
        tabs.setTabToolTip(index, previous + "\n" + text if previous else text)
        add_module_intro(tabs.widget(index), text, hosting_page=True)
        explain(tabs.widget(index), key, intro=False)
    return explain(tabs, context, intro=False)


def item_help(item, table, column):
    """Explain runtime rows from their own data; escape all machine-read text."""
    if item is not None and not item.toolTip():
        header = table.horizontalHeaderItem(column)
        meaning = header.toolTip() if header else "Value reported by this pane's data source."
        item.setToolTip("<qt>Reported value: " + escape(item.text()) + "<br>" + escape(meaning) + "</qt>")
    return item


def interface_property_help(name, value):
    """Meaning of property/value rows returned by netsh, without guessing values."""
    glossary = {"ssid": WIFI, "signal": WIFI, "channel": BAND, "band": BAND,
                "gateway": HEADERS["Gateway"], "dns": DNS, "mtu": MTU,
                "rdp": RDP, "winrm": WINRM, "smb": SMB, "hosts": HOSTS,
                "169.254": APIPA}
    term = next((text for key, text in glossary.items() if key in name.casefold()),
                "Wi-Fi interface property reported by netsh; this pane does not change it.")
    return "<qt>" + escape(name) + ": " + escape(value) + "<br>" + escape(term) + "</qt>"


def finding_help(finding):
    """Include each finding's own evidence and define the associated term."""
    term = APIPA if "apipa" in finding.id else MTU if "mtu" in finding.id else ""
    detail = "\n".join([finding.title, finding.detail, *finding.items, term])
    return "<qt>" + escape(detail).replace("\n", "<br>") + "</qt>"
