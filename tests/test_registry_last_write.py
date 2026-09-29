"""RegQueryInfoKey's lpftLastWriteTime for the key currently browsed in the
tree -- previously only ever computed inside registry_scan's search, never
shown for a key just clicked on. Real-machine check: HKLM\\SOFTWARE itself
was last written years before 1601+0 and refusing a key most users cannot
open (HKLM\\SECURITY) must report a reason, not crash or say "never"."""
import datetime
import winreg

from modules.registry_explorer.registry_model import RegistryTreeModel, _Node


def test_last_write_reads_a_real_key():
    node = _Node("SOFTWARE", winreg.HKEY_LOCAL_MACHINE, "SOFTWARE", None)
    when, why = node.last_write()
    assert why == ""
    assert isinstance(when, datetime.datetime)
    assert when.year >= 2009  # Windows 7 is the oldest this app targets


def test_last_write_reports_a_refusal_not_a_crash():
    node = _Node("nope", winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Does\Not\Exist\At\All", None)
    when, why = node.last_write()
    assert when is None
    assert why  # a reason, not a silent "unknown"


def test_model_delegates_to_the_node(qapp):
    model = RegistryTreeModel()
    # root row 0 is HKEY_LOCAL_MACHINE
    idx = model.index(0, 0)
    when, why = model.last_write_for(idx)
    assert why == ""
    assert isinstance(when, datetime.datetime)


def test_model_returns_nothing_for_an_invalid_index(qapp):
    model = RegistryTreeModel()
    from PyQt6.QtCore import QModelIndex
    when, why = model.last_write_for(QModelIndex())
    assert when is None and why == ""
