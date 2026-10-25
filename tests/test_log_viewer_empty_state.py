from modules.log_viewer.log_viewer_module import LogViewerWidget


def test_empty_state_offers_open_drop_and_recent_files(qapp, tmp_path):
    w = LogViewerWidget()
    assert w._content_stack.currentWidget() is w._empty
    assert "drop" in w._empty.hint.lower()
    path = tmp_path / "example.log"
    path.write_text("One record\n", encoding="utf-8")
    w._recent = [str(path)]
    w._refresh_empty_recent()
    assert w._empty_recent.count() == 1
    w._empty_recent.itemActivated.emit(w._empty_recent.item(0))
    assert w.model.total == 1
    assert w._content_stack.currentWidget() is w.splitter
    w.stop()


def test_empty_folder_keeps_the_open_action_and_reason(qapp, tmp_path):
    w = LogViewerWidget()
    w.open_folder(str(tmp_path))
    assert w._content_stack.currentWidget() is w._empty
    assert "No logs" in w._empty.hint_label.text()
    assert w._empty.action_button.isEnabled()
    w.stop()
