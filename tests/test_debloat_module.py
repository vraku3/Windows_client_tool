"""DebloatToolsModule's tweaks tables: the five-value status vocabulary,
shown with a reason, is what this file pins down. Nothing here touches a
real machine -- TweakEngine.detect is monkeypatched throughout.
"""
from __future__ import annotations

from PyQt6.QtCore import Qt

from modules.debloat import debloat_module as dm
from modules.tweaks import tweak_engine as te


class _FakeBackup:
    def create_restore_point(self, label, module):
        return "rp-fake"

    def record_steps(self, *a, **k):
        pass


class _FakeApp:
    def __init__(self):
        self.backup = _FakeBackup()
        self.thread_pool = None


def _module():
    mod = dm.DebloatToolsModule()
    mod.on_start(_FakeApp())
    mod.create_widget()
    return mod


def _tweak(id_, status, reason):
    return {"id": id_, "name": f"Tweak {id_}", "category": "Privacy",
            "risk": "Low"}, te.DetectionResult(status=status, reason=reason)


def test_all_five_statuses_render_as_five_distinct_labels(monkeypatch):
    mod = _module()
    fakes = [
        _tweak("a", te.APPLIED, "the value matches"),
        _tweak("b", te.NOT_APPLIED, "the key does not exist"),
        _tweak("c", te.PARTIAL, "3 of 5 steps are in place"),
        _tweak("d", te.NOT_APPLICABLE, "this edition has no Group Policy"),
        _tweak("e", te.UNKNOWN, "access denied reading the key"),
    ]
    tweaks = [t for t, _ in fakes]
    results = {t["id"]: r for t, r in fakes}
    monkeypatch.setattr(mod, "_load_tweak_definitions", lambda tab: tweaks)
    monkeypatch.setattr(te.TweakEngine, "detect",
                        lambda self, tweak: results[tweak["id"]])

    mod._populate_tweaks_table("tweak")

    from PyQt6.QtWidgets import QTableWidget
    table = mod._widget.findChild(QTableWidget, "_table_tweak")

    labels = {table.item(r, 4).text() for r in range(table.rowCount())}
    assert labels == {
        "● Applied", "○ Not Applied", "◑ Partially Applied",
        "– Not Applicable", "❓ Unknown",
    }


def test_every_row_carries_its_reason_as_a_tooltip(monkeypatch):
    mod = _module()
    tweak, result = _tweak("a", te.PARTIAL, "3 of 5 steps are in place")
    monkeypatch.setattr(mod, "_load_tweak_definitions", lambda tab: [tweak])
    monkeypatch.setattr(te.TweakEngine, "detect", lambda self, t: result)

    mod._populate_tweaks_table("tweak")

    from PyQt6.QtWidgets import QTableWidget
    table = mod._widget.findChild(QTableWidget, "_table_tweak")
    assert table.item(0, 4).toolTip() == "3 of 5 steps are in place"


def test_a_bare_status_with_no_reason_still_shows_something(monkeypatch):
    """The bug this whole task exists to prevent: a status with nothing
    behind it. Never blank."""
    mod = _module()
    tweak, result = _tweak("a", te.UNKNOWN, "")
    monkeypatch.setattr(mod, "_load_tweak_definitions", lambda tab: [tweak])
    monkeypatch.setattr(te.TweakEngine, "detect", lambda self, t: result)

    mod._populate_tweaks_table("tweak")

    from PyQt6.QtWidgets import QTableWidget
    table = mod._widget.findChild(QTableWidget, "_table_tweak")
    assert table.item(0, 4).toolTip()  # non-empty
