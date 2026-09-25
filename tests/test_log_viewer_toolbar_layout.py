"""The Log Viewer toolbar row was built in a second layout set on the widget.

`_build_toolbar` shadowed its `layout` argument with a fresh `QVBoxLayout(self)`;
Qt ignores a second layout on a widget, so the first row was never laid out and
its widgets piled up at the top-left corner (seen 2026-09-25).
"""
from modules.log_viewer.log_viewer_module import LogViewerWidget


def test_toolbar_widgets_are_laid_out_side_by_side(qapp):
    w = LogViewerWidget()
    w.resize(1400, 700)
    w.show()
    qapp.processEvents()
    a, b = w.open_button.geometry(), w.follow.geometry()
    assert not a.intersects(b)
    assert b.left() >= a.right()
