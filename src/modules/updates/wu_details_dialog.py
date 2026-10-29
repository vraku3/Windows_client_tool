"""One Windows Update's KB links, support URLs, notes and description."""
import html
import re

from PyQt6.QtWidgets import QDialog, QDialogButtonBox, QTextBrowser, QVBoxLayout


def _url(value: str) -> str:
    value = (value or "").strip()
    return html.escape(value, quote=True) if value.lower().startswith(("http://", "https://")) else ""


#: An MSRC bulletin ID such as MS17-010, or a CVE such as CVE-2026-1234.
_BULLETIN = re.compile(r"^MS\d{2}-\d{3}$", re.I)
_CVE = re.compile(r"^CVE-\d{4}-\d{4,}$", re.I)


def _security_link(value: str) -> str:
    """One published identifier as a link, or escaped text when unknown."""
    value = (value or "").strip()
    if _BULLETIN.match(value):
        year = f"20{value[2:4]}"
        url = (f"https://learn.microsoft.com/en-us/security-updates/securitybulletins/"
               f"{year}/{value.lower()}")
        return f'<a href="{url}">{html.escape(value)}</a>'
    if _CVE.match(value):
        url = f"https://nvd.nist.gov/vuln/detail/{value.upper()}"
        return f'<a href="{url}">{html.escape(value.upper())}</a>'
    return html.escape(value)


def render(update) -> str:
    """The details pane as HTML for one WindowsUpdate-like object."""
    def text(value):
        return html.escape(str(value or "—"))

    rows = [("Classification", getattr(update, "classification", "")),
            ("Severity", getattr(update, "severity", "")),
            ("Restart behaviour", getattr(update, "reboot", "")),
            ("Released", getattr(update, "release_date", ""))]
    if getattr(update, "driver", False):
        rows += [("Driver class", getattr(update, "driver_class", "")),
                 ("Driver manufacturer", getattr(update, "driver_manufacturer", ""))]
    parts = [f"<h2>{text(getattr(update, 'title', ''))}</h2>"]
    kb_ids = [str(k) for k in (getattr(update, "kb_ids", None) or [])]
    if not kb_ids:
        kb_ids = [str(getattr(update, "kb", "") or "").replace("KB", "")]
    links = [f'<a href="https://support.microsoft.com/help/KB{kb}">KB{html.escape(kb)}</a>'
             for kb in kb_ids if kb and kb != "N/A"]
    if links:
        parts.append("<p><b>Knowledge base:</b> " + ", ".join(links) + "</p>")
    parts.append("<p>" + "<br>".join(f"<b>{label}:</b> {text(value)}" for label, value in rows) + "</p>")
    support = _url(getattr(update, "support_url", ""))
    if support:
        parts.append(f'<p><b>Support:</b> <a href="{support}">{support}</a></p>')
    bulletins = [str(v) for v in (getattr(update, "security_bulletins", None) or [])
                 if str(v).strip()]
    cves = [str(v) for v in (getattr(update, "cve_ids", None) or []) if str(v).strip()]
    if bulletins:
        parts.append("<p><b>Security bulletins:</b> "
                     + ", ".join(_security_link(value) for value in bulletins) + "</p>")
    if cves:
        parts.append("<p><b>CVE:</b> "
                     + ", ".join(_security_link(value) for value in cves) + "</p>")
    more = [_url(url) for url in (getattr(update, "more_info_urls", None) or [])]
    more = [url for url in more if url]
    if more:
        parts.append("<p><b>More information:</b></p><ul>"
                     + "".join(f'<li><a href="{url}">{url}</a></li>' for url in more) + "</ul>")
    description = text(getattr(update, "description", ""))
    if description and description != "—":
        parts.append(f"<p>{description}</p>")
    if not (support or more or description):
        parts.append("<p>This update did not publish release notes, a support URL or more-information links.</p>")
    if not (bulletins or cves):
        parts.append("<p>Windows Update did not publish a security bulletin or CVE identifier "
                     "for this update.</p>")
    return "".join(parts)


class WindowsUpdateDetailsDialog(QDialog):
    def __init__(self, update, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Update details")
        self.resize(640, 480)
        layout = QVBoxLayout(self)
        browser = QTextBrowser(self)
        browser.setOpenExternalLinks(True)
        browser.setHtml(render(update))
        layout.addWidget(browser, 1)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)
        buttons.clicked.connect(self.accept)
        layout.addWidget(buttons)
