"""Update source / ownership evidence."""
from modules.updates import update_ownership as uo


def read(scopes, enrollments=(), unreadable=()):
    return uo.read_ownership(reader=lambda: {"scopes": scopes,
                                             "enrollments": list(enrollments),
                                             "unreadable": list(unreadable)})


def row(owner, label):
    return next(r for r in owner.rows if r.label == label)


def test_nothing_configured_is_a_definite_local_answer():
    owner = read({})
    assert owner.managed_by == "No local WSUS/MDM policy found"
    assert row(owner, "WSUS server").value == "Not configured"
    assert row(owner, "MDM enrollment").value == "No enrollment found"
    assert "Windows Update (no source policy)" in row(owner, "Feature updates source").value
    assert owner.unreadable == []


def test_wsus_endpoint_implies_the_source_for_unset_classes():
    owner = read({"wu_policy": {"WUServer": "http://wsus.contoso.local:8530",
                                "WUStatusServer": "http://wsus.contoso.local:8530",
                                "TargetGroup": "Workstations",
                                "TargetGroupEnabled": 1}})
    assert owner.managed_by == "WSUS" and owner.wsus_url == "http://wsus.contoso.local:8530"
    assert row(owner, "Target group").value == "Workstations"
    assert "WSUS (implied" in row(owner, "Quality updates source").value


def test_wufb_policy_and_per_class_sources():
    owner = read({"wu_policy": {"SetPolicyDrivenUpdateSourceForFeatureUpdates": 1,
                                "SetPolicyDrivenUpdateSourceForQualityUpdates": 1,
                                "SetPolicyDrivenUpdateSourceForDriverUpdates": 0,
                                "BranchReadinessLevel": 2}})
    assert owner.managed_by == "Windows Update for Business"
    assert row(owner, "Feature updates source").value == "Windows Update (WUfB)"
    assert row(owner, "Driver updates source").value == "WSUS / policy server"


def test_mdm_enrollment_is_named_and_not_confused_with_wufb():
    owner = read({}, enrollments=[{"id": "1", "provider": "MS DM Server", "state": 1},
                                  {"id": "2", "provider": "MS DM Server", "state": 1}])
    assert "MS DM Server" in owner.managed_by
    assert "2 enrollment" in row(owner, "MDM enrollment").value


def test_a_refused_scope_is_reported_not_unmanaged():
    owner = read({}, unreadable=["wu_policy"])
    assert owner.unreadable == ["wu_policy"]
    assert row(owner, "Source unreadable").value == "wu_policy"


def test_the_real_reader_does_not_raise():
    owner = uo.read_ownership()
    assert owner.rows and isinstance(owner.managed_by, str)
