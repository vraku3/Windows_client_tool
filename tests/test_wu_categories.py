"""Windows Update categories and the automated-run gating rules."""
from types import SimpleNamespace

from modules.updates import wu_categories as wc


def u(classification, title, severity=""):
    return SimpleNamespace(classification=classification, title=title, severity=severity)


def test_classification_covers_the_cases_that_matter():
    assert wc.classify("Security Updates", "2026-10 Security Update (KB1) (26100.1)") == wc.SECURITY
    assert wc.classify("Critical Updates", "Cumulative update") == wc.SECURITY
    assert wc.classify("Upgrades", "Windows 11, version 25H2") == wc.FEATURE
    assert wc.classify("Updates", "Windows 11, version 25H2") == wc.FEATURE
    assert wc.classify("Drivers", "Intel display driver") == wc.DRIVER
    assert wc.classify("Updates", "2026-10 Preview Update (KB2)") == wc.PREVIEW
    assert wc.classify("Updates", "Microsoft .NET Framework 4.8 update") == wc.DOTNET
    assert wc.classify("Definition Updates", "Security Intelligence Update for Defender") == wc.DEFENDER
    assert wc.classify("Update Rollups", "Some rollup") == wc.QUALITY


def test_summary_line_counts_categories():
    line = wc.summary_line([u("Security Updates", "fix"),
                            u("Upgrades", "Windows 11, version 25H2"),
                            u("Drivers", "Intel driver")])
    assert "Security 1" in line and "Feature upgrade 1" in line and "Driver 1" in line


def test_automatic_runs_skip_feature_and_optional_by_default():
    updates = [u("Security Updates", "fix"),
               u("Upgrades", "Windows 11, version 25H2"),
               u("Drivers", "Intel driver"),
               u("Updates", "Preview Update (KB9)")]
    run, skipped = wc.split_for_automatic(updates)
    assert [x.title for x in run] == ["fix"]
    assert len(skipped[wc.FEATURE]) == 1 and len(skipped[wc.DRIVER]) == 1
    assert len(skipped[wc.PREVIEW]) == 1
    text = wc.skipped_text(skipped)
    assert "needs explicit approval" in text and "optional" in text


def test_automatic_runs_can_opt_in_per_group():
    updates = [u("Upgrades", "Windows 11, version 25H2"), u("Drivers", "Intel driver")]
    run, skipped = wc.split_for_automatic(updates, allow_feature=True)
    assert [x.title for x in run] == ["Windows 11, version 25H2"]
    assert len(skipped[wc.DRIVER]) == 1
    run, skipped = wc.split_for_automatic(updates, allow_feature=True, allow_optional=True)
    assert len(run) == 2 and skipped == {}
