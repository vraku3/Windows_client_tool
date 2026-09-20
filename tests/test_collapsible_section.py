"""_CollapsibleSection: a header bar with a disclosure arrow over a body
that shows one child widget. Used by CleanupModule to replace its old
8-tab QTabWidget with one page of expand-on-demand sections (see
docs/superpowers/specs/2026-09-20-cleanup-single-tab-merge-design.md).
"""
from PyQt6.QtWidgets import QLabel


def _section(qapp):
    from modules.cleanup.collapsible_section import _CollapsibleSection
    body = QLabel("body content")
    section = _CollapsibleSection("System Junk", body)
    # isVisible() reflects the real on-screen state (ancestor chain
    # included), not just the explicit setVisible() flag -- show() the
    # top-level section itself so the body's own visibility is meaningful.
    section.show()
    return section, body


def test_starts_collapsed(qapp):
    section, body = _section(qapp)
    assert section.is_expanded() is False
    assert body.isVisible() is False


def test_set_expanded_true_shows_the_body_and_fires_expanded_once(qapp):
    section, body = _section(qapp)
    fired = []
    section.expanded.connect(lambda: fired.append(1))

    section.set_expanded(True)

    assert section.is_expanded() is True
    assert body.isVisible() is True
    assert fired == [1]


def test_expanding_an_already_expanded_section_does_not_refire(qapp):
    section, _body = _section(qapp)
    section.set_expanded(True)
    fired = []
    section.expanded.connect(lambda: fired.append(1))

    section.set_expanded(True)  # already expanded -- must be a no-op

    assert fired == []


def test_collapsing_does_not_fire_expanded(qapp):
    section, body = _section(qapp)
    section.set_expanded(True)
    fired = []
    section.expanded.connect(lambda: fired.append(1))

    section.set_expanded(False)

    assert section.is_expanded() is False
    assert body.isVisible() is False
    assert fired == []


def test_clicking_the_header_toggles_expansion(qapp):
    section, body = _section(qapp)
    section._header_btn.click()
    assert section.is_expanded() is True
    assert body.isVisible() is True
    section._header_btn.click()
    assert section.is_expanded() is False
    assert body.isVisible() is False


def test_set_summary_updates_the_header_label(qapp):
    section, _body = _section(qapp)
    section.set_summary("3 items, 42.1 MB")
    assert section._summary_lbl.text() == "3 items, 42.1 MB"
