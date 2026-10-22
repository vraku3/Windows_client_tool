r"""Opening a CbsPersist .cab without unpacking it by hand.

Windows 11 rolls CBS into `.cab` files -- there are four in
`C:\Windows\Logs\CBS` on this machine -- and the plain `.log` beside them is
usually the smallest part of the story.

Extracted with `expand.exe`, which ships with Windows. The existing CBS tab
uses 7-Zip, which is installed here but is not on most machines; depending on
it would make this work on the developer's box and nowhere else.

Two things real cabs do that a naive extractor gets wrong, both verified
against `C:\Windows\Logs\CBS`:

* the file INSIDE is named like the cab, extension and all, so "find the
  .log" finds nothing;
* it is 15.8 MB of text from a 465 KB cab, so the extraction has to be given
  somewhere with room and cleaned up afterwards.
"""
import os

import pytest

from modules.log_viewer.archives import (
    extract_cab, is_cab, largest_cbs_cab,
)


@pytest.fixture(autouse=True)
def _isolate_extractions(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))


def test_a_cab_is_recognised_by_its_extension():
    assert is_cab(r"C:\logs\CbsPersist_1.cab")
    assert is_cab(r"C:\logs\CbsPersist_1.CAB")
    assert not is_cab(r"C:\logs\CBS.log")
    assert not is_cab("")


def test_a_missing_cab_reports_why_rather_than_raising(tmp_path):
    path, problem = extract_cab(str(tmp_path / "nope.cab"))
    assert path == ""
    assert problem


def test_something_that_is_not_a_cab_reports_why(tmp_path):
    fake = tmp_path / "fake.cab"
    fake.write_text("not a cabinet at all", encoding="utf-8")

    path, problem = extract_cab(str(fake))

    assert path == ""
    assert problem


@pytest.mark.skipif(not os.path.isdir(r"C:\Windows\Logs\CBS"),
                    reason="no CBS folder on this machine")
def test_a_real_cbs_cab_extracts_to_readable_log_text(tmp_path):
    r"""The one that matters. Run against whatever cab this machine has."""
    cab = largest_cbs_cab()
    if not cab:
        pytest.skip("no CbsPersist cab on this machine")

    path, problem = extract_cab(cab, into=str(tmp_path))

    assert not problem, problem
    assert os.path.isfile(path)
    with open(path, "rb") as handle:
        head = handle.read(4)
    assert head[:4] != b"MSCF", "still a cabinet; nothing was extracted"
    assert os.path.getsize(path) > 0


@pytest.mark.skipif(not os.path.isdir(r"C:\Windows\Logs\CBS"),
                    reason="no CBS folder on this machine")
def test_the_extracted_file_is_found_despite_being_named_like_the_cab():
    """`expand` writes the member under its stored name, which for CbsPersist
    is the cab's own name -- extension and all. Looking for `*.log` finds
    nothing."""
    cab = largest_cbs_cab()
    if not cab:
        pytest.skip("no CbsPersist cab on this machine")
    import tempfile
    with tempfile.TemporaryDirectory() as into:
        path, problem = extract_cab(cab, into=into)
        assert not problem
        assert os.path.dirname(path) == into


# ---- the pane -----------------------------------------------------------

from modules.log_viewer.log_viewer_module import LogViewerWidget  # noqa: E402


@pytest.mark.skipif(not largest_cbs_cab(),
                    reason="no CbsPersist cab on this machine")
def test_opening_a_real_cab_shows_its_records(qapp):
    widget = LogViewerWidget()
    try:
        widget.open(largest_cbs_cab())
        assert widget.model.total > 0, "the cab opened but held no records"
        assert "CbsPersist" in widget.status.text()
    finally:
        widget.stop()


def test_a_cab_that_is_not_one_says_why_and_keeps_the_pane_usable(qapp,
                                                                  tmp_path):
    fake = tmp_path / "broken.cab"
    fake.write_text("not a cabinet", encoding="utf-8")

    widget = LogViewerWidget()
    try:
        widget.open(str(fake))
        assert "not a cabinet" in widget.status.text().lower()
        assert widget.model.total == 0
    finally:
        widget.stop()


def test_the_browse_filter_offers_cabs(qapp):
    widget = LogViewerWidget()
    try:
        assert ".cab" in widget.FILE_FILTER
    finally:
        widget.stop()
