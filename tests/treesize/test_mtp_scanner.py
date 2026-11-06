"""Shell COM is external: fake its boundary, exercise the real store and walk."""
from datetime import datetime
from types import SimpleNamespace

import pytest

from modules.treesize.store.node_store import DIR, NodeStore
from modules.treesize.store.rollup import rollup

PATH = r"::{20D04FE0-3AEA-1069-A2D8-08002B30309D}\\?\usb#phone"


class Item:
    def __init__(self, name, children=None, path="", kind="", **metadata):
        self.Name, self.Path, self.Type = name, path, kind
        self.IsFolder = children is not None
        self.children, self.metadata = children, metadata

    @property
    def GetFolder(self):
        if isinstance(self.children, Exception):
            raise self.children
        if self.children is None:
            return None
        return Folder(self.children, self)

    def ExtendedProperty(self, key):
        value = self.metadata.get(key.removeprefix("System."))
        if isinstance(value, Exception):
            raise value
        return value


class Folder:
    def __init__(self, items, item=None):
        self.items, self.Self = items, item

    def Items(self):
        return iter(self.items)


@pytest.fixture
def mtp(monkeypatch):
    from modules.treesize.scan import mtp_scanner
    return mtp_scanner


def shell_for(monkeypatch, mtp, device):
    shell = SimpleNamespace(NameSpace=lambda key: (
        Folder([device]) if key == 17 else
        device.GetFolder if key == PATH else None))
    monkeypatch.setattr(mtp.win32com.client, "Dispatch", lambda _name: shell)


def store_root():
    store = NodeStore()
    return store, store.add(-1, "OnePlus 15", attrs=DIR)


def test_localized_usb_phone_is_one_device_with_combined_storages(mtp, monkeypatch):
    phone = Item("OnePlus 15", [
        Item("Internal", [], Capacity=1000, FreeSpace=200),
        Item("SD card", [], Capacity=500, FreeSpace=100),
    ], path=PATH, kind="Telefon mobil")
    shell_for(monkeypatch, mtp, phone)
    devices = mtp.list_mtp_devices()
    assert [(d.name, d.shell_path, d.capacity, d.free_space, d.used)
            for d in devices] == [("OnePlus 15", PATH, 1500, 300, 1200)]
    assert mtp.mtp_device_storage(PATH) == (1500, 300)


def test_discovery_failure_is_nonfatal_and_logged(mtp, monkeypatch, caplog):
    def fail(_name):
        raise OSError("Shell unavailable")
    monkeypatch.setattr(mtp.win32com.client, "Dispatch", fail)
    assert mtp.list_mtp_devices() == []
    assert "Shell unavailable" in caplog.text


def test_bfs_retains_each_storage_sizes_dates_and_exact_batches(mtp, monkeypatch):
    date = "09/09/2026 21:00:42"
    phone = Item("OnePlus 15", [
        Item("Internal", [Item("DCIM", [Item("photo.jpg", Size=123,
                                                       DateModified=date)])]),
        Item("SD", [Item("song.mp3", Size=77)]),
    ])
    shell_for(monkeypatch, mtp, phone)
    store, root = store_root()
    batches = []
    assert mtp.mtp_enumerate(PATH, store, root, on_batch=batches.append,
                             batch_size=2) == (6, 0, [])
    assert [store.name(i) for i in range(len(store))] == [
        "OnePlus 15", "Internal", "SD", "DCIM", "song.mp3", "photo.jpg"]
    assert list(store.parent) == [-1, 0, 0, 1, 2, 3]
    assert list(store.size) == [0, 0, 0, 0, 77, 123]
    assert store.mtime[5] == int((datetime(2026, 9, 9, 21, 0, 42).timestamp()
                                + 11644473600) * 10000000)
    assert batches == [(1, 3), (3, 5), (5, 6)]
    assert list(store.children(root)) == [1, 2]
    rollup(store)
    assert store.size[root] == store.alloc[root] == 200


def test_cancel_finishes_current_folder_without_descending(mtp, monkeypatch):
    phone = Item("Phone", [Item("Internal", [Item("later", Size=1)]),
                            Item("SD", [])])
    shell_for(monkeypatch, mtp, phone)
    store, root = store_root()
    batches = []
    mtp.mtp_enumerate(PATH, store, root, on_batch=batches.append,
                      should_cancel=lambda: len(store) > 1, batch_size=1)
    assert len(store) == 3
    assert batches == [(1, 2), (2, 3)]
    assert list(store.children(root)) == [1, 2]


def test_unreadable_folders_continue_and_cap_error_details(mtp, monkeypatch):
    phone = Item("Phone", [Item(str(i), OSError("disconnected"))
                            for i in range(105)] + [Item("ok", Size=9)])
    shell_for(monkeypatch, mtp, phone)
    store, root = store_root()
    count, error_count, errors = mtp.mtp_enumerate(PATH, store, root)
    assert count == 107 and error_count == 105 and len(errors) == 100
    assert errors[0] == ("OnePlus 15\\0", "disconnected")
    assert store.size[-1] == 9


def test_unknown_file_size_is_zero_and_marks_totals_incomplete(mtp, monkeypatch):
    phone = Item("Phone", [Item("unknown", DateModified="bad date"),
                            Item("refused", Size=OSError("size refused")),
                            Item("empty", Size=0)])
    shell_for(monkeypatch, mtp, phone)
    store, root = store_root()
    count, error_count, errors = mtp.mtp_enumerate(PATH, store, root)
    assert count == 4 and error_count == 2
    assert list(store.size) == [0, 0, 0, 0]
    assert list(store.mtime) == [0, 0, 0, 0]
    assert len(errors) == 2


def test_none_getfolder_is_an_error(mtp, monkeypatch):
    phone = Item("Phone", [Item("missing", [])])
    phone.children[0].children = None
    phone.children[0].IsFolder = True
    shell_for(monkeypatch, mtp, phone)
    store, root = store_root()
    _, error_count, errors = mtp.mtp_enumerate(PATH, store, root)
    assert error_count == 1 and "missing" in errors[0][0]


@pytest.mark.parametrize("value", ["09.09.2026 21:00:42",
    "2026-09-09 21:00:42", "09/09/2026 09:00:42 PM",
    datetime(2026, 9, 9, 21, 0, 42)])
def test_dates_support_localized_strings_and_com_datetime(mtp, monkeypatch, value):
    shell_for(monkeypatch, mtp, Item("Phone", [Item("f", Size=1, DateModified=value)]))
    store, root = store_root()
    mtp.mtp_enumerate(PATH, store, root)
    assert store.mtime[1] == int((datetime(2026, 9, 9, 21, 0, 42).timestamp()
                                + 11644473600) * 10000000)
