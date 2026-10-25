"""Capture real, read-only module views on Windows (no button automation).

Run from the repository: python -B tools/ui_gallery.py [--only About]
Results: %TEMP%/wct-gallery/index.html and index.json. Composite children
retain their host chrome; ordinary modules show their initial internal tab.
"""

from __future__ import annotations

import argparse
import html
import json
import math
import os
from pathlib import Path
import re
import sys
import tempfile
import time
import traceback

# Do not leave bytecode in src/ while other workers are editing it.
sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))


def parse_args():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--only", default="", help="Comma-separated label substrings; prefix with = for an exact label")
    parser.add_argument("--wait", type=float, default=8, help="Maximum data-load wait per pane")
    parser.add_argument("--theme", choices=("dark", "light"), default="dark")
    parser.add_argument("--sheet", action="store_true", help="Write labelled 3 by 2 contact sheets")
    args = parser.parse_args()
    if not math.isfinite(args.wait) or args.wait < 0:
        parser.error("--wait must be a finite, nonnegative number")
    return args


def pump(qapp):
    from PyQt6.QtCore import QEvent

    qapp.processEvents()
    qapp.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    qapp.processEvents()


def tracked_workers(module, widget):
    """Find workers on modules, nested cards and their Python controllers.

    Walk only UI-owned objects, not App, engine results or Qt internals.
    Worker.finished_emitted works even after its auto-deleted QRunnable is gone.
    """
    from PyQt6.QtWidgets import QWidget
    from core.base_module import BaseModule
    from core.worker import Worker

    pending = [(module, 0)]
    if widget is not None:
        pending.extend((item, 0) for item in [widget, *widget.findChildren(QWidget)])
    seen = set()
    workers = []
    while pending:
        item, depth = pending.pop()
        if id(item) in seen:
            continue
        seen.add(id(item))
        if isinstance(item, Worker):
            workers.append(item)
            continue
        if depth > 5:
            continue
        if isinstance(item, dict):
            values = item.values()
        elif isinstance(item, (tuple, list, set)):
            values = item
        elif isinstance(item, (BaseModule, QWidget)) or type(item).__module__.startswith(
            ("modules.", "ui.")
        ):
            values = (value for key, value in getattr(item, "__dict__", {}).items() if key != "app")
        else:
            continue
        pending.extend((value, depth + 1) for value in values)
    return workers


def wait_for_data(qapp, app, module, widget, seconds, keepalive):
    deadline = time.monotonic() + seconds
    quiet_since = None
    while True:
        pump(qapp)
        workers = tracked_workers(module, widget)
        keepalive.update((id(worker), worker) for worker in workers)
        busy = any(not worker.finished_emitted for worker in workers)
        # Also covers workers whose owner does not retain them and QRunnables
        # implemented without core.Worker. No blocking waitForDone on the UI thread.
        busy = busy or app.thread_pool.activeThreadCount() > 0
        now = time.monotonic()
        if busy:
            quiet_since = None
        elif quiet_since is None:
            quiet_since = now
        if now >= deadline or (quiet_since is not None and now - quiet_since >= 0.25):
            return
        time.sleep(min(0.02, max(0, deadline - now)))


def notice(label, detail):
    from PyQt6.QtWidgets import QLabel, QPlainTextEdit, QVBoxLayout, QWidget

    widget = QWidget()
    layout = QVBoxLayout(widget)
    title = QLabel(label)
    title.setWordWrap(True)
    layout.addWidget(title)
    text = QPlainTextEdit()
    text.setReadOnly(True)
    text.setPlainText(detail)
    layout.addWidget(text)
    return widget


def host_window():
    from PyQt6.QtWidgets import QFrame, QScrollArea

    # Match MainWindow._ensure_built: a large minimum-size pane scrolls rather
    # than forcing a wider screenshot. Capture the viewport, including scrollbars.
    host = QScrollArea()
    host.setFrameShape(QFrame.Shape.NoFrame)
    host.setWidgetResizable(True)
    host.setFixedSize(1400, 900)
    host.move(-20000, 0)
    return host


def capture(qapp, app, prototype, child_index, label, path, wait, elevated, keepalive):
    from PyQt6.QtCore import QSignalBlocker
    from core.composite_module import CompositeModule

    started = time.monotonic()
    errors = []
    host = host_window()
    module = None
    widget = None
    previous_hook = sys.excepthook

    def qt_exception(exc_type, exc, tb):
        # Exceptions from Qt slots bypass the caller's try/except. Record them
        # instead of PyQt's default process-aborting exception hook.
        detail = "".join(traceback.format_exception(exc_type, exc, tb))
        errors.append(detail)
        print(detail, file=sys.stderr, flush=True)

    sys.excepthook = qt_exception
    try:
        # A fresh module per image gives every capture a complete lifecycle and
        # avoids reusing widgets deleted after the previous composite child.
        module = type(prototype)()
        if module.requires_admin and not elevated and not module.read_only_unelevated:
            raise PermissionError(f"{label} requires administrator privileges (run unelevated).")
        module.on_start(app)
        widget = module.create_widget()
        host.setWidget(widget)
        if isinstance(module, CompositeModule) and child_index is not None:
            # Select without firing _on_tab_changed, which activates implicitly;
            # on_activate below is the sole explicit activation for this capture.
            with QSignalBlocker(module._tabs):
                module._tabs.setCurrentIndex(child_index)
            module._current = child_index
            module._ensure_built(child_index)
            reason = module._disabled.get(child_index)
            if reason:
                errors.append(reason)
        host.show()
        host.move(-20000, 0)
        pump(qapp)
        module.on_activate()
        wait_for_data(qapp, app, module, widget, wait, keepalive)
        if errors:
            raise RuntimeError("Pane reported an error; see the companion text file.")
        if not host.grab().save(str(path), "PNG"):
            raise OSError(f"Could not save {path}")
    except Exception:
        errors.append(traceback.format_exc())
    finally:
        if module is not None:
            # Hold workers even if module.on_stop empties its worker lists.
            try:
                keepalive.update((id(w), w) for w in tracked_workers(module, widget))
            except Exception:
                errors.append(traceback.format_exc())
            for method in (module.on_deactivate, module.on_stop):
                try:
                    method()
                except Exception:
                    errors.append(traceback.format_exc())
        if errors:
            # Replace the pane only AFTER teardown: its original widgets must
            # remain valid while on_deactivate/on_stop release their resources.
            try:
                host.setWidget(notice(label, "\n\n".join(errors)))
                host.show()
                host.move(-20000, 0)
                pump(qapp)
                if not host.grab().save(str(path), "PNG"):
                    raise OSError(f"Could not save error PNG: {path}")
            except Exception:
                errors.append(traceback.format_exc())
        host.hide()
        host.deleteLater()  # also deletes the hosted module widget
        pump(qapp)
        sys.excepthook = previous_hook
    error = "\n\n".join(errors) or None
    if error:
        path.with_suffix(".txt").write_text(error, encoding="utf-8")
    return {"label": label, "file": path.name,
            "seconds": round(time.monotonic() - started, 3), "error": error}


def write_index(output, metadata, panes):
    (output / "index.json").write_text(
        json.dumps({**metadata, "panes": panes}, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    cards = []
    for pane in panes:
        filename = html.escape(pane["file"], quote=True)
        label = html.escape(pane["label"])
        error = ""
        if pane["error"]:
            error = f'<a class="error" href="{filename[:-4]}.txt">Error details</a>'
        cards.append(
            f'<article><a href="{filename}"><img loading="lazy" src="{filename}" '
            f'alt="{label}"></a><p>{label}</p><small>{pane["seconds"]:.2f}s</small> {error}</article>'
        )
    (output / "index.html").write_text(
        '<!doctype html><html lang="en"><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        '<title>WinClientTool UI gallery</title><style>'
        'body{background:#202124;color:#eee;font:15px Segoe UI,sans-serif;margin:24px}'
        'main{display:grid;grid-template-columns:repeat(auto-fit,minmax(340px,1fr));gap:20px}'
        'article{background:#303134;padding:12px;border-radius:8px}img{width:100%;height:auto}'
        'p{margin:8px 0}small{color:#bbb}a{color:#8ab4f8}.error{color:#ffadad}'
        '</style><h1>WinClientTool UI gallery</h1>'
        f'<p>{len(panes)} panes · {metadata["theme"]} · '
        f'{"Elevated" if metadata["elevated"] else "Unelevated"} · 1400 × 900 logical pixels</p>'
        '<main>' + "\n".join(cards) + '</main></html>', encoding="utf-8"
    )


def write_sheets(output, panes, theme):
    """Pillow contact sheets, six labelled captures per page."""
    from PIL import Image, ImageDraw, ImageFont
    destination = output / "sheets"
    destination.mkdir(exist_ok=True)
    font = ImageFont.truetype("segoeui.ttf", 18)
    for offset in range(0, len(panes), 6):
        sheet = Image.new("RGB", (1500, 740), "white")
        draw = ImageDraw.Draw(sheet)
        for index, pane in enumerate(panes[offset:offset + 6]):
            x, y = (index % 3) * 500, (index // 3) * 370
            with Image.open(output / pane["file"]) as source:
                preview = source.convert("RGB")
                preview.thumbnail((488, 326))
                sheet.paste(preview, (x + 6, y + 6))
            draw.text((x + 8, y + 338), pane["label"], font=font, fill="black")
        sheet.save(destination / f"{theme}-{offset // 6 + 1:02d}.png")


def component_module():
    from core.base_module import BaseModule

    class SharedComponents(BaseModule):
        name = "Shared Components"
        description = "Shared visual components in measured, unreadable, loading and filtered states."

        def create_widget(self):
            from PyQt6.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QTableWidget, QTableWidgetItem
            from ui.module_intro import add_module_intro
            from ui.glance import GlanceTile, GlanceStrip
            from ui.chip_bar import ChipBar
            from ui.empty_state import EmptyState
            from ui.table_style import apply_table_style
            root = QWidget()
            layout = QVBoxLayout(root)
            add_module_intro(root, self.description + " Expand this explanation to read the details.")
            tiles = []
            for caption, role, value in (("Healthy", "ok", 42), ("Needs review", "warning", 3), ("Blocked", "danger", 1), ("Disk status", "unknown", None)):
                tile = GlanceTile(caption, role)
                tile.set(value, "2 new since last scan", reason="Access was denied to this reading.")
                tile.set_history([1, 3, 2, 5, 4, 7])
                tiles.append(tile)
            layout.addWidget(GlanceStrip(tiles))
            layout.addWidget(ChipBar((("all", "All", 46), ("review", "Needs review", 3), ("none", "Offline", 0))))
            table = QTableWidget(3, 3)
            table.setHorizontalHeaderLabels(["Check", "Count", "Last checked"])
            for row, values in enumerate((("System services", "42", "Today 09:30"), ("Startup items", "3", "Today 09:29"), ("Unreadable sources", "1", "Today 09:28"))):
                for column, value in enumerate(values):
                    table.setItem(row, column, QTableWidgetItem(value))
            apply_table_style(table, numeric_columns=(1,), date_columns=(2,))
            table.resizeColumnsToContents()
            table.horizontalHeader().setStretchLastSection(True)
            layout.addWidget(table)
            states = QHBoxLayout()
            states.addWidget(EmptyState("○", "No matching results", "Clear the filter to see all results.", "Clear filter"))
            states.addWidget(EmptyState("↻", "Reading status", "Checking available sources.", variant="loading"))
            states.addWidget(EmptyState("!", "Could not read", "Try the check again.", "Retry", variant="error", reason="Access to this source was denied.", last_checked="Last checked just now"))
            layout.addLayout(states)
            return root

    return SharedComponents()


def main():
    args = parse_args()
    if sys.platform != "win32":
        raise SystemExit("This renderer requires the real Windows Qt platform.")
    # Override inherited test-runner settings; native Windows supplies real fonts.
    os.environ["QT_QPA_PLATFORM"] = "windows"
    from PyQt6.QtWidgets import QApplication
    from app import App
    from core.admin_utils import is_admin
    from core.composite_module import CompositeModule
    from main import register_all_modules

    qapp = QApplication([sys.argv[0]])
    qapp.setQuitOnLastWindowClosed(False)
    output = Path(tempfile.gettempdir()) / "wct-gallery"
    output.mkdir(parents=True, exist_ok=True)
    elevated = bool(is_admin())
    metadata = {"elevated": elevated, "theme": args.theme, "wait": args.wait,
                "platform": qapp.platformName(), "width": 1400, "height": 900}
    panes = []
    keepalive = {}
    # Use real services without overwriting the user's config or rotating their
    # logs. No app.start(): it would start even excluded and Stress Test modules.
    with tempfile.TemporaryDirectory(prefix="wct-gallery-session-") as scratch:
        app = App(app_data_dir=scratch)
        try:
            app.theme.apply_theme(args.theme)
            register_all_modules(app)
            specifications = []

            def collect(module):
                label = module.name
                if module.name.casefold() == "stress test":
                    return
                specifications.append((module, label, None))
                if isinstance(module, CompositeModule):
                    for index, child in enumerate(module.children):
                        # Current composites have a single level of children.
                        if child.name.casefold() != "stress test":
                            specifications.append((module, f"{label} > {child.name}", index))

            collect(component_module())
            for module in app.module_registry.modules:
                collect(module)
            for number, (module, label, child_index) in enumerate(specifications, 1):
                filters = [part.strip().casefold() for part in args.only.split(",") if part.strip()]
                if filters and not any(label.casefold() == part[1:] if part.startswith("=") else part in label.casefold() for part in filters):
                    continue
                slug = re.sub(r'[<>:"/\\|?*\x00-\x1f]+', "-", label).strip(" .")
                path = output / f"{number:02d}-{slug}-{args.theme}.png"
                record = capture(qapp, app, module, child_index,
                                 label, path, args.wait, elevated, keepalive)
                panes.append(record)
                write_index(output, metadata, panes)  # useful even if a later pane crashes natively
                print(f'{label}: {"ERROR" if record["error"] else "OK"} ({record["seconds"]:.2f}s)',
                      flush=True)
            write_index(output, metadata, panes)
            if args.sheet:
                write_sheets(output, panes, args.theme)
        finally:
            # Every rendered module was already stopped. Do not stop unstarted
            # registry prototypes via App.shutdown(). Keep services alive while
            # cancelled workers finish and deliver their queued signals.
            while app.thread_pool.activeThreadCount():
                pump(qapp)
                time.sleep(0.02)
            pump(qapp)
            app.backup.close()
            app.logger.shutdown()
            App.instance = None
    print(output / "index.html", flush=True)
    return 0 if panes else 1


if __name__ == "__main__":
    raise SystemExit(main())
