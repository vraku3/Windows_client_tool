"""Effective Windows Update deferral/restart policy."""
from modules.updates import update_policy as up


def read(scopes, unreadable=()):
    return up.read_policy(reader=lambda: (scopes, list(unreadable)))


def row(policy, label):
    return next(r for r in policy.rows if r.label == label)


def test_unconfigured_keys_are_defaults_not_unknown():
    policy = read({})
    assert policy.quality_deferral_days == 0 and policy.feature_deferral_days == 0
    assert row(policy, "Quality update deferral").source == "Default"
    assert "not configured" in row(policy, "Quality update deferral").value
    assert row(policy, "Updates paused until").value == "Not paused"
    assert policy.active_hours == "Windows-managed"


def test_policy_deferrals_win_over_mdm_and_keep_provenance():
    policy = read({"wu_policy": {"DeferQualityUpdatesPeriodInDays": 7},
                   "mdm_update": {"DeferQualityUpdatesPeriodInDays": 14,
                                  "DeferFeatureUpdatesPeriodInDays": 30}})
    assert policy.quality_deferral_days == 7
    assert row(policy, "Quality update deferral").source == "Local policy (GPO)"
    assert policy.feature_deferral_days == 30
    assert row(policy, "Feature update deferral").source == "MDM / Intune"


def test_pause_uses_the_latest_end_time():
    policy = read({"wu_policy": {"PauseQualityUpdatesEndTime": "2026-11-10T00:00:00Z"},
                   "ux_settings": {"PauseUpdatesExpiryTime": "2026-11-15T00:00:00Z"}})
    assert policy.pause_until == "2026-11-15T00:00:00Z"
    assert "paused until" in row(policy, "Updates paused until").label.lower()


def test_active_hours_are_only_shown_when_enabled():
    off = read({"wu_policy": {"SetActiveHours": 0, "ActiveHoursStart": 8, "ActiveHoursEnd": 22}})
    assert off.active_hours == "Windows-managed"
    on = read({"wu_policy": {"SetActiveHours": 1, "ActiveHoursStart": 8, "ActiveHoursEnd": 22}})
    assert on.active_hours == "08:00 – 22:00"
    assert row(on, "Active hours").source == "Local policy (GPO)"


def test_deadlines_target_release_preview_and_access_are_reported():
    policy = read({"wu_policy": {
        "ConfigureDeadlineForQualityUpdates": 5,
        "ConfigureDeadlineGracePeriod": 2,
        "ConfigureDeadlineNoAutoReboot": 1,
        "TargetReleaseVersion": 1,
        "TargetReleaseVersionInfo": "25H2",
        "ManagePreviewBuilds": 1,
        "ManagePreviewBuildsPolicyValue": 1,
        "DisableWindowsUpdateAccess": 1,
        "ExcludeWUDriversInQualityUpdate": 1,
    }})
    assert "quality 5 day(s)" in row(policy, "Install deadlines").value
    assert "auto-reboot disabled" in row(policy, "Install deadlines").value
    assert policy.target_release == "25H2"
    assert row(policy, "Preview builds").value == "allow optional"
    assert row(policy, "Windows Update access").value == "blocked by policy"
    assert row(policy, "Drivers in quality updates").value == "excluded"


def test_a_refused_key_is_reported_as_unreadable():
    policy = read({}, unreadable=["wu_policy"])
    assert policy.unreadable == ["wu_policy"]
    assert "Local policy (GPO)" in row(policy, "Policy source unreadable").value
    assert "not the same as 'not configured'" in row(policy, "Policy source unreadable").note


def test_the_real_reader_does_not_raise():
    policy = up.read_policy()
    assert policy.rows and isinstance(policy.quality_deferral_days, int)
