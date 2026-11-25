"""Outlook mailbox scan target through MAPI (spec 6.2).

Read-only, deliberately: Pro reads a mailbox to show where the space went, and
a disk-usage tool has no business deleting mail. `supports_file_ops()` is
False, so the pane hides the destructive actions rather than showing them
disabled.

The mailbox is walked with the injected `client` -- a MAPI folder object --
which is what lets the folder/item tree be tested without Outlook installed.
Pro bundles Redemption64.dll for this; plain MAPI through pywin32 gets the
folder and item sizes, which is what the views need.
"""
from ..store.node_store import DIR
from .base import Credentials, ScanTarget, TargetError, register

#: Mail with no subject is ordinary -- calendar items, some receipts. It still
#: occupies space, so it gets a name rather than being skipped.
NO_SUBJECT = "(no subject)"


class OutlookTarget(ScanTarget):
    id = "outlook"
    display_name = "Outlook mailbox"
    icon = "✉"
    file_ops = False           # read-only, by design
    form_labels = {"host": "Mailbox (blank for the default)", "port": None,
                   "username": None, "password": None, "root": None}
    #: Nothing at all: the default mailbox on this machine needs no details.
    required_fields = ()

    def __init__(self, credentials: Credentials | None = None, client=None) -> None:
        super().__init__(credentials)
        self._client = client
        self.errors: list[tuple[str, str]] = []
        self.error_count = 0

    @classmethod
    def is_available(cls):
        try:
            __import__("win32com.client")
        except ImportError:
            return False, ("pywin32 is not installed, so Outlook cannot be "
                           "read. Install it with: pip install pywin32")
        return True, ""

    def authenticate(self) -> None:
        if self._client is not None:
            return
        ok, why = self.is_available()
        if not ok:
            raise TargetError(why)
        import ctypes
        import win32com.client

        try:
            # A worker thread has no apartment of its own; MAPI is COM, and
            # Dispatch on an uninitialised thread fails with a bare HRESULT.
            ctypes.windll.ole32.CoInitialize(None)
            namespace = win32com.client.Dispatch(
                "Outlook.Application").GetNamespace("MAPI")
            store_name = self.credentials.host
            folders = namespace.Folders
            if store_name:
                self._client = folders.Item(store_name)
            else:
                self._client = folders.Item(1)
        except Exception as exc:                    # noqa: BLE001
            raise TargetError(f"Could not open Outlook: {exc}") from exc

    def enumerate(self, store, root: int, on_batch=None, should_cancel=None,
                  wait_if_paused=None, batch_size: int = 500) -> int:
        self.authenticate()
        self.errors = []
        self.error_count = 0
        batch_start = len(store)

        def emit_if_due():
            nonlocal batch_start
            if on_batch and len(store) - batch_start >= batch_size:
                on_batch((batch_start, len(store)))
                batch_start = len(store)

        self._walk(self._client, store, root, should_cancel, wait_if_paused,
                   emit_if_due)
        store.build_child_lists()
        if on_batch and len(store) > batch_start:
            on_batch((batch_start, len(store)))
        return len(store)

    def _walk(self, folder, store, parent: int, should_cancel, wait_if_paused,
              emit_if_due) -> None:
        """One node per folder, one per item.

        Recursive rather than breadth-first because a mailbox is shallow and
        MAPI hands back child folders as an attribute of the parent -- there is
        no cheap way to enumerate a level at a time.
        """
        if wait_if_paused:
            wait_if_paused()
        if should_cancel and should_cancel():
            return
        name = _attr(folder, "Name") or "(folder)"
        node = store.add(parent, name, size=0, alloc=0, attrs=DIR)
        try:
            items = _attr(folder, "Items") or ()
            for item in items:
                if should_cancel and should_cancel():
                    return
                size = int(_attr(item, "Size") or 0)
                store.add(node, _attr(item, "Subject") or NO_SUBJECT,
                          # No cluster geometry in a mailbox; a message costs
                          # what MAPI says it costs.
                          size=size, alloc=size)
                emit_if_due()
        except Exception as exc:                    # noqa: BLE001
            # One unreadable folder -- a store that will not open, an item
            # MAPI refuses -- must not end the scan, exactly as an
            # access-denied directory does not end a local one.
            self._record_error(name, str(exc))
        try:
            children = list(_attr(folder, "Folders") or ())
        except Exception as exc:                    # noqa: BLE001
            self._record_error(name, str(exc))
            children = []
        for child in children:
            self._walk(child, store, node, should_cancel, wait_if_paused,
                       emit_if_due)

    def _record_error(self, where: str, why: str) -> None:
        self.error_count += 1
        if len(self.errors) < 100:
            self.errors.append((where, why))

    def close(self) -> None:
        self._client = None


def _attr(obj, name):
    """COM attribute access that reports a refusal instead of masking it.

    `getattr` with a default would swallow a MAPI error as "absent", and an
    empty mailbox and an unreadable one would look identical.
    """
    return getattr(obj, name)


register(OutlookTarget)
