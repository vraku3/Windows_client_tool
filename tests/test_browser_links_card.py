from types import SimpleNamespace
import threading
import time

from PyQt6.QtWidgets import QMessageBox

from modules.quick_fix import browser_links as links
from modules.quick_fix.browser_links_card import BrowserLinksCard


def state():
    return links.State({key: links.Value('ChromeHTML', 1) for key in ('http', 'https', '.html', '.htm')},
                       {key: links.Value() for key in links.NAMES}, links.Value(), None,
                       'BrowserChoice absent', {'Chrome': links.Browser('Chrome', 'ChromeHTML', 'ChromeHTML')})


def test_firefox_absent_disabled(qapp):
    card = BrowserLinksCard()
    card._render(state())
    assert card._buttons['Chrome'].isEnabled()
    assert not card._buttons['Firefox'].isEnabled()
    assert card._buttons['Firefox'].toolTip() == 'not installed'
    card.close()


def test_prepare_confirmation_defaults_cancel(qapp, monkeypatch):
    card = BrowserLinksCard()
    checked = []
    monkeypatch.setattr(QMessageBox, 'exec', lambda box: checked.append(box.defaultButton().text()) or QMessageBox.StandardButton.Cancel)
    card._active = worker = SimpleNamespace(is_cancelled=False)
    card._generation = 0
    card._on_result((worker, 0, 'prepare', (state(), 'Chrome', 'Exact planned changes'), ''))
    assert checked == ['Cancel']
    assert card._active is None
    card.close()


def test_late_result_ignored(qapp):
    card = BrowserLinksCard()
    card._status.setText('new run')
    card._active = object()
    card._on_result((object(), 0, 'refresh', state(), ''))
    assert card._status.text() == 'new run'
    card.close()


def test_io_runs_on_workers_and_confirmation_on_gui_thread(qapp, monkeypatch):
    main_thread = threading.get_ident()
    io_threads = []
    confirmations = []
    snapshot = state()

    def read():
        io_threads.append(threading.get_ident())
        return snapshot

    def write(**kwargs):
        io_threads.append(threading.get_ident())
        assert kwargs['expected'] == snapshot.office
        return 'Office preference verified'

    monkeypatch.setattr(links, 'read_state', read)
    monkeypatch.setattr(links, 'read_defaults', lambda: read().defaults)
    monkeypatch.setattr(links, 'set_office_default', write)
    monkeypatch.setattr(links, 'confirmation', lambda *args: 'planned changes')
    card = BrowserLinksCard()
    monkeypatch.setattr(card, '_confirm', lambda text: confirmations.append(threading.get_ident()) or True)

    def drain():
        deadline = time.monotonic() + 5
        while card._active is not None and time.monotonic() < deadline:
            qapp.processEvents()
            time.sleep(0.005)
        assert card._active is None

    card.activate()
    drain()
    card._buttons['Chrome'].click()
    drain()
    assert io_threads and all(ident != main_thread for ident in io_threads)
    assert confirmations == [main_thread]
    assert 'verified' in card._status.text()
    assert 'Windows default:' in card._details.text()
    card.cancel()
    card.close()


def test_card_is_lazy_and_searchable_in_quick_fix(qapp, monkeypatch):
    from modules.quick_fix import quick_fix_module as module
    from modules.quick_fix import quick_fix_history
    monkeypatch.setattr(module, 'ALL_ACTIONS', [])
    monkeypatch.setattr(module, 'is_reboot_pending', lambda: False)
    monkeypatch.setattr(quick_fix_history, 'last_by_action', lambda: {})
    monkeypatch.setattr(quick_fix_history, 'recurring_actions', lambda: {})
    mod = module.QuickFixModule()
    widget = mod.create_widget()
    assert len(mod._cards) == 1
    card = mod._cards[0]
    assert isinstance(card, BrowserLinksCard)
    assert not card._workers
    assert all(not button.isEnabled() for button in card._buttons.values())
    mod._apply_filter('firefox')
    assert not card.isHidden()
    mod._apply_filter('unrelated repair')
    assert card.isHidden()
    activations = []
    monkeypatch.setattr(card, 'activate', lambda: activations.append(True))
    mod.on_activate()
    assert activations == [True]
    mod.on_stop()
    widget.close()
