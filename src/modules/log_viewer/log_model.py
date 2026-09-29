"""The table model behind the log viewer.

A `QAbstractTableModel` over a capped deque, not `QTreeWidgetItem`s: a
ConfigMgr log runs to hundreds of thousands of records, and a widget item per
row does not survive that.

Filtering keeps a separate index list rather than copying entries, so turning
"Errors only" on and off costs a pass over integers and never touches the
records themselves.
"""
import re
from bisect import bisect_left
from collections import deque
from typing import Optional, Tuple

from PyQt6.QtCore import QAbstractTableModel, QModelIndex, Qt
from PyQt6.QtGui import QColor

from .cmtrace_parser import UNKNOWN_TIME
from .highlight import haystack, matching_rule
from .log_export import format_stamp
from .packages import package_of
from .palette import component_colour, readable_text_on, severity_row_colour

#: Source is the FILE a record came from; Component is `entry.source`, which
#: is the subsystem inside that file. Two different questions, so two columns.
#: The Source column is always defined -- the pane hides it while a single log
#: is open -- because letting the column count change under the delegate would
#: shift MESSAGE's index at runtime.
COLUMNS = ("Time", "Source", "Severity", "Component", "Package", "Thread",
           "Message")
TIME, SOURCE, SEVERITY, COMPONENT, PACKAGE, THREAD, MESSAGE = range(
    len(COLUMNS))

#: How many records are kept. Past this the oldest go, which is what CMTrace
#: does in practice and what keeps a 300 MB log openable.
DEFAULT_CAP = 200_000

#: What "next issue" means by default -- the two levels an admin scanning a
#: log actually cares about, not Debug/verbose noise.
ISSUE_LEVELS = frozenset({"Error", "Warning"})


def split_terms(text: str) -> list:
    """A filter box's text as the terms it means, AS TYPED.

    Case is preserved because the delegate colours what it is given and the
    model lowercases when it compares; lowercasing here would make the
    highlighting disagree with the box.

    Space separated and ANDed, because "these two words, either order" is the
    everyday case and a regex is the wrong tool for it. A quoted phrase is
    one term, so a phrase with a space in it is still expressible.

    An unmatched quote is a half-typed phrase, not a syntax error: the rest
    of the text is taken as ordinary terms rather than the filter refusing.
    Empty terms are dropped -- one would match every row and silently widen
    the filter back out.
    """
    terms = []
    for index, chunk in enumerate((text or "").split('"')):
        if index % 2:
            # Inside quotes: one term, spaces and all.
            phrase = chunk.strip()
            if phrase:
                terms.append(phrase)
        else:
            terms.extend(word for word in chunk.split() if word)
    return terms


def _as_selection(value) -> frozenset:
    """A filter axis as a set of accepted values.

    Takes a bare string as well as a set, because every existing caller
    passes a string and one axis reading differently from its neighbour is
    how a filter quietly stops meaning what it says.

    An empty string OR an empty set both mean "no opinion, show everything".
    Treating an empty set as "accept nothing" would leave someone staring at
    an empty table having ticked nothing.
    """
    if isinstance(value, str):
        return frozenset([value]) if value else frozenset()
    return frozenset(entry for entry in value if entry)


class LogModel(QAbstractTableModel):
    def __init__(self, parent=None, cap: int = DEFAULT_CAP) -> None:
        super().__init__(parent)
        self._entries = deque(maxlen=cap)
        self._visible: list = []
        self._levels = set()            # empty means "show everything"
        self._needle = ""
        self._pattern = ""
        self._terms: list = []
        #: The inverse of the needle: rows matching this are REMOVED. A real
        #: CBS.log is mostly `Appl: detectParent` and `Plan: Package`, and no
        #: positive filter drops that boilerplate without dropping the rest.
        self._exclude = ""
        self._exclude_terms: list = []
        self._exclude_matcher = None
        self._component = frozenset()
        self._thread = frozenset()
        self._log = ""
        self._time_from = None
        self._time_to = None
        self._regex = False
        self._matcher = None            # compiled once per set_filter
        self._rules = []                # highlight rules
        self._fold = True               # continuations folded under parents
        self._fold_counts = {}          # entry index -> continuations under it
        self._folded = set()            # entry indices folded under a parent
        self.dropped = 0                # records aged out of the cap
        #: Records pushed off the NEWEST end to make room for an earlier
        #: chunk. The opposite end to `dropped`, and a different thing to
        #: tell the user: these were loaded a moment ago and are one "Newest"
        #: click away, rather than having scrolled out of a growing file.
        self.unloaded_newer = 0
        #: Bookmarked RECORDS, held two ways. The id set makes the lookup in
        #: `data()` O(1) -- it runs per painted cell -- and the reference
        #: list is what keeps those ids valid: without it a record could be
        #: freed and its id handed to a different object, silently marking
        #: the wrong row.
        #:
        #: Never row or entry INDICES. Rows shift whenever the filter
        #: changes and entry indices shift by the size of every "load
        #: earlier" chunk, so either would quietly come to mean a different
        #: record.
        self._bookmark_ids: set = set()
        self._bookmark_refs: list = []
        #: "Show rows near an anchor". `None` means off. Two features share
        #: it: errors-with-context anchors on every Error, peek anchors on
        #: one record you picked.
        self._context_lines = None
        self._peek_ref = None
        self._peek_lines = 0

    # ---- bookmarks ------------------------------------------------------

    def toggle_bookmark(self, entry) -> None:
        if entry is None:
            return
        if id(entry) in self._bookmark_ids:
            self._bookmark_ids.discard(id(entry))
            self._bookmark_refs = [held for held in self._bookmark_refs
                                   if held is not entry]
        else:
            self._bookmark_ids.add(id(entry))
            self._bookmark_refs.append(entry)
        self._repaint_visible()

    def is_bookmarked(self, entry) -> bool:
        return entry is not None and id(entry) in self._bookmark_ids

    def bookmarks(self) -> list:
        """Bookmarked records still loaded, in view order.

        A record evicted by the cap has nothing to point at, so it drops out
        rather than lingering as a row that goes nowhere.
        """
        loaded = {id(entry) for entry in self._entries}
        return [entry for entry in self._entries
                if id(entry) in self._bookmark_ids and id(entry) in loaded]

    # ---- content --------------------------------------------------------

    def append(self, entries) -> None:
        """Add parsed records, keeping the newest when the cap is reached.

        INSERTS rather than resets. A reset clears the view's selection, and
        while following this runs every second -- click a row to read it and
        it deselects under you a tick later.

        A reset is still right in one case: once the cap starts dropping from
        the front, every surviving record's index shifts, and an insert would
        leave the view pointing at the wrong rows.
        """
        if not entries:
            return
        entries = list(entries)
        capacity = self._entries.maxlen
        overflow = 0
        if capacity is not None:
            overflow = max(0, len(self._entries) + len(entries) - capacity)

        if overflow:
            self.dropped += overflow
            self.beginResetModel()
            self._entries.extend(entries)
            self._reindex()
            self.endResetModel()
            return

        first = len(self._entries)
        # Extend BEFORE deciding which rows appear: folding is decided per
        # index, and a new continuation raises the count on a parent that is
        # already on screen, which can only be recomputed once it is in.
        self._entries.extend(entries)
        self._recount_folds()
        fresh = [first + offset for offset, entry in enumerate(entries)
                 if self._shows(first + offset, entry)]
        if not fresh:
            # Everything new is hidden by the current filter: the records go
            # in and no row appears -- but a folded parent's count may have
            # risen, so the rows already on screen still need repainting.
            self._repaint_visible()
            return
        start = len(self._visible)
        self.beginInsertRows(QModelIndex(), start, start + len(fresh) - 1)
        self._visible.extend(fresh)
        self.endInsertRows()
        self._repaint_visible()

    def prepend(self, entries) -> None:
        """Put an EARLIER chunk in front of what is loaded.

        The mirror of `append`, and the model half of "load earlier". Where
        `append` lets the cap drop the oldest records, this lets it evict the
        newest: the window slides backwards through a file far larger than
        the cap could ever hold.

        Unlike `append` this RESETS rather than inserting. `_visible`,
        `_folded` and `_fold_counts` are all keyed by entry index and every
        one of them shifts by len(entries), so there is nothing to preserve;
        `_reindex` costs 0.05s over 134,527 records, against the ~1.2s parse
        that produced the chunk. The reset clears the selection -- the trap
        `append` exists to avoid -- which is acceptable only because a
        prepend is something the user deliberately asked for, and the pane
        restores the viewport afterwards.
        """
        if not entries:
            return
        entries = list(entries)
        capacity = self._entries.maxlen
        if capacity is not None and len(entries) > capacity:
            # `extendleft` would keep this chunk's OLDEST end and discard the
            # seam along with everything already loaded, leaving a window
            # with a hole in the middle of it and no sign that it happened.
            raise ValueError(
                f"cannot prepend {len(entries):,} records to a model capped "
                f"at {capacity:,}")
        if capacity is not None:
            self.unloaded_newer += max(
                0, len(self._entries) + len(entries) - capacity)

        self.beginResetModel()
        # extendleft REVERSES what it is given. Without the reversed() the
        # chunk goes in backwards -- every timestamp in it running the wrong
        # way, every row present and plausible, and nothing to say so.
        self._entries.extendleft(reversed(entries))
        self._reindex()
        self.endResetModel()

    def replace(self, entries, keep_oldest: bool = False) -> None:
        """Swap the whole contents for a finished list.

        The merged timeline's path. When several logs are open, "load
        earlier" cannot prepend: one source's earlier chunk is older than
        that source's own loaded part but not necessarily older than what is
        already loaded from another source, so the set is re-merged from
        scratch and handed here whole.

        `keep_oldest` says which end the cap trims. Loading earlier is a walk
        backwards, so what goes is the newest -- the same sliding-window
        semantics `prepend` has, counted into the same `unloaded_newer`.
        """
        entries = list(entries)
        capacity = self._entries.maxlen
        overflow = 0
        if capacity is not None and len(entries) > capacity:
            overflow = len(entries) - capacity
            if keep_oldest:
                self.unloaded_newer += overflow
                entries = entries[:capacity]
            else:
                self.dropped += overflow
                entries = entries[-capacity:]

        self.beginResetModel()
        self._entries.clear()
        self._entries.extend(entries)
        self._reindex()
        self.endResetModel()

    def clear(self) -> None:
        self.beginResetModel()
        self._entries.clear()
        self._visible = []
        self._fold_counts = {}
        self._folded = set()
        self.dropped = 0
        self.unloaded_newer = 0
        self._bookmark_ids = set()
        self._bookmark_refs = []
        self.endResetModel()

    def entry(self, row: int):
        if 0 <= row < len(self._visible):
            return self._entries[self._visible[row]]
        return None

    def entry_index(self, row: int):
        """Which RECORD a row is showing, as an index into the deque.

        A row number is only meaningful until the next prepend shifts every
        one of them; a record's index shifts by a known amount, so this is
        what an anchor across a load has to be kept as.
        """
        if 0 <= row < len(self._visible):
            return self._visible[row]
        return None

    def row_for_record(self, entry) -> int:
        """The row showing this exact record, or -1.

        By identity, not by index: callers that computed something over
        `rows_for_export()` hold a record, and that list ignores folding, so
        its positions are not row numbers. Turning one back into a row is
        this method's whole job.
        """
        for row, index in enumerate(self._visible):
            if self._entries[index] is entry:
                return row
        return -1

    def row_at_or_after(self, when) -> int:
        """The first VISIBLE row timestamped at or after `when`, or -1.

        A linear walk, deliberately, where `row_for_entry` next door uses a
        bisect. A bisect needs the column to be sorted, and log timestamps
        are not: `setupact.log` and `setuperr.log` both jump ten hours
        backwards at a setup phase boundary, and a merged set interleaves
        several clocks. A bisect over that answers confidently and wrongly.

        200,000 comparisons is a few milliseconds and this runs on a click,
        not on a keystroke.

        Records with no clock of their own are skipped rather than matched:
        landing on a continuation puts you inside a block with no way to tell
        where you are.
        """
        for row, index in enumerate(self._visible):
            entry = self._entries[index]
            if entry.timestamp == UNKNOWN_TIME:
                continue
            if entry.timestamp >= when:
                return row
        return len(self._visible) - 1 if self._visible else -1

    def row_for_entry(self, index: int) -> int:
        """The row showing record `index`, or the nearest one below it.

        `_visible` is ascending by construction, so this is a bisect rather
        than a scan -- it runs against 200,000 records. A record hidden by
        the current filter has no row of its own; the next visible one after
        it is the honest answer, and past the end means the record is no
        longer loaded at all.
        """
        if not self._visible:
            return -1
        row = bisect_left(self._visible, index)
        return row if row < len(self._visible) else len(self._visible) - 1

    @property
    def total(self) -> int:
        return len(self._entries)

    def components(self) -> list:
        return sorted({e.source for e in self._entries if e.source})

    # ---- filtering ------------------------------------------------------

    def set_filter(self, levels=None, needle: str = None,
                   component: str = None, thread: str = None,
                   time_from=None, time_to=None, regex: bool = None,
                   log: str = None, exclude: str = None) -> None:
        """Each argument left as None keeps that axis unchanged.

        `time_from`/`time_to` take the sentinel `False` to mean "clear this
        bound", since None already means "leave it alone" and a range has to
        be removable.
        """
        if levels is not None:
            self._levels = set(levels)
        if needle is not None:
            self._needle = needle.lower()
            self._pattern = needle
            self._terms = [term.lower()
                           for term in split_terms(needle)]
        if component is not None:
            self._component = _as_selection(component)
        if thread is not None:
            self._thread = _as_selection(thread)
        if log is not None:
            self._log = log
        if exclude is not None:
            self._exclude = exclude
            self._exclude_terms = [term.lower()
                                   for term in split_terms(exclude)]
        if time_from is not None:
            self._time_from = None if time_from is False else time_from
        if time_to is not None:
            self._time_to = None if time_to is False else time_to
        if regex is not None:
            self._regex = bool(regex)

        # Compiled ONCE here, never inside _matches: at 134,527 records a
        # per-row compile is 134,527 compiles per keystroke.
        self._matcher = None
        if self._regex and self._pattern:
            try:
                self._matcher = re.compile(self._pattern, re.IGNORECASE)
            except re.error:
                # A half-typed pattern is a typo. Nothing matches until it
                # is finished; the pane says so in the status bar.
                self._matcher = False

        # Compiled here for the same reason the include filter is: at
        # 134,527 records a per-row compile is 134,527 compiles per keystroke.
        self._exclude_matcher = None
        if self._regex and self._exclude:
            try:
                self._exclude_matcher = re.compile(self._exclude,
                                                   re.IGNORECASE)
            except re.error:
                # An unfinished exclude removes NOTHING. False here would
                # mean "hide everything", which empties the table over a
                # keystroke -- the opposite of what the include filter's
                # False does, and the reason the two are not shared.
                self._exclude_matcher = False

        self.beginResetModel()
        self._reindex()
        self.endResetModel()

    def set_highlight_rules(self, rules) -> None:
        """Colouring only -- which rows are VISIBLE does not change, so this
        repaints rather than resetting. A reset would clear the selection,
        the same trap `append` documents."""
        self._rules = list(rules or [])
        if self._visible:
            top = self.index(0, 0)
            bottom = self.index(len(self._visible) - 1, len(COLUMNS) - 1)
            self.dataChanged.emit(top, bottom,
                                  [Qt.ItemDataRole.BackgroundRole,
                                   Qt.ItemDataRole.ForegroundRole])

    def _matches(self, entry) -> bool:
        if self._levels and entry.level not in self._levels:
            return False
        if self._component and entry.source not in self._component:
            return False
        if self._thread and entry.raw.get("thread", "") not in self._thread:
            return False
        if self._log and entry.raw.get("log", "") != self._log:
            return False
        # A record with no timestamp of its own -- a continuation line -- is
        # never removed by a time filter. Losing a record is the one outcome
        # a log viewer must not produce.
        if entry.timestamp != UNKNOWN_TIME:
            if self._time_from and entry.timestamp < self._time_from:
                return False
            if self._time_to and entry.timestamp > self._time_to:
                return False
        if self._needle:
            # The whole ROW as the user sees it, not just the message --
            # the same `haystack()` a highlight rule is matched against, so
            # the filter and a rule can never quietly disagree on what
            # "the row" means. Typing "warning" has to find the warning row
            # of a CMTrace log, where the word lives in the type attribute
            # rather than the text.
            text = haystack(entry)
            if self._matcher is False:
                return False
            if self._matcher is not None:
                if not self._matcher.search(text):
                    return False
            elif not all(term in text.lower() for term in self._terms):
                return False
        if self._exclude and self._excluded_by(entry):
            return False
        return True

    def _excluded_by(self, entry) -> bool:
        """Whether the exclude pattern removes this row.

        Applied AFTER every include test, so exclude narrows what the filter
        left rather than being able to resurrect a row the filter dropped.
        Matched against the same `haystack()` the include filter and the
        highlight rules use, so the three can never disagree about what "the
        row" means.
        """
        if self._exclude_matcher is False:
            # Unfinished pattern: remove nothing.
            return False
        text = haystack(entry)
        if self._exclude_matcher is not None:
            return bool(self._exclude_matcher.search(text))
        lowered = text.lower()
        return bool(self._exclude_terms) and all(
            term in lowered for term in self._exclude_terms)

    def has_packages(self) -> bool:
        """Whether any loaded record names a package.

        Stops at the first hit, so on a servicing log -- where 92% of records
        carry one -- it answers immediately. Only a large log with NO
        packages pays the full scan, and those are the small ones.
        """
        return any(package_of(entry.message) for entry in self._entries)

    def logs(self) -> list:
        """The files currently represented, for the Source combo."""
        return sorted({e.raw.get("log", "") for e in self._entries
                       if e.raw.get("log")})

    def threads(self) -> list:
        """`(thread, count)` ordered by count descending.

        DISM carries 329 distinct thread ids; an alphabetical list of 329
        numbers is not a control anyone can use.
        """
        counts: dict = {}
        for entry in self._entries:
            thread = entry.raw.get("thread", "")
            if thread:
                counts[thread] = counts.get(thread, 0) + 1
        return sorted(counts.items(), key=lambda pair: (-pair[1], pair[0]))

    def time_span(self):
        """`(first, last)` real timestamp, or None. Used to prefill the range
        boxes so they open on the whole log rather than on the year 1752."""
        stamps = [e.timestamp for e in self._entries
                  if e.timestamp != UNKNOWN_TIME]
        if not stamps:
            return None
        return min(stamps), max(stamps)

    def exclude_pattern_is_invalid(self) -> bool:
        """The pane asks, so it can say so rather than leaving someone to
        wonder why nothing is being hidden."""
        return self._exclude_matcher is False

    def filter_pattern_is_invalid(self) -> bool:
        """The pane asks, so it can say so rather than showing an empty
        table that reads as "no such records"."""
        return self._matcher is False

    def _reindex(self) -> None:
        self._recount_folds()
        visible = [i for i, e in enumerate(self._entries)
                   if self._shows(i, e)]
        self._visible = self._widened(visible)

    def _widened(self, visible) -> list:
        """`visible` plus the rows around each anchor, if context is on.

        Context WIDENS what an anchor pulls in; it never overrides a filter
        the user set, so a neighbour excluded by the component or severity
        filter stays excluded. Windows are merged rather than concatenated:
        two errors three apart with a context of three would otherwise list
        the rows between them twice.
        """
        if self._context_lines is None and self._peek_ref is None:
            return visible

        allowed = set(visible)
        anchors = []
        if self._context_lines is not None:
            anchors = [index for index in visible
                       if self._entries[index].level == "Error"]
        if self._peek_ref is not None:
            anchors = [index for index, entry in enumerate(self._entries)
                       if entry is self._peek_ref]

        peeking = self._peek_ref is not None
        reach = self._peek_lines if peeking else self._context_lines
        kept = set()
        for anchor in anchors:
            low = max(anchor - reach, 0)
            high = min(anchor + reach, len(self._entries) - 1)
            for index in range(low, high + 1):
                # The two features differ here, deliberately.
                #
                # PEEK reveals its neighbours unconditionally: it is an
                # explicit, temporary "show me what this filter is hiding
                # around this one row", and honouring the filter would
                # answer nothing.
                #
                # ERROR CONTEXT is a browsing mode rather than a request to
                # see past anything, so a neighbour still has to pass the
                # filter -- otherwise choosing a component would silently
                # show rows from the components you excluded.
                if peeking or index in allowed                         or self._matches(self._entries[index]):
                    kept.add(index)
        return sorted(kept)

    def set_error_context(self, lines) -> None:
        """Show only errors and the `lines` rows either side, or None to
        stop."""
        self._context_lines = lines
        self._peek_ref = None
        self.beginResetModel()
        self._reindex()
        self.endResetModel()

    def is_peeking(self) -> bool:
        """Whether a peek is open at all.

        Not "at this record": once a peek opens, the neighbours it revealed
        become rows too, so the current row is usually no longer the record
        that was peeked at. Asking about a specific one made pressing the
        button twice peek at a NEIGHBOUR instead of closing.
        """
        return self._peek_ref is not None

    def peek(self, entry, lines: int) -> None:
        """Reveal the rows around one record without clearing the filter."""
        self._peek_ref = entry
        self._peek_lines = lines
        self.beginResetModel()
        self._reindex()
        self.endResetModel()

    def _recount_folds(self) -> None:
        """How many continuation lines sit under each parent record.

        Built in one pass and cached because a deque is O(n) to index in the
        middle: counting on demand inside `data()` would be quadratic on a
        200,000-record log. An orphan continuation -- the first records of a
        tail slice that opened inside a 1,260-line block -- has no parent to
        fold under and is left visible.
        """
        counts = {}
        folded = set()
        parent = None
        for index, entry in enumerate(self._entries):
            if entry.raw.get("continuation"):
                # An orphan has nothing to fold under, so it stays a row of
                # its own rather than vanishing with no parent to reveal it.
                if parent is not None:
                    counts[parent] = counts.get(parent, 0) + 1
                    folded.add(index)
            else:
                parent = index
        self._fold_counts = counts
        self._folded = folded

    def _shows(self, index: int, entry) -> bool:
        """Whether the record at `index` earns a row right now."""
        if self._folding_now() and index in self._folded:
            return False
        return self._matches(entry)

    def _repaint_visible(self) -> None:
        """Ask the view to repaint what is on screen, without a reset.

        A reset clears the selection, which `append` documents as the trap
        it exists to avoid -- but a parent's folded count can change under a
        row that is already displayed, so the text does need refreshing.
        """
        if not self._visible:
            return
        self.dataChanged.emit(self.index(0, MESSAGE),
                              self.index(len(self._visible) - 1, MESSAGE),
                              [Qt.ItemDataRole.DisplayRole])

    def _folding_now(self) -> bool:
        """Whether folding is actually hiding anything at this moment.

        Folding is a browsing convenience, so it yields the moment the user
        searches: with a needle typed, every continuation is eligible again
        and nothing can hide a match from them.
        """
        return self._fold and not self._needle

    def is_folding(self) -> bool:
        """The checkbox's state, which `find` may have turned off."""
        return self._fold

    def set_folding(self, enabled: bool) -> None:
        self._fold = bool(enabled)
        self.beginResetModel()
        self._reindex()
        self.endResetModel()

    def folded_count(self) -> int:
        """How many records are hidden right now, for the status bar.

        Silently showing fewer records than the log holds is how someone
        concludes the log is clean, so the pane says this out loud.
        """
        if not self._folding_now():
            return 0
        return sum(self._fold_counts.values())

    def rows_for_export(self) -> list:
        """Every visible record, plus the continuations folding is hiding.

        Folding is a view convenience; an actual filter is not. An exported
        file that silently dropped a tenth of the log would be the exact
        failure this module exists to prevent, so folding is ignored here
        and every other filter still applies.
        """
        out = []
        folding = self._folding_now()
        for index in self._visible:
            out.append(self._entries[index])
            if not folding:
                continue
            for offset in range(1, self._fold_counts.get(index, 0) + 1):
                out.append(self._entries[index + offset])
        return out

    # ---- Qt -------------------------------------------------------------

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self._visible)

    def columnCount(self, parent=QModelIndex()):
        return len(COLUMNS)

    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):
        if (orientation is Qt.Orientation.Horizontal
                and role == Qt.ItemDataRole.DisplayRole
                and 0 <= section < len(COLUMNS)):
            return COLUMNS[section]
        return None

    def _cell_colours(self, index, entry) -> Optional[Tuple[str, str]]:
        """`(background, foreground)` for one cell, or None for no colour.

        Background and Foreground used to duplicate this shape, differing
        only by tuple index -- and that duplication is exactly what let a
        malformed highlight colour raise out of one role (Foreground, via
        `readable_text_on`) while the other (Background, a bare `QColor()`
        construction) merely came back invalid. One helper both roles index
        keeps the three-system territory rule in one place: the Component
        column's tint wins its own cell over everything else, a highlight
        rule beats severity, and severity applies when nothing else claims
        the row.
        """
        if index.column() == COMPONENT and entry.source:
            return component_colour(entry.source)
        rule = matching_rule(self._rules, entry)
        if rule is not None:
            return rule.colour, readable_text_on(rule.colour)
        return severity_row_colour(entry.level)

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        entry = self.entry(index.row()) if index.isValid() else None
        if entry is None:
            return None
        if role == Qt.ItemDataRole.DisplayRole:
            column = index.column()
            if column == TIME:
                # The same formatter log_export uses for the exported file,
                # so what someone sees here and what they export can never
                # quietly disagree. The bookmark star is DISPLAY only, the
                # same rule the fold suffix follows -- export reads the
                # record, so it can never leak into a file.
                stamp = format_stamp(entry)
                return f"★ {stamp}" if self.is_bookmarked(entry) else stamp
            if column == SOURCE:
                return entry.raw.get("log", "")
            if column == SEVERITY:
                return entry.level
            if column == COMPONENT:
                return entry.source
            if column == PACKAGE:
                # Computed here rather than stored at parse time: Qt only
                # asks for the cells it is about to paint, so this costs ~30
                # regex searches per repaint instead of 250 ms added to
                # every open of the real archive.
                return package_of(entry.message)
            if column == THREAD:
                return entry.raw.get("thread", "")
            if column == MESSAGE:
                # One row per record: a stack trace is one entry, and letting
                # its newlines through would break the row height for the
                # whole table.
                text = entry.message.replace("\n", " ↵ ")
                # Display only. Export and copy read `entry.message`, so the
                # suffix can never leak into what they write.
                if self._folding_now():
                    hidden = self._fold_counts.get(
                        self._visible[index.row()], 0)
                    if hidden:
                        text = f"{text}   (+{hidden:,} lines)"
                return text
        elif role == Qt.ItemDataRole.BackgroundRole:
            colours = self._cell_colours(index, entry)
            return QColor(colours[0]) if colours else None
        elif role == Qt.ItemDataRole.ForegroundRole:
            colours = self._cell_colours(index, entry)
            return QColor(colours[1]) if colours else None
        elif role == Qt.ItemDataRole.ToolTipRole:
            # The line as written, then what its error codes mean. This is
            # what people open CMTrace for: a line says 0x80070005 and they
            # need "access denied".
            from .error_codes import annotate

            return annotate(entry.message)
        return None

    # ---- find -----------------------------------------------------------

    def find(self, needle: str, start_row: int = 0, forwards: bool = True) -> int:
        """The next row containing `needle`, or -1, unfolding to reach it.

        A search result outranks a view convenience: if the only match is a
        line folding is hiding, folding is turned off and the search run
        again, rather than reporting no match for text that is in the log.
        The pane re-reads `is_folding()` afterwards so the checkbox shows
        what happened.
        """
        row = self._find_visible(needle, start_row, forwards)
        if row >= 0 or not self._folding_now():
            return row
        self.set_folding(False)
        row = self._find_visible(needle, start_row, forwards)
        if row < 0:
            # Nothing anywhere. Searching for text that is not in the log
            # should not silently rearrange the view, so put folding back.
            self.set_folding(True)
        return row

    def find_issue(self, start_row: int = 0, forwards: bool = True,
                   levels: frozenset = ISSUE_LEVELS) -> int:
        """The next VISIBLE row at severity `levels`, or -1.

        Independent of the Find box: an admin scanning a log they have not
        typed anything into yet wants "take me to the next problem",
        deliberately mirroring `find`'s unfold-and-retry rather than
        `_find_visible`'s plain wrap. A folded CONTINUATION line is not
        automatically safe to skip -- `parse_service_log` gives it its
        PARENT's level, so a run of Error continuation lines folded under one
        parent row can itself be the next thing worth landing on, and
        unfolding is what makes those reachable.
        """
        row = self._find_issue_visible(start_row, forwards, levels)
        if row >= 0 or not self._folding_now():
            return row
        self.set_folding(False)
        row = self._find_issue_visible(start_row, forwards, levels)
        if row < 0:
            # Nothing anywhere, even unfolded: put folding back rather than
            # leaving the view permanently unfolded over a search that found
            # nothing.
            self.set_folding(True)
        return row

    def _find_issue_visible(self, start_row: int, forwards: bool,
                            levels: frozenset) -> int:
        """The next VISIBLE row at severity `levels`, or -1.

        Wraps, the same reason `_find_visible` wraps: stopping at the end of
        the loaded window makes someone scroll back to the top by hand to
        keep going.
        """
        if not self._visible:
            return -1
        count = len(self._visible)
        step = 1 if forwards else -1
        for offset in range(1, count + 1):
            row = (start_row + offset * step) % count
            entry = self.entry(row)
            if entry is not None and entry.level in levels:
                return row
        return -1

    def _find_visible(self, needle: str, start_row: int = 0,
                      forwards: bool = True) -> int:
        """The next VISIBLE row containing `needle`, or -1.

        Wraps, because a search that stops at the end of a log makes the user
        scroll back to the top to carry on. Honours the same Regex flag the
        filter does: one checkbox governs both.
        """
        if not needle or not self._visible:
            return -1
        matcher = None
        if self._regex:
            try:
                matcher = re.compile(needle, re.IGNORECASE)
            except re.error:
                return -1
        lowered = needle.lower()
        count = len(self._visible)
        step = 1 if forwards else -1
        for offset in range(1, count + 1):
            row = (start_row + offset * step) % count
            entry = self.entry(row)
            if entry is None:
                continue
            if matcher is not None:
                if matcher.search(entry.message):
                    return row
            elif lowered in entry.message.lower():
                return row
        return -1
