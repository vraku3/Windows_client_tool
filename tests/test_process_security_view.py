"""The Security view in Process Explorer's lower pane and Properties dialog.

Pins that a refused token says so in words (never an empty table that
reads as "this process has no groups"), that enabled powerful privileges
lead the status line, and that the view is wired into the lower pane.
"""
import os

from core.procengine.mitigations import CFG, MitigationReport
from core.procengine.tokeninfo import (
    SE_PRIVILEGE_ENABLED, TokenGroup, TokenPrivilege, TokenReport,
)


def _view(qapp):
    from modules.process_explorer.lower_pane.security_view import SecurityView
    return SecurityView()


def test_refused_token_is_stated_not_blank(qapp):
    from modules.process_explorer.lower_pane.security_view import show
    view = _view(qapp)
    show(view, 4, TokenReport(pid=4, error="Access is denied (error 5)"),
         MitigationReport(pid=4, error="Access is denied"))
    assert "could not be read" in view._label.text()
    assert "Access is denied" in view._label.text()
    assert view._label.objectName() == "statusWarning"
    assert view._groups.rowCount() == 0


def test_enabled_powerful_privilege_leads(qapp):
    from modules.process_explorer.lower_pane.security_view import show
    view = _view(qapp)
    token = TokenReport(
        pid=3476, user="VRAKU\\iorda", user_sid="S-1-5-21-1", session=1,
        elevation_type="Full",
        groups=[TokenGroup("BUILTIN\\Administrators", "S-1-5-32-544", 0xF)],
        privileges=[TokenPrivilege("SeDebugPrivilege", SE_PRIVILEGE_ENABLED),
                    TokenPrivilege("SeShutdownPrivilege", 0)])
    show(view, 3476, token, MitigationReport(pid=3476, flags={CFG: 1},
                                             image_cfg=False))
    assert "SeDebugPrivilege" in view._label.text()
    assert view._facts["Elevation"].text() == "Full"
    assert view._facts["CFG"].text().startswith("Enabled for system DLLs")
    assert view._privileges.rowCount() == 2
    assert view._mitigations.item(0, 0).text() == "Control Flow Guard"


def test_real_read_of_our_own_process(qapp):
    from modules.process_explorer.lower_pane.security_view import (
        load_security, show)
    view = _view(qapp)
    token, mitigations = load_security(os.getpid())
    show(view, os.getpid(), token, mitigations)
    assert view._groups.rowCount() > 0
    assert view._privileges.rowCount() > 0
    assert view._facts["User"].text() not in ("", "—")
    assert view._facts["DEP"].text().startswith("Enabled")


def test_lower_pane_has_a_security_tab(qapp):
    from modules.process_explorer.process_explorer_module import (
        ProcessExplorerModule)
    module = ProcessExplorerModule()
    module.create_widget()
    tabs = [module._lower_tabs.tabText(i)
            for i in range(module._lower_tabs.count())]
    assert tabs[-1] == "Security"
    module._cancel_lower_pane()
