"""CMTrace-style log viewer.

Opens any log file, colours it by severity, follows it as it grows, and lets
you filter and search it. Unlike the CBS and DISM viewers, which point at
fixed system paths, this one reads whatever you hand it -- which is why it is
its own module rather than another tab in the Diagnose hub.

Reading and parsing run on the UI thread deliberately. `LogReader` caps a
first read at a few megabytes and every later read is only what was appended,
so the work per tick is small and bounded; a worker thread would buy nothing
and cost the marshalling.
"""
import logging
import os
from datetime import timedelta
from typing import Optional

from PyQt6.QtCore import (Qt, QTimer, QDateTime, QStringListModel,
                          pyqtSignal)
from PyQt6.QtGui import QFont, QKeySequence, QShortcut
from PyQt6.QtWidgets import (
    QAbstractItemView, QCheckBox, QComboBox, QDateTimeEdit, QDialog,
    QDialogButtonBox, QFileDialog, QHBoxLayout,
    QHeaderView, QLabel, QLineEdit, QMenu, QPlainTextEdit, QPushButton,
    QCompleter, QListWidget, QListWidgetItem, QSplitter, QTableView,
    QToolButton,
    QVBoxLayout, QWidget,
)

from core.base_module import BaseModule
from core.module_groups import ModuleGroup
from core.search_provider import SearchProvider

from modules.log_viewer import cmtrace_parser
from modules.log_viewer.cmtrace_parser import UNKNOWN_TIME
from modules.log_viewer.log_model import (  # noqa: I001
    COLUMNS, split_terms,
    COMPONENT, LogModel, MESSAGE, PACKAGE, SEVERITY, SOURCE, THREAD,
    TIME,
)
from modules.log_viewer.history import (RECENT_CAP, load_history,
                                        load_recent, remember,
                                        save_history, save_recent)
from modules.log_viewer.layout import load_layout, save_layout
from modules.log_viewer.known_logs import largest_cbs_archive
from modules.log_viewer.log_reader import DEFAULT_MAX_BYTES
from modules.log_viewer.clustering import normalise
from modules.log_viewer.compare import compare
from modules.log_viewer.density import buckets
from modules.log_viewer.density_strip import DensityStrip
from modules.log_viewer.archives import (extract_cab, extract_zip,
                                         is_cab, is_zip)
from modules.log_viewer.filescan import scan_file
from modules.log_viewer.folder_dialog import FolderPickDialog
from modules.log_viewer.log_set import (LOG_SUFFIXES, LogSet,
                                        preselected)
from modules.log_viewer.views import View, load_view, save_view
from modules.log_viewer.presets import (BUILT_IN, Preset,
                                        load_presets,
                                        save_presets)
from modules.log_viewer.sessions import sessions
from modules.log_viewer.log_stats import (first_error, gaps,
                                          last_success_before,
                                          top_codes,
                                          top_components,
                                          top_messages)
from modules.log_viewer.log_search_provider import LogSearchProvider

logger = logging.getLogger(__name__)

#: How often a followed file is checked. Fast enough to feel live, slow
#: enough that a busy log does not repaint the table continuously.
FOLLOW_MS = 1000

LEVELS = ("Error", "Warning", "Info")

#: How wide the Package column may get. Sized to content it took 470px on the
#: real archive -- servicing names run to 62 characters -- and stood empty on
#: every visible row while pushing Message off to the right. The full value is
#: in the message and the detail pane; the column only has to be enough to
#: recognise and compare.
PACKAGE_MAX_WIDTH = 240


def _size(count: int) -> str:
    """Bytes as something a person can read, for the status line.

    Local rather than imported: the only formatter in the tree lives inside
    the cleanup scanner's package, and reaching into that from here would tie
    two unrelated modules together for four lines.
    """
    step = float(count)
    for unit in ("B", "KB", "MB", "GB"):
        if step < 1024:
            return f"{step:.1f} {unit}"
        step /= 1024
    return f"{step:.1f} TB"


class LogViewerWidget(QWidget):
    #: How many records are added to the model before the window is given a
    #: chance to repaint. Measured: the 84.5 MB archive parses in 1.44s and
    #: the 90-log Windows\Logs tree in 1.45s, both past the second where a
    #: frozen window stops looking busy and starts looking broken.
    #:
    #: Chunked on the UI thread rather than moved to a worker: making open()
    #: asynchronous would break the contract that it completes before it
    #: returns, which roughly two hundred existing tests rely on, and would
    #: add widget-lifetime hazards for a 1.4-second win.
    CHUNK = 5000

    #: Emitted 0..100 while a big log is being taken in.
    progress_reported = pyqtSignal(int)

    #: Offered on a row. Five minutes is the default because a servicing
    #: operation's related lines land within seconds of each other; the
    #: wider ones are for correlating across a reboot.
    RANGE_MINUTES = (1, 5, 15, 60)

    #: Windows 11 rolls CBS into .cab files, so they belong in the dialog
    #: beside the plain logs rather than behind "All files".
    FILE_FILTER = ("Logs and bundles (*.log *.lo_ *.txt *.cab *.zip);;"
                   "Cabinets (*.cab);;Bundles (*.zip);;All files (*)")

    def __init__(self, parent=None, max_bytes: Optional[int] = None) -> None:
        super().__init__(parent)
        self.model = LogModel(self)
        self.provider = LogSearchProvider()
        self._set: Optional[LogSet] = None
        #: Every log currently open. One entry is the ordinary case; a folder
        #: puts several in and the pane becomes a merged timeline.
        self._paths: list = []
        #: How much one read covers, forwards on Open and backwards on "load
        #: earlier". Only the tests pass this; they would otherwise have to
        #: write 32 MB files. `tools/log_viewer_real_check.py` pages the real
        #: archive at the real size, because a seam like this hides the bugs
        #: that live on the other side of it.
        self._max_bytes = max_bytes or DEFAULT_MAX_BYTES
        #: One load at a time. A step costs ~1.2s at the real size and the
        #: scroll bar keeps emitting throughout, so without this the auto
        #: path chains loads and walks the window back several steps from one
        #: flick of the wheel.
        self._loading_earlier = False
        #: Set by cancel_load() while a big log is being taken in.
        self._cancelled = False
        #: (records taken in, records offered) when a load was cancelled, so
        #: the status line can say so. Kept here rather than written straight
        #: to the label because `_poll` calls `_update_status()` immediately
        #: after a load and would overwrite the message.
        self._cancelled_at = None
        #: Logs and folders opened recently, most recent first.
        self._recent: list = []
        #: The folder to re-scan while following, or "" -- a repair run
        #: creates new logs and Follow only tracks the ones already open.
        self._watched_folder = ""
        #: Temporary extractions, mapped back to the cab they came from, so
        #: the status line can name the file the user actually chose.
        self._extracted: dict = {}
        self._path = ""
        # Dropping a file or a folder on the pane opens it. The folder path
        # is already a first-class input, so this costs almost nothing.
        self.setAcceptDrops(True)
        self._last_found = -1
        self._lookup = None
        self._rules = []
        #: Overrides for the two colours painted inside a message. Empty
        #: means "follow the theme"; see match_colours.
        self._match_colours = {}
        self._config = None
        # Whether the user has actually asked for a time range. The boxes
        # always HOLD a value (the log's whole span, or whatever
        # anchor_range/a manual edit set), but _apply_filters must not send
        # it to the model unless this is True -- otherwise "Clear range"
        # is undone by the next touch of any other control, and while
        # following, the upper bound is frozen at the moment the log was
        # opened, so new lines (later than that bound) silently stop
        # appearing.
        self._range_active = False
        #: Columns the reader has turned off, by NAME. Thread is dead weight
        #: on CBS and essential on DISM's 329 threads.
        self._hidden_columns: set = set()
        #: Columns hidden because they would be blank, recomputed per load.
        self._auto_hidden: set = set()

        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(6)

        # Construction in the order the pane reads, top to bottom.
        # This was one 480-line __init__; nothing about it could be
        # read or tested in pieces, in a module whose parser and
        # reader are deliberately separable and separately tested.
        self._build_toolbar(layout)
        self._build_find_row(layout)
        self._build_range_row(layout)
        self._build_markers(layout)
        self._build_table(layout)
        self._build_status(layout)
        self._wire_signals(layout)


    def _build_toolbar(self, layout) -> None:
        """The open/reload/follow row, and the source and severity filters."""
        top = QHBoxLayout()
        # A split button: pressing it browses, the arrow lists the logs this
        # machine actually has. Same idea as TreeSize's scan target -- offer
        # somewhere to go rather than making someone type a path.
        self.open_button = QToolButton(self)
        self.open_button.setText("Open log…")
        self.open_button.setPopupMode(
            QToolButton.ToolButtonPopupMode.MenuButtonPopup)
        self.open_button.clicked.connect(self.choose_file)
        self.open_menu = QMenu(self.open_button)
        self.open_button.setMenu(self.open_menu)
        self._build_open_menu()
        top.addWidget(self.open_button)

        self.follow = QCheckBox("Follow", self)
        self.follow.setToolTip("Re-read the file as it grows and stay at the "
                               "bottom, the way CMTrace does.")
        self.follow.toggled.connect(self._on_follow_toggled)
        top.addWidget(self.follow)

        # CSI writes operation lists that run to 1,260 continuation lines
        # under one record, and 9,185 of the big CBS archive's 90,714 rows
        # are continuations. Folded by default, because a log you have to
        # scroll past is a log you stop reading -- and the parent's
        # "(+N lines)" suffix and the status bar both say what is hidden.
        self.fold = QCheckBox("Fold continuations", self)
        self.fold.setChecked(True)
        self.fold.setToolTip("Tuck wrapped continuation lines under the "
                             "record they belong to.")
        self.fold.toggled.connect(self._on_fold_toggled)
        top.addWidget(self.fold)

        # A log too big to hold is opened at its tail, and until these two
        # existed everything before that point was simply unreachable: the
        # real CBS archive is 380 MB and the window is 32 MB of it.
        self.load_earlier_button = QPushButton("Load earlier", self)
        self.load_earlier_button.setToolTip(
            "Read the previous chunk of this file and put it above what is "
            "shown. Scrolling to the top does the same thing.")
        self.load_earlier_button.setEnabled(False)
        self.load_earlier_button.clicked.connect(self.load_earlier)
        top.addWidget(self.load_earlier_button)

        self.newest_button = QPushButton("Newest", self)
        self.newest_button.setToolTip(
            "Go back to the end of the file, where new lines land.")
        self.newest_button.setEnabled(False)
        self.newest_button.clicked.connect(self.go_to_newest)
        top.addWidget(self.newest_button)

        # `grep -C` for errors: the single most common thing anyone does to
        # a log by hand.
        self.context_box = QCheckBox("Errors + context", self)
        self.context_box.setToolTip(
            "Show only the errors and the lines either side of them.")
        self.context_box.toggled.connect(self._on_context_toggled)
        top.addWidget(self.context_box)

        self.rolled = QCheckBox("Include rolled (.lo_)", self)
        self.rolled.setToolTip("ConfigMgr rolls foo.log to foo.lo_ . Read the "
                               "pair as one timeline.")
        self.rolled.toggled.connect(lambda _c: self.reload())
        top.addWidget(self.rolled)

        top.addSpacing(12)
        self._level_boxes = {}
        for level in LEVELS:
            box = QCheckBox(level, self)
            box.setChecked(True)
            box.toggled.connect(lambda _c: self._apply_filters())
            self._level_boxes[level] = box
            top.addWidget(box)

        top.addSpacing(12)
        # A checkable menu rather than a combo: CSI does the servicing work
        # and CBS narrates it, so reading a failure means seeing both, and a
        # combo can only ever say one.
        self.component_button = QToolButton(self)
        self.component_button.setText("Component: All")
        self.component_button.setPopupMode(
            QToolButton.ToolButtonPopupMode.InstantPopup)
        self.component_menu = QMenu(self.component_button)
        self.component_button.setMenu(self.component_menu)
        self._component_actions: list = []
        self._populating_components = False
        top.addWidget(self.component_button)

        # Only useful once several logs are open; hidden until then, like the
        # column it filters on.
        self.source_label = QLabel("Source:", self)
        top.addWidget(self.source_label)
        self.source = QComboBox(self)
        self.source.setMinimumWidth(150)
        self.source.addItem("All")
        self.source.currentIndexChanged.connect(lambda _i: self._apply_filters())
        top.addWidget(self.source)
        self.source_label.setVisible(False)
        self.source.setVisible(False)

        top.addStretch(1)
        layout.addLayout(top)

    def _build_find_row(self, layout) -> None:
        """Find, Filter and Exclude.

        Find JUMPS to the next match and leaves everything on screen;
        Filter HIDES everything that does not match. Two jobs, two
        boxes — a single shared box only re-applied when toggled."""
        find_row = QHBoxLayout()
        find_row.addWidget(QLabel("Find:", self))
        self.find_box = QLineEdit(self)
        self.find_box.setPlaceholderText("text to look for")
        self.find_box.returnPressed.connect(self.find_next)
        # Find does not filter, so it does not go through
        # _apply_filters -- but what it is looking for is still
        # coloured in place as it is typed.
        self.find_box.textChanged.connect(
            lambda _t: self._refresh_match_colours())
        find_row.addWidget(self.find_box, 1)
        self.previous_button = QPushButton("Previous", self)
        self.previous_button.clicked.connect(self.find_previous)
        find_row.addWidget(self.previous_button)
        self.scan_button = QPushButton("Search earlier", self)
        self.scan_button.setToolTip(
            "Search the part of the file that has not been loaded.")
        self.scan_button.clicked.connect(self.search_unloaded)
        find_row.addWidget(self.scan_button)

        self.next_button = QPushButton("Next", self)
        self.next_button.clicked.connect(self.find_next)
        find_row.addWidget(self.next_button)

        # Find JUMPS to the next match and leaves everything on screen.
        # Filter HIDES everything that does not match. Two different jobs, so
        # two boxes -- the previous "Show only matches" checkbox shared Find's
        # text and only re-applied when it was toggled, so editing the text
        # afterwards silently did nothing.
        find_row.addSpacing(16)
        find_row.addWidget(QLabel("Filter:", self))
        self.filter_box = QLineEdit(self)
        self.filter_box.setPlaceholderText(
            "show only lines containing… (case insensitive)")
        self.filter_box.setClearButtonEnabled(True)
        self.filter_box.textChanged.connect(lambda _t: self._apply_filters())
        # The filter applies live, so every keystroke would otherwise be
        # remembered -- H, HR, HRE, HRES. Enter is the commit gesture.
        self.filter_box.returnPressed.connect(self._remember_filter)
        self._filter_history: list = []
        self._history_model = QStringListModel(self)
        completer = QCompleter(self._history_model, self)
        completer.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        # A log pattern is rarely recalled from its first character.
        completer.setFilterMode(Qt.MatchFlag.MatchContains)
        self.filter_box.setCompleter(completer)
        find_row.addWidget(self.filter_box, 1)

        # The inverse box. A real CBS.log is mostly `Appl: detectParent` and
        # `Plan: Package`; no positive filter drops that boilerplate without
        # dropping the rest with it.
        find_row.addWidget(QLabel("Hide:", self))
        self.exclude_box = QLineEdit(self)
        self.exclude_box.setPlaceholderText("hide lines containing…")
        self.exclude_box.setClearButtonEnabled(True)
        self.exclude_box.textChanged.connect(lambda _t: self._apply_filters())
        find_row.addWidget(self.exclude_box, 1)
        layout.addLayout(find_row)

    def _build_range_row(self, layout) -> None:
        """Thread, component, time range and the regex switch."""
        range_row = QHBoxLayout()
        range_row.addWidget(QLabel("Thread:", self))
        # Editable with a completer, not a plain dropdown: DISM carries 329
        # distinct thread ids, and an alphabetical list of 329 numbers is not
        # a control anyone can use. Ordered by how common each one is.
        self.thread = QComboBox(self)
        self.thread.setEditable(True)
        self.thread.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        self.thread.setMinimumWidth(140)
        self.thread.addItem("All")
        self.thread.currentIndexChanged.connect(lambda _i: self._apply_filters())
        range_row.addWidget(self.thread)

        range_row.addSpacing(12)
        range_row.addWidget(QLabel("From:", self))
        self.time_from = QDateTimeEdit(self)
        self.time_from.setDisplayFormat("yyyy-MM-dd HH:mm:ss")
        # Only a genuine user edit reaches this slot: both programmatic
        # writers (_reset_range, anchor_range) already blockSignals() around
        # setDateTime(). A real edit is what turns the range on.
        self.time_from.dateTimeChanged.connect(self._on_range_edited)
        range_row.addWidget(self.time_from)
        range_row.addWidget(QLabel("To:", self))
        self.time_to = QDateTimeEdit(self)
        self.time_to.setDisplayFormat("yyyy-MM-dd HH:mm:ss")
        self.time_to.dateTimeChanged.connect(self._on_range_edited)
        range_row.addWidget(self.time_to)
        # Jumping is not filtering. The range boxes HIDE everything outside
        # the range; this only moves you to a moment, which is what you want
        # when correlating a log against an event log or someone's account of
        # what happened. It reuses the From box because that is already
        # prefilled with the loaded span.
        self.goto_button = QPushButton("Go to", self)
        self.goto_button.setToolTip(
            "Scroll to a moment in time without hiding anything.")
        self.goto_button.clicked.connect(self.choose_go_to)
        range_row.addWidget(self.goto_button)

        self.clear_range_button = QPushButton("Clear range", self)
        self.clear_range_button.clicked.connect(self._reset_range)
        range_row.addWidget(self.clear_range_button)

        self.export_button = QPushButton("Export…", self)
        self.export_button.clicked.connect(self.choose_export)
        range_row.addWidget(self.export_button)

        self.error_lookup_button = QPushButton("Error lookup…", self)
        self.error_lookup_button.clicked.connect(
            lambda _c=False: self.open_error_lookup())
        range_row.addWidget(self.error_lookup_button)

        # The knowledge of what to grep for is the expensive part; it should
        # not live in one person's head. Every shipped preset was run against
        # the real log it targets before it was added.
        self.preset_button = QToolButton(self)
        self.preset_button.setText("Presets")
        self.preset_button.setPopupMode(
            QToolButton.ToolButtonPopupMode.InstantPopup)
        self.preset_menu = QMenu(self.preset_button)
        self.preset_button.setMenu(self.preset_menu)
        self._presets: list = []
        self._build_preset_menu()
        range_row.addWidget(self.preset_button)

        self.summary_button = QPushButton("Summary", self)
        self.summary_button.setCheckable(True)
        self.summary_button.setToolTip(
            "What is failing most often in what is currently shown.")
        self.summary_button.toggled.connect(self._on_summary_toggled)
        range_row.addWidget(self.summary_button)

        self.highlight_button = QPushButton("Highlight rules…", self)
        self.highlight_button.clicked.connect(self.edit_highlight_rules)
        range_row.addWidget(self.highlight_button)

        self.colour_button = QPushButton("Message colours…", self)
        self.colour_button.setToolTip(
            "Choose the colours the Filter and Find matches and the failing "
            "error codes are painted in.")
        self.colour_button.clicked.connect(self.edit_match_colours)
        range_row.addWidget(self.colour_button)

        range_row.addSpacing(12)
        self.regex_box = QCheckBox("Regex", self)
        self.regex_box.setToolTip("Treat Find and Filter as regular "
                                  "expressions.")
        self.regex_box.toggled.connect(lambda _c: self._apply_filters())
        range_row.addWidget(self.regex_box)
        range_row.addStretch(1)
        layout.addLayout(range_row)

    def _build_markers(self, layout) -> None:
        """The pinned-rows table and the density strip.

        The pinned table has its OWN model rather than a proxy over the
        main one: a pinned row has to survive a filter that would hide
        it, and a proxy cannot show a row the source has excluded."""
        # Rows kept in view while you scroll. Its OWN model rather than a
        # proxy over the main one: a pinned row has to survive a filter that
        # would hide it, and a proxy cannot show a row the source model has
        # already excluded.
        self.pinned_model = LogModel(self)
        self.pinned_table = QTableView(self)
        self.pinned_table.setModel(self.pinned_model)
        self.pinned_table.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectRows)
        self.pinned_table.setEditTriggers(
            QAbstractItemView.EditTrigger.NoEditTriggers)
        self.pinned_table.verticalHeader().setVisible(False)
        self.pinned_table.horizontalHeader().setVisible(False)
        self.pinned_table.verticalHeader().setDefaultSectionSize(20)
        self.pinned_table.setShowGrid(False)
        self.pinned_table.setFont(QFont("Consolas", 9))
        self.pinned_table.setWordWrap(False)
        self.pinned_table.setMaximumHeight(84)
        self.pinned_table.setVisible(False)
        self._pinned: list = []
        layout.addWidget(self.pinned_table)

        # Above the table: where the records and the failures fall across the
        # loaded span. On a 1.5-million-record archive this is the difference
        # between reading a log and hunting through one.
        self.density = DensityStrip(self)
        self.density.moment_picked.connect(self.go_to_time)
        self.density.setVisible(False)
        layout.addWidget(self.density)

    def _build_table(self, layout) -> None:
        """The log table and the detail panel, in their splitter."""
        self.table = QTableView(self)
        self.table.setModel(self.model)
        self.table.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setAlternatingRowColors(False)
        self.table.verticalHeader().setVisible(False)
        self.table.verticalHeader().setDefaultSectionSize(20)
        self.table.setShowGrid(False)
        self.table.setFont(QFont("Consolas", 9))
        self.table.setWordWrap(False)
        # Message takes whatever is left; without this every column sits at
        # its default width and the text is crammed into the left third of
        # the window with two thirds empty beside it.
        header = self.table.horizontalHeader()
        # SOURCE sizes to content like the rest: left at Qt's default
        # width it rendered "CbsPersist_20…", and CBS archives differ
        # only in the timestamp at the END of the name, so every one of
        # them elided to the same characters.
        for column in (TIME, SOURCE, SEVERITY, COMPONENT, PACKAGE,
                       THREAD):
            header.setSectionResizeMode(column,
                                        QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(MESSAGE, QHeaderView.ResizeMode.Stretch)

        from .log_delegate import LogMessageDelegate

        # Kept as an attribute: it is told what the Filter and Find boxes
        # hold, so it can pick those out inside the message.
        self.message_delegate = LogMessageDelegate(self)
        self.table.setItemDelegateForColumn(MESSAGE, self.message_delegate)

        # Message is a Stretch section, so the table has no horizontal scroll
        # bar to reach a clipped line with -- measured on a real CBS archive,
        # 3,998 of 4,000 sampled rows were elided and the tooltip was the
        # only way to read one. Scrolling would not have helped either: those
        # rows are up to 2,751px wide. CMTrace uses a detail pane; so does
        # this. Collapsible and visible by default, so dragging it shut is
        # the setting rather than something to store.
        self.detail = QPlainTextEdit(self)
        self.detail.setReadOnly(True)
        self.detail.setFont(QFont("Consolas", 9))
        self.detail.setLineWrapMode(QPlainTextEdit.LineWrapMode.WidgetWidth)
        self.detail.setPlaceholderText(
            "Select a row to read the whole line here.")

        self.splitter = QSplitter(Qt.Orientation.Vertical, self)
        self.splitter.addWidget(self.table)
        self.splitter.addWidget(self.detail)
        self.splitter.setChildrenCollapsible(True)
        self.splitter.setStretchFactor(0, 1)
        self.splitter.setStretchFactor(1, 0)
        self.splitter.setSizes([600, 160])
        layout.addWidget(self.splitter, 1)

    def _build_status(self, layout) -> None:
        """The failure-summary panel and the status line."""
        # Counts of what is failing, over whatever the filter has left.
        # Hidden until asked for: top_codes costs 297 ms over the real
        # archive's 138,683 records, which is not a per-keystroke expense.
        self.summary_panel = QWidget(self)
        summary_row = QHBoxLayout(self.summary_panel)
        summary_row.setContentsMargins(0, 0, 0, 0)
        self.summary_codes = QListWidget(self.summary_panel)
        self.summary_components = QListWidget(self.summary_panel)
        self.summary_messages = QListWidget(self.summary_panel)
        self.summary_gaps = QListWidget(self.summary_panel)
        self.summary_failure = QListWidget(self.summary_panel)
        self.summary_sessions = QListWidget(self.summary_panel)
        self.summary_bookmarks = QListWidget(self.summary_panel)
        for title, listing in (("Bookmarks", self.summary_bookmarks),
                               ("The failure", self.summary_failure),
                               ("Servicing sessions", self.summary_sessions),
                               ("Failing codes", self.summary_codes),
                               ("Components", self.summary_components),
                               ("Silences", self.summary_gaps),
                               ("Repeated lines", self.summary_messages)):
            column = QVBoxLayout()
            column.setContentsMargins(0, 0, 0, 0)
            column.addWidget(QLabel(title, self.summary_panel))
            listing.setMaximumHeight(160)
            # The table's font: it tightens the rows enough to see most of
            # the ten, and being monospace it lines the counts up into a
            # column you can compare down.
            listing.setFont(QFont("Consolas", 9))
            listing.setUniformItemSizes(True)
            listing.setToolTip("Click a row to filter by it.")
            column.addWidget(listing)
            summary_row.addLayout(column)
        self.summary_codes.itemClicked.connect(self._on_summary_code)
        self.summary_components.itemClicked.connect(
            self._on_summary_component)
        self.summary_messages.itemClicked.connect(self._on_summary_message)
        self.summary_gaps.itemClicked.connect(self._on_summary_gap)
        self.summary_failure.itemClicked.connect(self._on_summary_gap)
        self.summary_sessions.itemClicked.connect(self._on_summary_gap)
        self.summary_bookmarks.itemClicked.connect(self._on_summary_gap)
        self.summary_panel.setVisible(False)
        layout.addWidget(self.summary_panel)

        # Restarted on every change and fired once it goes quiet, so typing
        # a filter costs one recount rather than one per character.
        self._summary_timer = QTimer(self)
        self._summary_timer.setSingleShot(True)
        self._summary_timer.setInterval(400)
        self._summary_timer.timeout.connect(self._refresh_summary)

        self.status = QLabel("No log open.", self)
        self.status.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addWidget(self.status)

    def _wire_signals(self, layout) -> None:
        """Connections that need the widgets above to exist.

        Scroll, context menus, shortcuts and the follow timer. Kept
        last because every one of them names a widget built earlier."""
        # Reaching the top is a request for what comes before it. Connected
        # after the table exists, and guarded by _loading_earlier, since
        # prepending rows moves the bar and would otherwise re-enter here.
        header = self.table.horizontalHeader()
        header.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        header.customContextMenuRequested.connect(self._on_header_menu)

        self.table.verticalScrollBar().valueChanged.connect(self._on_scrolled)

        self._build_shortcuts()

        self.table.selectionModel().currentRowChanged.connect(
            self._on_row_selected)

        self.table.setContextMenuPolicy(
            Qt.ContextMenuPolicy.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self._on_context_menu)

        from PyQt6.QtGui import QKeySequence, QShortcut

        QShortcut(QKeySequence.StandardKey.Copy, self.table,
                  activated=self.copy_selection)

        self._timer = QTimer(self)
        self._timer.setInterval(FOLLOW_MS)
        self._timer.timeout.connect(self._poll)


    # ---- opening --------------------------------------------------------

    def _build_open_menu(self) -> None:
        """The logs present on this machine, plus Browse.

        Rebuilt rather than cached: a ConfigMgr client can be installed, and
        CBS rolls into new archives, between one opening of the menu and the
        next.
        """
        from .known_logs import known_logs, newest_cbs_archive

        self.open_menu.clear()
        for log in known_logs():
            self.open_menu.addAction(
                log.label, lambda _c=False, p=log.path: self.open(p))
        archive = newest_cbs_archive()
        if archive:
            self.open_menu.addSeparator()
            self.open_menu.addAction(
                "CBS — newest rolled archive",
                lambda _c=False, p=archive: self.open(p))
        largest = largest_cbs_archive()
        if largest and largest != archive:
            # The newest archive is routinely the smallest -- 15 MB here
            # against the 363 MB one two days older that holds the actual
            # servicing history.
            self.open_menu.addAction(
                "CBS — largest archive on disk",
                lambda _c=False, p=largest: self.open(p))
        recent = [path for path in getattr(self, "_recent", [])
                  if os.path.exists(path)]
        if recent:
            # A log can be rolled away between sessions; offering a path that
            # is gone is offering an error message.
            self.open_menu.addSeparator()
            for path in recent:
                # A folder path can end in a separator, and basename() of
                # "C:\\Logs\\CBS\\" is the empty string.
                label = os.path.basename(path.rstrip(os.sep + "/")) or path
                if os.path.isdir(path):
                    label = f"{label}  (folder)"
                self.open_menu.addAction(
                    label, lambda _c=False, p=path: self.open_recent(p))
        self.open_menu.addSeparator()
        self.open_menu.addAction("Browse…", self.choose_file)
        self.open_menu.addAction("Open every log in a folder…",
                                 self.choose_folder)
        self.open_menu.addAction("Open a folder and its subfolders…",
                                 self.choose_folder_tree)

    def choose_file(self) -> None:
        path, _filter = QFileDialog.getOpenFileName(
            self, "Open log", "",
            self.FILE_FILTER)
        if path:
            self.open(path)

    def open(self, path: str) -> None:
        self.open_paths([path])

    def open_paths(self, paths, remember_as: str = None) -> None:
        """Open one log, or several read as a single timeline.

        `remember_as` is what goes in the recent list: a folder is remembered
        as the folder, not as the dozen files it happened to contain.
        """
        opened = []
        for path in paths:
            if is_zip(path):
                members, problem = extract_zip(path)
                if problem:
                    self.status.setText(f"Could not open that bundle: "
                                        f"{problem}")
                    continue
                for member in members:
                    self._extracted[member] = path
                opened.extend(members)
                continue
            if not is_cab(path):
                opened.append(path)
                continue
            extracted, problem = extract_cab(path)
            if problem:
                self.status.setText(f"Could not open that cabinet: {problem}")
                continue
            # Remembered under the CAB's name: that is the file the user
            # chose and the one that will still be there tomorrow. The
            # extraction is a temporary copy.
            self._extracted[extracted] = path
            opened.append(extracted)
        if paths and not opened:
            return
        self._paths = opened
        entry = remember_as or (paths[0] if paths else "")
        # Only a FOLDER is watched for newcomers. Opening one file is not a
        # request to open its neighbours.
        self._watched_folder = remember_as if (
            remember_as and os.path.isdir(remember_as)) else ""
        if entry:
            self._recent = remember(self._recent, entry, cap=RECENT_CAP)
            save_recent(self._config, self._recent)
            self._build_open_menu()
        self._path = self._paths[0] if self._paths else ""
        self.reload()

    def open_folder(self, folder: str) -> None:
        """Every log sitting directly in `folder`, as one timeline."""
        found = LogSet.logs_in_folder(folder)
        if not found:
            self.model.clear()
            self.detail.clear()
            self._set = None
            self._paths = []
            self._path = ""
            self.status.setText(
                f"No logs (*.log, *.lo_) directly in {folder}")
            return
        self.open_paths(found, remember_as=folder)

    def open_recent(self, path: str) -> None:
        """Reopen something from the recent list, file or folder."""
        if os.path.isdir(path):
            self.open_folder(path)
        else:
            self.open(path)

    def dragEnterEvent(self, event) -> None:
        # Chained only on the path we do NOT handle: QWidget's default
        # implementation calls event.ignore(), which would undo the
        # acceptProposedAction above it and break the drop entirely.
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
        else:
            super().dragEnterEvent(event)

    def dragMoveEvent(self, event) -> None:
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
        else:
            super().dragMoveEvent(event)

    def dropEvent(self, event) -> None:
        """Open whatever was dropped: a log, several logs, or a folder.

        A drop that contains nothing openable leaves the current log alone
        and says so. Replacing it with an empty pane would lose the thing
        the person was reading in order to report a mistake.
        """
        # normpath because QUrl hands back forward slashes on Windows. The
        # recent list deduplicates by string, so without this one file would
        # sit in it twice under two spellings.
        paths = [os.path.normpath(url.toLocalFile())
                 for url in event.mimeData().urls() if url.isLocalFile()]
        if not paths:
            return
        event.acceptProposedAction()

        if len(paths) == 1 and os.path.isdir(paths[0]):
            self.open_folder(paths[0])
            return

        logs = [path for path in paths
                if os.path.isfile(path)
                and path.lower().endswith(LOG_SUFFIXES)]
        if not logs:
            self.status.setText(
                "Dropped no log files (*.log, *.lo_ are what this opens).")
            return
        self.open_paths(logs)
        event.acceptProposedAction()

    def choose_folder_tree(self) -> None:
        """Walk a whole tree, then show what was found before opening any of
        it.

        Separate from the flat open on purpose. `C:/Windows/Logs` holds 90
        logs across twelve subfolders, one of them 84.5 MB; opening all of
        that because someone pointed at the parent is the accident this
        exists to prevent.
        """
        folder = QFileDialog.getExistingDirectory(
            self, "Open logs from a folder and its subfolders")
        if not folder:
            return
        found, capped = LogSet.logs_under(folder)
        if not found:
            self.status.setText(
                f"No logs (*.log, *.lo_) anywhere under {folder}")
            return
        dialog = FolderPickDialog(folder, found, preselected(found), capped,
                                  self)
        if dialog.exec():
            chosen = dialog.chosen()
            if chosen:
                self.open_paths(chosen, remember_as=folder)

    def choose_folder(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "Open every log in a folder")
        if folder:
            self.open_folder(folder)

    def reload(self) -> None:
        """Start again from the beginning of the current file(s)."""
        if not self._paths:
            return
        self.model.clear()
        self.detail.clear()
        # Pinned rows belong to the log they came from; carrying them into a
        # different file would show records that are not in it.
        self._pinned = []
        self._refresh_pinned()
        self._set = LogSet(self._paths,
                           max_bytes=self._max_bytes,
                           include_rolled=self.rolled.isChecked(),
                           cap=self.model._entries.maxlen)
        self._poll(scroll=True)
        self._refresh_components()
        self._refresh_threads()
        self._refresh_sources()
        self._reset_range()
        self._sync_paging_buttons()
        # Columns that would be blank: Source with one log open, Package in
        # a log that names none. Cached because has_packages() scans records
        # and _apply_column_choice runs on every menu toggle.
        self._auto_hidden = set()
        if len(self._paths) < 2:
            self._auto_hidden.add("Source")
        if not self.model.has_packages():
            self._auto_hidden.add("Package")
        self._apply_column_choice()
        self._cap_package_width()
        # The refreshes above blockSignals() around rebuilding the Component
        # and Thread combos, so falling back to index 0 ("All") when the
        # previous log's selection is not in the new one never told the
        # model -- it kept filtering on a component/thread that belonged to
        # the log that is no longer open. One unblocked call here is what
        # guarantees the widgets and the model agree. Safe to combine with
        # _reset_range just above: _range_active is False at this point, so
        # this does not re-introduce the frozen-range bug that flag exists
        # to prevent.
        self._apply_filters()

    def _poll(self, scroll: bool = None) -> None:
        if self._set is None:
            return
        self._pick_up_new_logs()
        try:
            entries = self._set.read_new()
        except Exception as exc:                    # noqa: BLE001
            logger.warning("Could not read %s", self._path, exc_info=True)
            self.status.setText(f"Could not read the log: {exc}")
            return
        if entries:
            self._append_in_chunks(entries)
            self.provider.set_entries(self.model._entries,
                                      os.path.basename(self._path))
        if scroll or (scroll is None and self.follow.isChecked()):
            self.table.scrollToBottom()
        self._update_status()

    def cancel_load(self) -> None:
        """Stop taking in the rest of the current log."""
        self._cancelled = True

    def _append_in_chunks(self, entries) -> None:
        """Add records a slice at a time, letting the window breathe.

        Below one chunk this is exactly the old behaviour -- one append, no
        progress, no event pumping -- so the common case pays nothing.

        `processEvents` between slices is what makes Cancel possible without
        a worker: the click is delivered during the load rather than after
        it.
        """
        from PyQt6.QtWidgets import QApplication

        entries = list(entries)
        if len(entries) <= self.CHUNK:
            self.model.append(entries)
            return

        self._cancelled = False
        self._cancelled_at = None
        total = len(entries)
        for start in range(0, total, self.CHUNK):
            if self._cancelled:
                # Recorded rather than written straight to the status line:
                # `_poll` calls `_update_status()` immediately after this
                # and would overwrite the message, so the user would cancel
                # and be told nothing had happened.
                self._cancelled_at = (self.model.total, total)
                return
            self.model.append(entries[start:start + self.CHUNK])
            done = min(start + self.CHUNK, total)
            self.progress_reported.emit(int(100 * done / total))
            self.status.setText(
                f"Reading {os.path.basename(self._path)}… "
                f"{100 * done // total}%")
            QApplication.processEvents()

    def _pick_up_new_logs(self) -> None:
        """Add any log that has appeared in the watched folder.

        Only while following: this costs a directory listing per tick, and
        someone who is not following is not waiting for anything to arrive.
        A folder that has been removed is not an error -- it is a folder that
        has been removed.
        """
        if not self._watched_folder or not self.follow.isChecked():
            return
        if self._set is None:
            return
        try:
            present = LogSet.logs_in_folder(self._watched_folder)
        except OSError:
            return
        for path in present:
            if path not in self._set.paths:
                logger.info("A new log appeared while following: %s", path)
                self._set.add_source(path)
                self._paths = list(self._set.paths)

    # ---- walking backwards ----------------------------------------------

    def load_earlier(self) -> None:
        """Step every truncated source back once and rebuild the timeline.

        With one log this is the sliding window of chunk 3. With several it
        cannot be a prepend: one source's earlier chunk is older than that
        source's own loaded part but not necessarily older than what is
        already loaded from another, so the set is re-merged and replaces the
        model's contents whole. `keep_oldest` makes the cap drop from the
        newest end either way, which is the same window sliding backwards.

        Both the button and the scroll bar come through here, so the lock
        lives here too.
        """
        if self._loading_earlier or self._set is None:
            return
        if not self._set.has_earlier():
            return

        self._loading_earlier = True
        try:
            try:
                merged = self._set.read_earlier()
            except Exception as exc:                # noqa: BLE001
                logger.warning("Could not read earlier of %s", self._path,
                               exc_info=True)
                self.status.setText(f"Could not read the log: {exc}")
                return
            if not merged:
                self._sync_paging_buttons()
                return

            # Following means "stay at the end", and the end is what the cap
            # has just dropped. Appending live lines onto a slice they are
            # not contiguous with fabricates a timeline, which is worse than
            # not following at all.
            if self.follow.isChecked():
                self.follow.setChecked(False)

            anchor = self._top_entry_index()
            before = self.model.total
            self.model.replace(merged, keep_oldest=True)
            self.provider.set_entries(self.model._entries,
                                      os.path.basename(self._path))
            self._restore_viewport(anchor, max(self.model.total - before, 0))
            self._refresh_components()
            self._refresh_threads()
            self._refresh_sources()
            self._refresh_range_bounds()
        finally:
            self._loading_earlier = False
        self._sync_paging_buttons()
        self._update_status()

    def go_to_newest(self) -> None:
        """Back to the end of the file, where new lines land."""
        self.reload()

    def _on_scrolled(self, value: int) -> None:
        """Reaching the top asks for what comes before it."""
        if value > self.table.verticalScrollBar().minimum():
            return
        self.load_earlier()

    def _top_entry_index(self) -> Optional[int]:
        """Which record the view is currently showing at its top.

        Kept as an ENTRY index rather than a row: a prepend shifts every row
        number by the size of the chunk, and this is the thing that has to
        survive that.
        """
        row = self.table.rowAt(0)
        if row < 0:
            row = self.table.verticalScrollBar().value()
        return self.model.entry_index(row)

    def _restore_viewport(self, anchor: Optional[int], prepended: int) -> None:
        """Put the record the user was reading back under their eyes.

        Not a nicety: without it the view stays at row 0 after the prepend,
        which immediately re-fires the auto-load and walks the window back
        through the whole file from one flick of the wheel.

        Eviction happens at the far end and shifts nothing, so the anchor's
        new entry index is exactly its old one plus the chunk size.
        """
        if anchor is None:
            return
        row = self.model.row_for_entry(anchor + prepended)
        if row < 0:
            return
        self.table.scrollTo(self.model.index(row, MESSAGE),
                            QAbstractItemView.ScrollHint.PositionAtTop)

    def apply_layout(self, layout) -> None:
        """Restore how the pane was arranged, if anything was stored.

        Each key is applied only if it is present, so a partial or
        partially-rejected layout leaves the defaults alone rather than
        resetting them to something arbitrary.
        """
        if not layout:
            return
        if "fold" in layout:
            self.fold.setChecked(layout["fold"])
        if "regex" in layout:
            self.regex_box.setChecked(layout["regex"])
        if "splitter" in layout:
            self.splitter.setSizes(layout["splitter"])
        if "hidden_columns" in layout:
            self._hidden_columns = {name for name in layout["hidden_columns"]
                                    if name in COLUMNS
                                    and name not in self.ALWAYS_SHOWN}
            self._apply_column_choice()

    def current_layout(self) -> dict:
        return {
            "fold": self.fold.isChecked(),
            "regex": self.regex_box.isChecked(),
            "splitter": list(self.splitter.sizes()),
            "hidden_columns": sorted(self._hidden_columns),
        }

    def save_layout_now(self) -> None:
        save_layout(self._config, self.current_layout())

    def _build_shortcuts(self) -> None:
        """Keyboard access to the things done constantly.

        Every binding carries a modifier or is a function key, deliberately.
        A `QShortcut` takes precedence over the widget that has focus, so a
        bare `/` or `n` would be stolen from the Find, Filter, Hide and
        thread boxes the moment someone typed one -- the shortcut would fire
        and the character would never arrive.
        """
        self._shortcuts = []
        for sequence, slot in (
                ("Ctrl+F", lambda: self._focus(self.find_box)),
                ("Ctrl+L", lambda: self._focus(self.filter_box)),
                ("Ctrl+H", lambda: self._focus(self.exclude_box)),
                ("Ctrl+D", self.toggle_bookmark),
                ("Ctrl+P", self.toggle_pin),
                ("F3", self.find_next),
                ("Shift+F3", self.find_previous),
        ):
            shortcut = QShortcut(QKeySequence(sequence), self)
            shortcut.setContext(
                Qt.ShortcutContext.WidgetWithChildrenShortcut)
            shortcut.activated.connect(slot)
            self._shortcuts.append(shortcut)

    @staticmethod
    def _focus(box) -> None:
        box.setFocus()
        box.selectAll()

    def _remember_filter(self) -> None:
        """Put the committed pattern at the front of the history."""
        self._filter_history = remember(self._filter_history,
                                        self.filter_box.text())
        self._history_model.setStringList(self._filter_history)
        save_history(self._config, self._filter_history)

    # ---- the summary panel ----------------------------------------------

    def _on_summary_toggled(self, shown: bool) -> None:
        self.summary_panel.setVisible(shown)
        if shown:
            self._refresh_summary()

    def _schedule_summary(self) -> None:
        """Recount once the filtering goes quiet.

        Only while the panel is open: `top_codes` costs 297 ms over the real
        archive's 138,683 records, and a panel nobody is looking at must not
        charge that to every keystroke.
        """
        if self.summary_button.isChecked():
            self._summary_timer.start()

    def _refresh_summary(self) -> None:
        """Recount over what the filter left.

        `visible_entries()` rather than everything loaded, so the panel
        answers "what is in what I am looking at" and moves with the filter.
        It ignores folding, which is a reading convenience and no business of
        a count.
        """
        if not self.summary_button.isChecked():
            return
        entries = self.visible_entries()
        self.summary_codes.clear()
        for code, count in top_codes(entries):
            self.summary_codes.addItem(f"0x{code:08x}   {count:,}")
        self.summary_components.clear()
        for name, count in top_components(entries):
            self.summary_components.addItem(f"{name}   {count:,}")
        self.summary_messages.clear()
        # Normalised: verbatim, a real CBS log's most repeated line occurs
        # 589 times of 138,683 because every line names a different package.
        # Grouped, the top 200 forms cover 95% of the records.
        for message, count in top_messages(entries, key=normalise):
            self.summary_messages.addItem(f"{count:,}   {message[:120]}")
        self.summary_failure.clear()
        failed = first_error(entries)
        if failed is None:
            self.summary_failure.addItem("No errors in what is shown")
        else:
            self._add_record(self.summary_failure, "first error",
                             entries[failed])
            worked = last_success_before(entries, failed)
            if worked is None:
                self.summary_failure.addItem(
                    "last success: nothing before it")
            else:
                self._add_record(self.summary_failure, "last success",
                                 entries[worked])
        self.summary_bookmarks.clear()
        marked = self.model.bookmarks()
        if not marked:
            self.summary_bookmarks.addItem("Nothing bookmarked (Ctrl+D)")
        for entry in marked:
            self._add_record(self.summary_bookmarks, "★", entry)
        self.summary_sessions.clear()
        found = sessions(entries)
        if not found:
            self.summary_sessions.addItem("No servicing sessions here")
        for session in found:
            self._add_record(self.summary_sessions, session.label(),
                             entries[session.start])
        self.summary_gaps.clear()
        for index, seconds in gaps(entries):
            # The RECORD is kept, not the index: gaps are counted over
            # `visible_entries()`, which ignores folding, so those positions
            # are not row numbers and clicking one would land anywhere.
            self._add_record(self.summary_gaps, f"{seconds:,.0f}s",
                             entries[index])

    def _on_summary_code(self, item) -> None:
        self.filter_box.setText(item.text().split(" ")[0])

    def _on_summary_component(self, item) -> None:
        self.set_components({item.text().rsplit("   ", 1)[0]})

    #: How many lines either side an error or a peeked row shows.
    CONTEXT_LINES = 3

    def _on_context_toggled(self, on: bool) -> None:
        self.model.set_error_context(self.CONTEXT_LINES if on else None)
        self._update_status()

    def peek_around_current(self) -> None:
        """Reveal the rows around the current one, without clearing the
        filter. Pressing it again closes the peek."""
        if self.model.is_peeking():
            self.model.peek(None, 0)
            self._update_status()
            return
        entry = self.model.entry(self.table.currentIndex().row())
        if entry is None:
            return
        self.model.peek(entry, self.CONTEXT_LINES)
        self._update_status()

    # ---- saving a whole investigation -------------------------------------

    def current_view(self) -> View:
        """Everything needed to put this pane back as it is now."""
        return View(
            sources=list(self._paths),
            needle=self.filter_box.text(),
            exclude=self.exclude_box.text(),
            regex=self.regex_box.isChecked(),
            levels=[level for level, box in self._level_boxes.items()
                    if box.isChecked()],
            components=sorted(self.selected_components()),
            thread=self.thread.currentText(),
            log=self.source.currentText(),
            time_from=self.time_from.dateTime().toString(
                "yyyy-MM-dd HH:mm:ss") if self._range_active else "",
            time_to=self.time_to.dateTime().toString(
                "yyyy-MM-dd HH:mm:ss") if self._range_active else "",
            fold=self.fold.isChecked(),
            hidden_columns=sorted(self._hidden_columns),
        )

    def apply_view(self, view: View) -> None:
        """Reopen the logs and restore every axis.

        Missing sources are NAMED. Opening the rest quietly would present a
        partial investigation as a whole one.
        """
        gone = view.missing()
        present = [path for path in view.sources if path not in gone]
        if present:
            self.open_paths(present)
        self._hidden_columns = {name for name in view.hidden_columns
                                if name not in self.ALWAYS_SHOWN}
        self._apply_column_choice()
        self.filter_box.setText(view.needle)
        self.exclude_box.setText(view.exclude)
        self.regex_box.setChecked(view.regex)
        self.fold.setChecked(view.fold)
        wanted = set(view.levels)
        for level, box in self._level_boxes.items():
            box.setChecked(level in wanted if wanted else True)
        self.set_components(set(view.components))
        if gone:
            names = ", ".join(os.path.basename(path) for path in gone)
            self.status.setText(f"These logs are no longer there: {names}")

    def save_view_to(self, path: str) -> str:
        problem = save_view(path, self.current_view())
        if problem:
            self.status.setText(f"Could not save the view: {problem}")
        else:
            self.status.setText(f"View saved to {os.path.basename(path)}.")
        return problem

    def load_view_from(self, path: str) -> str:
        view, problem = load_view(path)
        if problem:
            self.status.setText(f"Could not open that view: {problem}")
            return problem
        self.apply_view(view)
        return ""

    def choose_save_view(self) -> None:
        path, _filter = QFileDialog.getSaveFileName(
            self, "Save this view", "", "Saved views (*.json)")
        if path:
            self.save_view_to(path)

    def choose_load_view(self) -> None:
        path, _filter = QFileDialog.getOpenFileName(
            self, "Open a saved view", "", "Saved views (*.json)")
        if path:
            self.load_view_from(path)

    def _provenance(self) -> list:
        """What each open log is, and how much of it was actually read."""
        sources = []
        for index, path in enumerate(self._paths):
            shown = self._extracted.get(path, path)
            try:
                size = os.path.getsize(path)
            except OSError:
                size = 0
            loaded = size
            if self._set is not None and index < len(self._set._readers):
                reader = self._set._readers[index]
                # window_start() is how much sits BEFORE what was read, so
                # anything non-zero means the file was opened at its tail.
                loaded = max(size - reader.window_start(), 0)
            name = os.path.basename(shown)
            stamps = [e.timestamp for e in self.model._entries
                      if e.raw.get("log", name) == name
                      and e.timestamp != UNKNOWN_TIME]
            sources.append({
                "name": name, "path": shown, "size": size, "loaded": loaded,
                "first": min(stamps) if stamps else None,
                "last": max(stamps) if stamps else None,
            })
        return sources

    def _filters_in_force(self) -> dict:
        """Only the axes actually narrowing the view, named as the UI names
        them."""
        active = {}
        if self.filter_box.text():
            active["Filter"] = self.filter_box.text()
        if self.exclude_box.text():
            active["Hide"] = self.exclude_box.text()
        off = [level for level, box in self._level_boxes.items()
               if not box.isChecked()]
        if off:
            active["Severity"] = "hiding " + ", ".join(sorted(off))
        chosen = self.selected_components()
        if chosen:
            active["Component"] = ", ".join(sorted(chosen))
        if self.thread.currentText() not in ("", "All"):
            active["Thread"] = self.thread.currentText()
        if self.source.currentText() not in ("", "All"):
            active["Source"] = self.source.currentText()
        if self._range_active:
            active["Time range"] = (
                f"{self.time_from.dateTime().toString('yyyy-MM-dd HH:mm:ss')}"
                f" .. "
                f"{self.time_to.dateTime().toString('yyyy-MM-dd HH:mm:ss')}")
        if self.context_box.isChecked():
            active["Showing"] = "errors and their surrounding lines"
        return active

    def evidence_bundle(self) -> str:
        """The visible rows with everything needed to judge what they are."""
        from .log_export import as_evidence

        return as_evidence(self.visible_entries(), self._provenance(),
                           self._filters_in_force(),
                           self.model.rowCount(), self.model.total)

    def write_evidence_to(self, path: str) -> str:
        try:
            with open(path, "w", encoding="utf-8", newline="") as handle:
                handle.write(self.evidence_bundle())
        except OSError as problem:
            self.status.setText(f"Could not write the bundle: {problem}")
            return str(problem)
        self.status.setText(f"Evidence written to {os.path.basename(path)}.")
        return ""

    def choose_evidence(self) -> None:
        path, _filter = QFileDialog.getSaveFileName(
            self, "Save an evidence bundle", "", "Text (*.txt)")
        if path:
            self.write_evidence_to(path)

    def compare_with(self, path: str) -> str:
        """This log's steps against another file's, in the log's own words.

        Aligned on what happened rather than on when: two machines never
        share a clock. Reads the other file with its own reader so its
        encoding is sniffed independently.
        """
        from .log_reader import LogReader

        try:
            text = LogReader(path).read_new()
        except OSError as problem:
            return f"Could not read {os.path.basename(path)}: {problem}"
        other = cmtrace_parser.parse(text)
        if not other:
            return f"{os.path.basename(path)} held no records to compare."
        return compare(self.visible_entries(), other).as_text()

    def choose_compare(self) -> None:
        path, _filter = QFileDialog.getOpenFileName(
            self, "Compare against another log", "", self.FILE_FILTER)
        if not path:
            return
        from PyQt6.QtWidgets import QPlainTextEdit

        dialog = QDialog(self)
        dialog.setWindowTitle(f"This log vs {os.path.basename(path)}")
        dialog.resize(760, 460)
        layout = QVBoxLayout(dialog)
        report = QPlainTextEdit(dialog)
        report.setReadOnly(True)
        report.setFont(QFont("Consolas", 9))
        report.setPlainText(self.compare_with(path))
        layout.addWidget(report)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close,
                                   dialog)
        buttons.rejected.connect(dialog.reject)
        buttons.accepted.connect(dialog.accept)
        layout.addWidget(buttons)
        dialog.exec()

    def search_unloaded(self) -> list:
        """Scan the part of the file the window never covered.

        "No match" over a 32 MB window on a 380 MB archive is a statement
        about memory, not about the log -- and it is believed. This searches
        the rest, and reports where the hits are so they can be reached.
        """
        needle = self.find_box.text().strip()
        if not needle or self._set is None or not self._paths:
            return []
        window_start = self._set.earlier_bytes()
        if not window_start:
            self.status.setText(
                "The whole file is loaded — there is nothing else to search.")
            return []

        self.status.setText(f"Searching the {_size(window_start)} before the "
                            "loaded window…")
        hits = scan_file(self._paths[0], needle,
                         regex=self.regex_box.isChecked(),
                         end=window_start)
        if not hits:
            self.status.setText(
                f"{needle!r} is not in the {_size(window_start)} earlier in "
                "the file either.")
            return []
        first = hits[0]
        self.status.setText(
            f"{len(hits):,} match(es) earlier in the file, not loaded. "
            f"First at byte {first.offset:,}: {first.line[:80]}")
        return hits

    # ---- columns ---------------------------------------------------------

    #: Without it the table is metadata about lines you cannot read.
    ALWAYS_SHOWN = ("Message",)

    def set_column_hidden(self, name: str, hidden: bool) -> None:
        if name in self.ALWAYS_SHOWN or name not in COLUMNS:
            return
        if hidden:
            self._hidden_columns.add(name)
        else:
            self._hidden_columns.discard(name)
        self._apply_column_choice()

    def _apply_column_choice(self) -> None:
        """Set every column's visibility from the whole truth, in one place.

        A column is hidden if the reader turned it off OR if it would be a
        column of blanks -- Source with one log open, Package in a log that
        names none. Computing it here rather than only ever ADDING hiding is
        what lets a column be turned back ON: an add-only pass could hide but
        never reveal.

        The auto rules are cached by `reload`, because `has_packages()` scans
        the records and this runs on every menu toggle.
        """
        for index, name in enumerate(COLUMNS):
            hidden = (name in self._hidden_columns
                      or name in self._auto_hidden)
            self.table.setColumnHidden(index, hidden)

    def build_column_menu(self) -> QMenu:
        menu = QMenu(self)
        for name in COLUMNS:
            if name in self.ALWAYS_SHOWN:
                continue
            action = menu.addAction(name)
            action.setCheckable(True)
            action.setChecked(name not in self._hidden_columns)
            action.toggled.connect(
                lambda shown, n=name: self.set_column_hidden(n, not shown))
        return menu

    def _on_header_menu(self, point) -> None:
        header = self.table.horizontalHeader()
        self.build_column_menu().exec(header.mapToGlobal(point))

    # ---- presets ---------------------------------------------------------

    def _build_preset_menu(self) -> None:
        self.preset_menu.clear()
        for preset in list(BUILT_IN) + list(self._presets):
            self.preset_menu.addAction(
                preset.name,
                lambda _c=False, p=preset: self.apply_preset(p))
        self.preset_menu.addSeparator()
        self.preset_menu.addAction("Save current view as…",
                                   self.choose_preset_name)
        self.preset_menu.addSeparator()
        self.preset_menu.addAction("Save investigation to a file…",
                                   self.choose_save_view)
        self.preset_menu.addAction("Open an investigation…",
                                   self.choose_load_view)
        self.preset_menu.addAction("Save an evidence bundle…",
                                   self.choose_evidence)
        self.preset_menu.addAction("Compare against another log…",
                                   self.choose_compare)

    def apply_preset(self, preset) -> None:
        """Set every axis the preset names, and CLEAR the ones it does not.

        A preset is a whole view, not a patch: leaving yesterday's exclude in
        place would give a result neither the preset nor the user asked for,
        and it would be attributed to the preset.
        """
        self.filter_box.setText(preset.needle)
        self.exclude_box.setText(preset.exclude)
        self.regex_box.setChecked(preset.regex)
        wanted = set(preset.levels)
        for level, box in self._level_boxes.items():
            box.setChecked(level in wanted if wanted else True)
        self._apply_filters()

    def current_preset(self, name: str):
        return Preset(name=name,
                      needle=self.filter_box.text(),
                      exclude=self.exclude_box.text(),
                      levels=tuple(level for level, box
                                   in self._level_boxes.items()
                                   if box.isChecked())
                      if not all(b.isChecked()
                                 for b in self._level_boxes.values())
                      else (),
                      regex=self.regex_box.isChecked())

    def save_current_preset(self, name: str) -> None:
        if not name.strip():
            return
        preset = self.current_preset(name.strip())
        self._presets = [p for p in self._presets if p.name != preset.name]
        self._presets.append(preset)
        save_presets(self._config, self._presets)
        self._build_preset_menu()
        self.status.setText(f"Saved the current view as {preset.name!r}.")

    def choose_preset_name(self) -> None:
        from PyQt6.QtWidgets import QInputDialog

        name, chosen = QInputDialog.getText(self, "Save view",
                                            "Name for this view:")
        if chosen:
            self.save_current_preset(name)

    def toggle_pin(self) -> None:
        """Keep the current row on screen, or stop keeping it.

        Held as RECORDS, like bookmarks: rows shift on every filter change
        and entry indices shift on every "load earlier".
        """
        entry = self.model.entry(self.table.currentIndex().row())
        if entry is None:
            return
        if any(held is entry for held in self._pinned):
            self._pinned = [held for held in self._pinned if held is not entry]
        else:
            self._pinned.append(entry)
        self._refresh_pinned()

    def _refresh_pinned(self) -> None:
        """Rebuild the strip, in the order the records appear in the log."""
        order = {id(entry): index
                 for index, entry in enumerate(self.model._entries)}
        self._pinned.sort(key=lambda entry: order.get(id(entry), 0))
        self.pinned_model.clear()
        self.pinned_model.append(list(self._pinned))
        self.pinned_table.setVisible(bool(self._pinned))
        for column in range(self.pinned_model.columnCount()):
            self.pinned_table.setColumnHidden(
                column, self.table.isColumnHidden(column))
            self.pinned_table.setColumnWidth(
                column, self.table.columnWidth(column))

    def toggle_bookmark(self) -> None:
        """Mark or unmark the current row.

        An investigation is a loop of "that line and this line", and the only
        way back to a row was to remember what it said.
        """
        entry = self.model.entry(self.table.currentIndex().row())
        if entry is None:
            return
        self.model.toggle_bookmark(entry)
        self._refresh_summary()

    def _add_record(self, listing, label: str, entry) -> None:
        """A summary row that remembers WHICH RECORD it is about.

        The record, never its position: these lists are built over
        `visible_entries()`, which ignores folding, so an index from it is
        not a row number and clicking one would land anywhere.
        """
        item = QListWidgetItem(f"{label}: {entry.message[:110]}")
        item.setData(Qt.ItemDataRole.UserRole, entry)
        listing.addItem(item)

    def _on_summary_gap(self, item) -> None:
        """Go to the first record after the silence."""
        entry = item.data(Qt.ItemDataRole.UserRole)
        if entry is None:
            return
        row = self.model.row_for_record(entry)
        if row < 0:
            return
        index = self.model.index(row, MESSAGE)
        self.table.setCurrentIndex(index)
        self.table.scrollTo(index,
                            QAbstractItemView.ScrollHint.PositionAtCenter)

    def _on_summary_message(self, item) -> None:
        self.filter_box.setText(item.text().split("   ", 1)[1])

    def _refresh_density(self) -> None:
        """Re-bucket over what the filter left.

        Cheap enough to run inline -- one pass, no regex -- unlike the
        Summary panel's counts, which is why this is not behind the debounce.
        A log with no timestamps anywhere gets no strip rather than an empty
        box: there is nothing to place on a timeline.
        """
        made = buckets(self.visible_entries())
        self.density.set_buckets(made)
        self.density.setVisible(bool(made))

    def _refresh_match_colours(self) -> None:
        """Tell the Message delegate what to pick out inside each line.

        Both boxes, because they answer different questions and both are
        worth seeing: Filter decides which rows survive, Find decides which
        row you are taken to. Neither is visible inside a 2,751px CBS message
        without this.

        A repaint, not a reset: which rows are VISIBLE has not changed, and a
        reset would clear the selection -- the trap `append` documents.
        """
        # Split the same way the filter does, or a multi-term filter would
        # match rows and highlight nothing in them: "package install" is
        # never present as typed. In regex mode the box IS one pattern,
        # spaces and all, so it goes through whole.
        if self.regex_box.isChecked():
            needles = [self.filter_box.text(), self.find_box.text()]
        else:
            needles = (split_terms(self.filter_box.text())
                       + split_terms(self.find_box.text()))
        self.message_delegate.set_needles(
            needles, regex=self.regex_box.isChecked())
        self.table.viewport().update()

    def _refresh_range_bounds(self) -> None:
        """Re-open the From/To boxes on the span that is loaded NOW.

        Deliberately not `_reset_range`, which also CLEARS the range: a range
        the user set is theirs and survives a load. When none is set the
        boxes are only a starting point, and one describing a slice three
        minutes wide after paging back through 300 MB is worse than useless
        -- nudging a box is how the range is turned on, so the first nudge
        would filter out everything that had just been loaded.

        The signals are blocked for the same reason `_reset_range` blocks
        them: `setDateTime` is indistinguishable from a user edit, and a user
        edit is what sets `_range_active`.
        """
        if self._range_active:
            return
        span = self.model.time_span()
        if not span:
            return
        for box, value in ((self.time_from, span[0]), (self.time_to, span[1])):
            box.blockSignals(True)
            box.setDateTime(QDateTime(value))
            box.blockSignals(False)

    def _sync_paging_buttons(self) -> None:
        earlier = self._set is not None and self._set.has_earlier()
        self.load_earlier_button.setEnabled(earlier)
        # "Newest" is only meaningful once the window has actually moved off
        # the end of the file.
        self.newest_button.setEnabled(bool(self.model.unloaded_newer))

    def _update_status(self) -> None:
        if not self._paths:
            self.status.setText("No log open.")
            return
        if len(self._paths) > 1:
            opened = f"{len(self._paths)} logs merged"
        else:
            shown = self._extracted.get(self._path, self._path)
            opened = os.path.basename(shown)
        parts = [opened,
                 f"{self.model.rowCount():,} shown of {self.model.total:,}"]
        if self.context_box.isChecked() and not self.model.rowCount():
            # Distinct from the ordinary "no rows match": the user asked for
            # errors and there are none, which is an answer worth having.
            parts.append("no errors in what is loaded")
        elif self.model.total and not self.model.rowCount():
            # An empty table reads as "this log has no such records", which
            # is a different statement from "your filter removed everything"
            # -- and the first one sends someone away believing the log is
            # clean. Only said when records ARE loaded: nothing loaded is a
            # third thing again, and not the filter's fault.
            parts.append("no rows match the current filter")
        if self.model.dropped:
            # Saying so matters: silently showing the last 200k lines of a
            # 900k-line log is how someone concludes the log is clean.
            parts.append(f"{self.model.dropped:,} older records dropped")
        folded = self.model.folded_count()
        if folded:
            # Same reason. These records are one click away rather than gone,
            # but the count still has to be visible.
            parts.append(f"{folded:,} continuation lines folded")
        if self._cancelled_at:
            taken, offered = self._cancelled_at
            parts.append(f"load cancelled — {taken:,} of {offered:,} "
                         "records taken in")
        if self.model.unloaded_newer:
            # The window slid backwards and the newest records went to make
            # room. They are one "Newest" click away rather than gone, but
            # showing a slice out of the middle of a file without saying so
            # is how someone concludes the log is clean.
            parts.append(f"{self.model.unloaded_newer:,} newer records "
                         "unloaded")
        earlier = self._earlier_bytes()
        if earlier:
            parts.append(f"{_size(earlier)} earlier in the file not loaded")
        self.status.setText("  |  ".join(parts))

    def _earlier_bytes(self) -> int:
        """How much of the file sits before the loaded window."""
        if self._set is None or not self._set.has_earlier():
            return 0
        return self._set.earlier_bytes()

    def _expand_row(self, row: int) -> None:
        """Let the selected row wrap, and let the previous one collapse.

        Both rows are resized explicitly: Qt only re-asks for a size hint
        when told to, and resizing the whole table would measure every row.
        """
        previous = self.message_delegate.expanded_row
        self.message_delegate.expanded_row = row
        for changed in {previous, row}:
            if 0 <= changed < self.model.rowCount():
                self.table.resizeRowToContents(changed)

    def _on_row_selected(self, current, _previous) -> None:
        """Show the whole selected record, and spell out its error codes.

        This used to overwrite the status line, which is where the file name
        and the record counts live -- so clicking a row threw away the one
        piece of text saying how much of the log was on screen.
        """
        self._expand_row(current.row() if current.isValid() else -1)
        entry = self.model.entry(current.row()) if current.isValid() else None
        if entry is None:
            self.detail.clear()
            return
        from .error_codes import explain

        stamp = self.model.data(self.model.index(current.row(), TIME)) or "—"
        head = [f"{stamp}   {entry.level}"]
        if entry.source:
            head.append(entry.source)
        thread = entry.raw.get("thread", "")
        if thread:
            head.append(f"thread {thread}")
        if entry.raw.get("continuation"):
            head.append("(continuation of the record above)")

        parts = ["   |   ".join(head), "", entry.message]
        found = explain(entry.message)
        if found:
            parts.append("")
            parts.extend(f"{code} — {meaning}" for code, meaning in found)
        self.detail.setPlainText("\n".join(parts))

    # ---- context menu and actions -------------------------------------------

    def anchor_range(self, row: int, minutes: int) -> None:
        """Set the time range to ±minutes around the timestamp of the given row."""
        entry = self.model.entry(row)
        if entry is None or entry.timestamp == UNKNOWN_TIME:
            return
        span = timedelta(minutes=minutes)
        self.time_from.blockSignals(True)
        self.time_to.blockSignals(True)
        self.time_from.setDateTime(QDateTime(entry.timestamp - span))
        self.time_to.setDateTime(QDateTime(entry.timestamp + span))
        self.time_from.blockSignals(False)
        self.time_to.blockSignals(False)
        self._range_active = True
        self._apply_filters()

    def choose_go_to(self) -> None:
        """Ask for a moment, then jump to it.

        Its own input rather than the From box: editing that box fires
        `dateTimeChanged`, which is what turns the range filter ON, so
        typing a time there would hide rows before the jump ever happened.
        """
        span = self.model.time_span()
        if not span:
            return
        dialog = QDialog(self)
        dialog.setWindowTitle("Go to time")
        layout = QVBoxLayout(dialog)
        layout.addWidget(QLabel("Scroll to the first record at or after:",
                                dialog))
        picker = QDateTimeEdit(dialog)
        picker.setDisplayFormat("yyyy-MM-dd HH:mm:ss")
        picker.setDateTime(QDateTime(span[0]))
        layout.addWidget(picker)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel, dialog)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        if dialog.exec():
            self.go_to_time(picker.dateTime().toPyDateTime())

    def go_to_time(self, when=None) -> None:
        """Scroll to the first row at or after `when`.

        Deliberately does NOT set `_range_active`: a jump is not a filter,
        and switching the range on here would hide the surrounding rows that
        are the entire reason for jumping rather than filtering.
        """
        if when is None:
            when = self.time_from.dateTime().toPyDateTime()
        row = self.model.row_at_or_after(when)
        if row < 0:
            return
        index = self.model.index(row, MESSAGE)
        self.table.setCurrentIndex(index)
        self.table.scrollTo(index,
                            QAbstractItemView.ScrollHint.PositionAtCenter)

    def _on_range_edited(self, _new_value) -> None:
        """A genuine user edit of either box -- see the comment on the
        connect() calls. This is what turns the range on."""
        self._range_active = True
        self._apply_filters()

    def build_row_menu(self, row: int) -> QMenu:
        """Build the context menu for a row."""
        menu = QMenu(self)
        for minutes in self.RANGE_MINUTES:
            menu.addAction(
                f"Show ±{minutes} minute{'s' if minutes > 1 else ''} "
                f"around this row",
                lambda _c=False, m=minutes: self.anchor_range(row, m))
        menu.addSeparator()
        menu.addAction("Look up the error codes on this row",
                       lambda _c=False: self.open_error_lookup(row))
        menu.addAction("Peek at the lines around this row",
                       self.peek_around_current)
        menu.addAction("Bookmark this row  (Ctrl+D)", self.toggle_bookmark)
        menu.addAction("Pin this row  (Ctrl+P)", self.toggle_pin)
        menu.addAction("Copy selected rows", self.copy_selection)
        menu.addAction("Copy selected rows as Markdown",
                       self.copy_selection_as_markdown)
        return menu

    def _on_context_menu(self, point) -> None:
        """Handle right-click on the table."""
        index = self.table.indexAt(point)
        if index.isValid():
            self.build_row_menu(index.row()).exec(
                self.table.viewport().mapToGlobal(point))

    def open_error_lookup(self, row: int = -1) -> None:
        from .error_lookup_dialog import ErrorLookupDialog

        if self._lookup is None:
            self._lookup = ErrorLookupDialog(self)
        entry = self.model.entry(row) if row >= 0 else None
        self._lookup.show_for(entry.message if entry is not None else "")

    def edit_highlight_rules(self) -> None:
        from .highlight import save_rules
        from .highlight_dialog import HighlightDialog

        dialog = HighlightDialog(self._rules, self)
        if dialog.exec():
            self._rules = dialog.rules()
            self.model.set_highlight_rules(self._rules)
            if self._config is not None:
                save_rules(self._config, self._rules)

    def edit_match_colours(self) -> None:
        from .match_colour_dialog import MatchColourDialog
        from .match_colours import save_colours

        dialog = MatchColourDialog(self._match_colours, self)
        if dialog.exec():
            self.set_match_colours(dialog.chosen())
            if self._config is not None:
                save_colours(self._config, self._match_colours)

    def set_match_colours(self, colours) -> None:
        """Repaint in `colours`. The table is told to redraw because the
        colour lives in the delegate, not in the model -- nothing the model
        holds changed, so it emits nothing and the view would keep the old
        paint until something else moved."""
        from .match_colours import _clean

        self._match_colours = _clean(colours)
        self.message_delegate.set_colours(self._match_colours)
        self.table.viewport().update()

    def _on_fold_toggled(self, folded: bool) -> None:
        self.model.set_folding(folded)
        self._update_status()

    def _sync_fold_box(self) -> None:
        """Keep the checkbox honest: `find` unfolds to reach a match, and a
        box that still read "folded" would be lying about the view."""
        if self.fold.isChecked() != self.model.is_folding():
            self.fold.blockSignals(True)
            self.fold.setChecked(self.model.is_folding())
            self.fold.blockSignals(False)

    def visible_entries(self) -> list:
        """What the filter left, in view order -- folding deliberately ignored.

        A folded continuation is hidden for reading, not excluded by the
        user, so an export that dropped it would lose a tenth of a real CBS
        archive without saying so. Every other filter still applies.
        """
        return self.model.rows_for_export()

    def _selected_entries(self) -> list:
        """The records under the selection, falling back to the current row.

        Shared by both copy actions so they can never disagree about what
        "the selection" is.
        """
        rows = sorted({index.row()
                       for index in self.table.selectionModel().selectedRows()}
                      or {self.table.currentIndex().row()})
        return [self.model.entry(row) for row in rows
                if self.model.entry(row) is not None]

    def copy_selection(self) -> None:
        """Copy selected rows to clipboard without provenance header."""
        entries = self._selected_entries()
        if not entries:
            return
        from .log_export import as_text
        from PyQt6.QtWidgets import QApplication

        QApplication.clipboard().setText(as_text(entries, header=False))
        self.status.setText(f"{len(entries):,} row(s) copied.")

    def copy_selection_as_markdown(self) -> None:
        """The selected rows as a table that pastes into a ticket."""
        from .log_export import as_markdown
        from PyQt6.QtWidgets import QApplication

        entries = self._selected_entries()
        if not entries:
            return
        QApplication.clipboard().setText(as_markdown(entries))
        self.status.setText(f"{len(entries):,} row(s) copied as Markdown.")

    def export_to(self, path: str) -> None:
        """Export the filtered view to a file (CSV or text).

        Writes to a temporary file beside the target, then atomically replaces
        the destination. This ensures the destination is never truncated or
        left in a partial state if the write fails.
        """
        import tempfile
        from .log_export import as_csv, as_html, as_markdown, as_text

        entries = self.visible_entries()
        lowered = path.lower()
        if lowered.endswith(".csv"):
            text = as_csv(entries)
        elif lowered.endswith((".html", ".htm")):
            text = as_html(entries)
        elif lowered.endswith(".md"):
            text = as_markdown(entries)
        else:
            text = as_text(entries)

        # Create a temporary file in the same directory as the target to ensure
        # os.replace() stays on the same volume (atomic on Windows).
        target_dir = os.path.dirname(path)
        if not target_dir:
            target_dir = "."

        temp_fd = None
        temp_path = None
        try:
            # Create temp file in the same directory as the target
            temp_fd, temp_path = tempfile.mkstemp(
                dir=target_dir,
                prefix=".export_",
                suffix=os.path.splitext(path)[1])

            # Write to the temp file
            with os.fdopen(temp_fd, "w", encoding="utf-8", newline="") as handle:
                handle.write(text)

            # Atomically replace the target with the temp file
            os.replace(temp_path, path)
        except OSError as exc:
            logger.warning("Could not export to %s", path, exc_info=True)
            self.status.setText(f"Could not write the export: {exc}")
            # Clean up the temp file if it was created
            if temp_path is not None:
                try:
                    os.unlink(temp_path)
                except OSError:
                    # Best-effort cleanup; the original export failure above
                    # is already reported, and this is not another one.
                    logger.debug("Could not remove temp export file %s",
                                temp_path, exc_info=True)
            return

        self.status.setText(f"{len(entries):,} row(s) written to "
                            f"{os.path.basename(path)}")

    def choose_export(self) -> None:
        """Open a file dialog to choose where to export the filtered view."""
        path, _filter = QFileDialog.getSaveFileName(
            self, "Export the filtered view", "",
            "Text (*.txt);;CSV (*.csv);;HTML (*.html);;Markdown (*.md)")
        if path:
            self.export_to(path)

    # ---- filtering and find ---------------------------------------------

    def component_values(self) -> list:
        """Every component the menu offers, in menu order."""
        return [action.text() for action in self._component_actions]

    def selected_components(self) -> set:
        return {action.text() for action in self._component_actions
                if action.isChecked()}

    def set_components(self, chosen) -> None:
        """Tick exactly `chosen` and apply it."""
        self._populating_components = True
        try:
            for action in self._component_actions:
                action.setChecked(action.text() in chosen)
        finally:
            self._populating_components = False
        self._on_components_changed()

    def _on_components_changed(self) -> None:
        if self._populating_components:
            return
        chosen = self.selected_components()
        if not chosen:
            label = "Component: All"
        elif len(chosen) <= 2:
            label = "Component: " + ", ".join(sorted(chosen))
        else:
            # A button that grows with the selection shoves every control to
            # its right along the toolbar.
            label = f"Component: {len(chosen)} components"
        self.component_button.setText(label)
        self._apply_filters()

    def _cap_package_width(self) -> None:
        """Let Package size to its content, then stop it taking the table.

        Sized to contents it measures the widest package in the WHOLE model,
        not the widest on screen, so one 62-character name gave a column that
        was empty on every visible row and half as wide as the message.
        """
        header = self.table.horizontalHeader()
        if self.table.isColumnHidden(PACKAGE):
            return
        header.setSectionResizeMode(PACKAGE,
                                    QHeaderView.ResizeMode.Interactive)
        header.resizeSection(
            PACKAGE, min(header.sectionSize(PACKAGE), PACKAGE_MAX_WIDTH))

    def _refresh_components(self) -> None:
        """Rebuild the menu for the components now loaded.

        A tick only survives if its component is still present. Keeping one
        that belongs to a log which is no longer open is the stale-filter
        shape that has bitten this pane twice -- the control would read
        "CSI" while the model filtered on a component the new log has never
        heard of, and the table would be empty with nothing to explain it.
        """
        kept = self.selected_components()
        self._populating_components = True
        try:
            self.component_menu.clear()
            self._component_actions = []
            for name in self.model.components():
                action = self.component_menu.addAction(name)
                action.setCheckable(True)
                action.setChecked(name in kept)
                action.toggled.connect(
                    lambda _c=False: self._on_components_changed())
                self._component_actions.append(action)
            if self._component_actions:
                self.component_menu.addSeparator()
                self.component_menu.addAction(
                    "Show all", lambda: self.set_components(set()))
        finally:
            self._populating_components = False
        self._on_components_changed()

    def _refresh_threads(self) -> None:
        current = self.thread.currentText()
        self.thread.blockSignals(True)
        self.thread.clear()
        self.thread.addItem("All")
        for thread, count in self.model.threads():
            self.thread.addItem(f"{thread}  ({count:,})")
        index = self.thread.findText(current)
        self.thread.setCurrentIndex(max(0, index))
        self.thread.blockSignals(False)

    def _refresh_sources(self) -> None:
        """Rebuild the Source combo for the logs now open.

        Blocked while rebuilding, like the Component and Thread combos -- and
        for the same reason `reload` documents: falling back to "All" without
        telling the model leaves it filtering on a file that is no longer
        open, while the combo claims otherwise.
        """
        current = self.source.currentText()
        self.source.blockSignals(True)
        self.source.clear()
        self.source.addItem("All")
        for name in self.model.logs():
            self.source.addItem(name)
        index = self.source.findText(current)
        self.source.setCurrentIndex(max(0, index))
        self.source.blockSignals(False)
        several = len(self._paths) > 1
        self.source_label.setVisible(several)
        self.source.setVisible(several)

    def _reset_range(self) -> None:
        """Open the boxes on the whole log, and stop filtering by time."""
        span = self.model.time_span()
        for box, value in ((self.time_from, span[0] if span else None),
                           (self.time_to, span[1] if span else None)):
            box.blockSignals(True)
            if value is not None:
                box.setDateTime(QDateTime(value))
            box.blockSignals(False)
        self._range_active = False
        self.model.set_filter(time_from=False, time_to=False)
        self._update_status()

    def _apply_filters(self) -> None:
        checked = {level for level, box in self._level_boxes.items()
                   if box.isChecked()}
        # All boxes ticked means no filtering at all, rather than a set that
        # happens to list every level -- Debug records would vanish otherwise.
        levels = set() if checked == set(LEVELS) else checked
        thread = self.thread.currentText()
        thread = "" if thread == "All" else thread.split(" ")[0]
        # The boxes always hold a value -- the log's whole span, or
        # whatever anchor_range/a manual edit set -- but that must reach
        # the model only once the user has actually asked for a range.
        # Otherwise every OTHER control (Filter, a severity box, Component,
        # Thread, Regex) re-sends time_from/time_to on every call, undoing
        # "Clear range", and while following, the upper bound is frozen at
        # the moment the log was opened so newer lines never appear.
        time_from: object = False
        time_to: object = False
        if self._range_active:
            # A backwards range would hide every row, and an empty table
            # reads as "no such records" -- a lie about the log rather than
            # a complaint about the range. Say so and filter nothing.
            start = self.time_from.dateTime().toPyDateTime()
            end = self.time_to.dateTime().toPyDateTime()
            if start > end:
                self.model.set_filter(time_from=False, time_to=False)
                self.status.setText("That time range ends before it starts.")
                return
            time_from, time_to = start, end
        self.model.set_filter(
            levels=levels,
            needle=self.filter_box.text(),
            exclude=self.exclude_box.text(),
            component=self.selected_components(),
            thread=thread,
            log="" if self.source.currentText() == "All"
                else self.source.currentText(),
            time_from=time_from,
            time_to=time_to,
            regex=self.regex_box.isChecked())
        if self.model.filter_pattern_is_invalid():
            self.status.setText("That pattern is not finished yet.")
            self._refresh_match_colours()
            return
        if self.model.exclude_pattern_is_invalid():
            # Nothing is hidden until the pattern is finished, which is the
            # opposite of what an unfinished INCLUDE pattern does -- so say
            # which box is unfinished rather than just "that pattern".
            self.status.setText("That Hide pattern is not finished yet.")
            return
        self._refresh_match_colours()
        self._refresh_density()
        self._schedule_summary()
        self._update_status()

    def find_next(self) -> None:
        self._find(forwards=True)

    def find_previous(self) -> None:
        self._find(forwards=False)

    def _find(self, forwards: bool) -> None:
        needle = self.find_box.text().strip()
        if not needle:
            return
        start = self.table.currentIndex().row()
        if start < 0:
            start = 0 if forwards else self.model.rowCount()
        row = self.model.find(needle, start_row=start, forwards=forwards)
        # `find` unfolds to reach a match it would otherwise have missed, so
        # the checkbox has to be told what happened to it.
        self._sync_fold_box()
        if row < 0:
            # Only about what is LOADED. If more of the file is behind the
            # window, say so rather than letting "No match" be read as a
            # statement about the file.
            extra = ""
            if self._set is not None and self._set.has_earlier():
                extra = (f" {_size(self._set.earlier_bytes())} earlier in "
                         "the file is not loaded — press Search earlier.")
            self.status.setText(f"No match for {needle!r} in what is "
                                f"loaded.{extra}")
            return
        index = self.model.index(row, MESSAGE)
        self.table.setCurrentIndex(index)
        self.table.scrollTo(index, QAbstractItemView.ScrollHint.PositionAtCenter)

    # ---- following ------------------------------------------------------

    def _on_follow_toggled(self, following: bool) -> None:
        if following and self._path:
            self._timer.start()
        else:
            self._timer.stop()

    def stop(self) -> None:
        self._timer.stop()
        # Saved here rather than on every drag: the splitter emits while it
        # is being moved, and writing the config file on each pixel would be
        # a lot of disk for a preference.
        self.save_layout_now()


class LogViewerModule(BaseModule):
    name = "Log Viewer"
    icon = "📄"
    description = "CMTrace-style viewer for ConfigMgr and plain text logs"
    requires_admin = False
    group = ModuleGroup.DIAGNOSE

    def __init__(self) -> None:
        super().__init__()
        self._widget: Optional[LogViewerWidget] = None
        self._provider = LogSearchProvider()

    def create_widget(self) -> QWidget:
        self._widget = LogViewerWidget()
        self._provider = self._widget.provider
        config = getattr(self.app, "config", None) if self.app else None
        if config is not None:
            from .highlight import load_rules
            from .match_colours import load_colours

            self._widget._config = config
            self._widget._rules = load_rules(config)
            self._widget.model.set_highlight_rules(self._widget._rules)
            self._widget.set_match_colours(load_colours(config))
            # The filter history is only worth saving if it is read back.
            self._widget._filter_history = load_history(config)
            self._widget._history_model.setStringList(
                self._widget._filter_history)
            self._widget._recent = load_recent(config)
            self._widget._build_open_menu()
            self._widget.apply_layout(load_layout(config))
            self._widget._presets = load_presets(config)
            self._widget._build_preset_menu()
        return self._widget

    def get_search_provider(self) -> Optional[SearchProvider]:
        return self._provider

    def on_deactivate(self) -> None:
        # Following holds a timer and re-reads a file nobody is looking at.
        if self._widget is not None:
            self._widget.stop()

    def on_stop(self) -> None:
        self.on_deactivate()

    def get_status_info(self) -> str:
        if self._widget is None or not self._widget._path:
            return "No log open"
        return (f"{os.path.basename(self._widget._path)} — "
                f"{self._widget.model.total:,} records")
