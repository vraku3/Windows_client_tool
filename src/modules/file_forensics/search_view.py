"""Compact search chrome and detail layout, using Cleanup's shared path and QSS conventions."""
import os
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (QCheckBox, QComboBox, QFileDialog, QGridLayout, QHBoxLayout,
    QLabel, QLineEdit, QPushButton, QSplitter, QStackedWidget, QTableView, QTextBrowser,
    QVBoxLayout, QWidget, QListWidget, QAbstractItemView, QDialog, QDialogButtonBox,
    QTableWidget, QTableWidgetItem, QHeaderView, QSpinBox)
from core.table_ui import set_role
from ui.empty_state import EmptyState
from ui.elided_path import ElidedPathLabel
from modules.cleanup.cleanup_scanner.known_folders import known_folder
from .limit_controls import LimitControls
from .results_model import ResultsModel, ResultsFilter
from ui.glance import GlanceTile, GlanceStrip

FILTERS = (('all', 'All'), ('locked', 'Locked'), ('unsigned', 'Unsigned creator'),
           ('executables', 'Executables'), ('hour', 'Last hour'), ('day', 'Last 24 h'))


_FilterTile = GlanceTile


class SearchView(LimitControls):
    def _build_search_tab(self):
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(0, 4, 0, 0)
        layout.addLayout(self._build_toolbar())
        self._progress_label = QLabel('Choose a folder and Search. No files are changed.')
        self._progress_label.setWordWrap(True)
        set_role(self._progress_label, 'statusInfo')
        layout.addWidget(self._progress_label)
        layout.addWidget(self._build_tiles())
        layout.addLayout(self._build_chips())
        splitter = QSplitter(Qt.Orientation.Horizontal)
        self._results_stack = QStackedWidget()
        self._table = self._build_table()
        self._results_stack.addWidget(self._table)
        self._empty = EmptyState('search', 'No results yet', 'Search a folder such as %TEMP%; add a name to narrow the search.')
        self._results_stack.addWidget(self._empty)
        self._results_stack.setCurrentIndex(1)
        splitter.addWidget(self._results_stack)
        splitter.addWidget(self._build_detail())
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([800, 300])
        layout.addWidget(splitter, 1)
        return tab

    def _build_toolbar(self):
        bar = QGridLayout()
        self._folder_edit = QLineEdit(os.environ.get('TEMP', ''))
        self._folder_edit.setToolTip('Folder to inspect. Environment variables such as %TEMP% are accepted. Enter starts a search.')
        self._folder_edit.returnPressed.connect(self._on_search_clicked)
        self._browse_btn = QPushButton('Browse…')
        self._browse_btn.setToolTip('Choose a folder to inspect; no files are changed.')
        self._browse_btn.clicked.connect(self._browse)
        self._places = QComboBox()
        self._places.setToolTip('Choose a common folder. Downloads and Desktop use the real Windows known folder, including redirection.')
        self._places.addItem('Common places', '')
        for name in ('%TEMP%', r'C:\Windows\Temp', 'Downloads', 'Desktop', '%LOCALAPPDATA%', '%APPDATA%', '%ProgramData%'):
            self._places.addItem(name, name)
        self._places.activated.connect(self._choose_place)
        self._filter_edit = QLineEdit()
        self._filter_edit.setPlaceholderText('Name contains…')
        self._filter_edit.setToolTip('Match part of a filename, ignoring case; leave blank for all names. Enter starts the search.')
        self._filter_edit.returnPressed.connect(self._on_search_clicked)
        self._recurse_cb = QCheckBox('Recurse')
        self._recurse_cb.setChecked(True)
        self._recurse_cb.setToolTip('Include subfolders. Junctions and directory links are skipped and counted to prevent loops.')
        self._search_btn = QPushButton('Search')
        self._search_btn.setToolTip('Search reads metadata only, then checks readable open handles once. While running, click Stop to retain partial results.')
        self._search_btn.clicked.connect(self._on_search_clicked)
        for col, widget in enumerate((QLabel('Folder'), self._folder_edit, self._browse_btn, self._places)):
            bar.addWidget(widget, 0, col)
        for col, widget in enumerate((QLabel('Name'), self._filter_edit, self._recurse_cb, self._search_btn)):
            bar.addWidget(widget, 1, col)
        self._add_search_filters(bar)
        self._build_limit_controls(bar)
        bar.setColumnStretch(1, 1)
        return bar

    def _add_search_filters(self, bar):
        self._extensions_edit = QLineEdit()
        self._extensions_edit.setPlaceholderText('exe,dll,ps1 (blank = all)')
        self._extensions_edit.setToolTip('Comma-separated extensions, ignoring case. Applied during search before the chosen match limit.')
        self._extensions_edit.returnPressed.connect(self._on_search_clicked)
        bar.addWidget(QLabel('Extensions'), 2, 0)
        bar.addWidget(self._extensions_edit, 2, 1, 1, 3)
        for row, attr, label, maximum, tip in (
                (3, '_created_days', 'Created within days', 36500, 'Keep files created within this many days; 0 means any creation time.'),
                (4, '_modified_days', 'Modified within days', 36500, 'Keep files modified within this many days; 0 means any modification time.'),
                (5, '_min_size', 'Minimum size (KiB)', 2147483647, 'Keep files at least this many KiB (1,024 bytes); 0 means any size. Applied before the match limit.')):
            control = QSpinBox()
            control.setRange(0, maximum)
            control.setSpecialValueText('Any')
            control.setToolTip(tip)
            setattr(self, attr, control)
            bar.addWidget(QLabel(label), row, 0)
            bar.addWidget(control, row, 1, 1, 3)

    def _browse(self):
        folder = QFileDialog.getExistingDirectory(self._widget, 'Choose folder', self._folder_edit.text())
        if folder:
            self._folder_edit.setText(folder)

    def _choose_place(self, index):
        name = self._places.itemData(index)
        path = known_folder(name) if name in ('Downloads', 'Desktop') else os.path.expandvars(name)
        if path:
            self._folder_edit.setText(path)
        elif name:
            self._error_banner.set_error(f'Windows could not resolve {name}. Use Browse to choose the folder.')

    def _build_tiles(self):
        self._tiles = {}
        cards = []
        for key, label, tone in (('all', 'Files matched', 'total'), ('size', 'Total size', 'drive'),
                ('locked', 'Locked right now', 'review'), ('unsigned', 'Unsigned creator', 'blocked'),
                ('skipped', 'Skipped', 'review')):
            tile = _FilterTile(label, tone,
                ('Show unreadable paths and reasons, plus separate counts for unreadable items and skipped links.' if key == 'skipped' else
                 f'{label}: click or press Enter to filter. Unsigned creators count only rows whose signatures have been checked.'))
            tile.set('0', 'Click to filter')
            tile.clicked.connect(lambda k=key: self._filter_results('all' if k == 'size' else k))
            self._tiles[key] = (tile, label)
            cards.append(tile)
        strip = GlanceStrip(cards)
        strip._place(3)  # Start compact; the strip reflows to the real width on resize.
        return strip

    def _skip_summary(self):
        batch = self._last_batch
        if not batch:
            return f'{self._last_skip_count:,} skipped items.'
        return f'{batch.unreadable:,} items could not be read; {batch.skipped_links:,} links/reparse points skipped.'

    def _show_skipped(self):
        dialog = QDialog(self._widget)
        dialog.setWindowTitle('Skipped search items')
        dialog.resize(720, 360)
        layout = QVBoxLayout(dialog)
        batch = self._last_batch
        items = batch.unreadable_items if batch else ()
        label = QLabel(self._skip_summary() + '\nShowing the first 200 unreadable paths at most. Links have no result rows.')
        label.setWordWrap(True)
        layout.addWidget(label)
        table = QTableWidget(len(items), 2)
        table.setHorizontalHeaderLabels(['Path', 'Reason'])
        table.setToolTip('Unreadable search items and the error reported by Windows; limited to the first 200.')
        table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        for index, (path, reason) in enumerate(items):
            table.setItem(index, 0, QTableWidgetItem(path))
            table.setItem(index, 1, QTableWidgetItem(reason))
        layout.addWidget(table)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.button(QDialogButtonBox.StandardButton.Close).setToolTip('Close the skipped-items list and keep the current result filter.')
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        dialog.exec()

    def _build_chips(self):
        grid = QGridLayout()
        self._chips = {}
        for i, (key, label) in enumerate(FILTERS):
            button = QPushButton(f'{label} (0)')
            button.setObjectName('filterChip')
            button.setCheckable(True)
            button.setChecked(key == 'all')
            button.setToolTip(f'Show {label.lower()} among this search’s results. Counts update while searching; signatures are checked on demand.')
            button.clicked.connect(lambda checked=False, k=key: self._filter_results(k))
            grid.addWidget(button, i // 3, i % 3)
            self._chips[key] = (button, label)
        return grid

    def _build_table(self):
        table = QTableView()
        self._model = ResultsModel(table)
        self._proxy = ResultsFilter(table)
        self._proxy.setSourceModel(self._model)
        table.setModel(self._proxy)
        table.setSortingEnabled(True)
        table.sortByColumn(0, Qt.SortOrder.AscendingOrder)
        table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        table.setTextElideMode(Qt.TextElideMode.ElideMiddle)
        table.setAlternatingRowColors(True)
        table.verticalHeader().hide()
        table.setAccessibleName('File Forensics search results')
        table.setToolTip('Select a file for details. Click a heading to sort; hover a heading for its meaning.')
        table.selectionModel().selectionChanged.connect(lambda *_: self._on_row_selected())
        for i, width in enumerate((140, 135, 80, 130, 130, 110, 120, 105)):
            table.setColumnWidth(i, width)
        table.setMinimumWidth(0)
        return table

    def _build_detail(self):
        pane = QWidget()
        pane.setObjectName('detailPane')
        pane.setMinimumWidth(0)
        layout = QVBoxLayout(pane)
        self._path_label = ElidedPathLabel('Select a file')
        layout.addWidget(self._path_label)
        self._detail_label = QTextBrowser()
        self._detail_label.setAccessibleName('File metadata, open handles and creator evidence')
        layout.addWidget(self._detail_label, 1)
        buttons = (('_signature_btn', 'Check signatures', self._check_signatures,
                    'Verify this file and its creator candidates, only for the selected row. Cloud placeholders are never opened.'),
                   ('_reputation_btn', 'Check reputation', self._check_reputation,
                    'Compute the selected file’s SHA-256 and query VirusTotal with the configured API key. No file is uploaded.'),
                   ('_reveal_btn', 'Reveal in Explorer', self._on_reveal_clicked, 'Open Explorer with the selected file highlighted.'),
                   ('_copy_btn', 'Copy path', self._on_copy_path_clicked, 'Copy the selected full path to the clipboard.'),
                   ('_kill_btn', 'End locking process', self._on_kill_locking_clicked, 'Choose one locking process to end immediately; protected processes cannot be selected. Unsaved work may be lost. Confirmation and process identity checks are required.'),
                   ('_export_btn', 'Export CSV', self._on_export_clicked, 'Save all search results as CSV, including rows hidden by filters.'))
        grid = QGridLayout()
        for i, (attr, label, handler, tip) in enumerate(buttons):
            button = QPushButton(label)
            button.setToolTip(tip)
            button.clicked.connect(handler)
            button.setEnabled(attr == '_export_btn')
            setattr(self, attr, button)
            grid.addWidget(button, i, 0)
        layout.addLayout(grid)
        return pane

    def _build_watch_tab(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        info = QLabel('Watch the search folder for new files. Creator candidates are guesses from process start times. Leaving this pane stops watching.')
        info.setWordWrap(True)
        layout.addWidget(info)
        self._watch_cb = QCheckBox('Live Watch: off / on')
        self._watch_cb.setToolTip('Watch for newly created files in the search folder. No files are changed. Switch off to stop immediately.')
        self._watch_cb.toggled.connect(self._on_watch_toggled)
        layout.addWidget(self._watch_cb)
        self._ignore_edit = QLineEdit('*.tmp;~$*')
        self._ignore_edit.setToolTip('Editable ignore patterns, separated by semicolons; * matches any characters. Applies to new detections.')
        self._ignore_edit.textChanged.connect(self._update_ignore_chips)
        layout.addWidget(self._ignore_edit)
        self._ignore_chips = QHBoxLayout()
        layout.addLayout(self._ignore_chips)
        self._update_ignore_chips()
        self._watch_feed = QListWidget()
        self._watch_feed.setAccessibleName('Live feed of new files and likely creators')
        layout.addWidget(self._watch_feed, 1)
        return page

    def _update_ignore_chips(self):
        self._watch_patterns = tuple(self._ignore_patterns())
        while self._ignore_chips.count():
            item = self._ignore_chips.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        for pattern in self._watch_patterns:
            button = QPushButton(pattern + ' ×')
            button.setToolTip(f'Remove the ignore pattern {pattern}; future matching files will appear in the feed.')
            button.clicked.connect(lambda checked=False, p=pattern: self._ignore_edit.setText(';'.join(x for x in self._ignore_patterns() if x != p)))
            self._ignore_chips.addWidget(button)
        self._ignore_chips.addStretch()
