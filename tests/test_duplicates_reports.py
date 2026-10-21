from modules.cleanup.duplicates.models import DuplicateGroup, FileEntry, ScanResult
from modules.cleanup.duplicates.reports import export_report
from modules.cleanup.duplicates.actions import ActionOutcome


def test_reports_escape_paths_and_include_outcomes(tmp_path):
    path = '=formula<img src="bad">.txt'
    entry = FileEntry(path, 3, 1, 1, 1, 1)
    result = ScanResult(groups=[DuplicateGroup(0, [entry], "name", "candidate")])
    outcomes = [ActionOutcome("removed.bin", "done", "recycle", 0)]
    html = tmp_path / "out.html"
    csv = tmp_path / "out.csv"
    export_report(str(html), result, outcomes, html=True)
    export_report(str(csv), result, outcomes)
    assert "<img" not in html.read_text(encoding="utf-8")
    assert "&lt;img" in html.read_text(encoding="utf-8")
    assert "'=formula" in csv.read_text(encoding="utf-8")
    assert "removed.bin" in csv.read_text(encoding="utf-8")
