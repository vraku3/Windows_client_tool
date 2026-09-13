from modules.driver_manager.driver_reader import DriverInfo
from modules.driver_manager.vendor_updates import rollback as rb


def _driver(inf_name="oem12.inf"):
    return DriverInfo(device_name="Test GPU", driver_class="Display",
                      version="1.0", date="", publisher="V", signed=True,
                      error_code=0, flags="", inf_name=inf_name,
                      hardware_id="PCI\\VEN_10DE&DEV_2684")


def test_snapshot_before_install_records_the_oem_published_name():
    token = rb.snapshot_before_install(_driver(inf_name="oem12.inf"))
    assert token == "oem12.inf"


def test_snapshot_before_install_returns_none_for_a_driverless_device():
    token = rb.snapshot_before_install(_driver(inf_name=""))
    assert token is None


def test_snapshot_before_install_returns_none_for_an_inbox_driver():
    # published_name_for() itself returns None for non-oem infs like usb.inf
    token = rb.snapshot_before_install(_driver(inf_name="usb.inf"))
    assert token is None


def test_rollback_reinstalls_the_snapshotted_package(monkeypatch):
    # store_folder_for() returns a bare FileRepository folder NAME, never a
    # full path (Finding C1a) -- and the real .inf inside that folder is the
    # vendor's OWN filename, never the OEM published token (Finding C1b).
    monkeypatch.setattr(rb, "create_restore_point", lambda desc, timeout=60: (True, ""))
    monkeypatch.setattr(rb, "store_folder_for",
                        lambda published: "display.inf_amd64_abc")
    monkeypatch.setattr(rb, "file_repository",
                        lambda: r"C:\Windows\System32\DriverStore\FileRepository")
    monkeypatch.setattr(rb.os, "listdir", lambda path: ["nv_disp.inf", "nv_disp.sys"])
    ran = {}

    def fake_pnputil(inf_path):
        ran["inf_path"] = inf_path
        return True, ""

    monkeypatch.setattr(rb, "_run_pnputil_install", fake_pnputil)
    result = rb.rollback("oem12.inf")
    assert result.ok is True
    # The real filename inside the store folder is used -- never the token.
    assert ran["inf_path"] == (
        r"C:\Windows\System32\DriverStore\FileRepository"
        r"\display.inf_amd64_abc\nv_disp.inf")
    assert "oem12.inf" not in ran["inf_path"]


def test_rollback_refuses_when_no_restore_point_can_be_created(monkeypatch):
    monkeypatch.setattr(rb, "create_restore_point", lambda desc, timeout=60: (False, "disabled"))
    result = rb.rollback("oem12.inf")
    assert result.ok is False
    assert "restore point" in result.reason.lower()


def test_rollback_reports_when_the_store_no_longer_has_the_package(monkeypatch):
    monkeypatch.setattr(rb, "create_restore_point", lambda desc, timeout=60: (True, ""))
    monkeypatch.setattr(rb, "store_folder_for", lambda published: None)
    result = rb.rollback("oem12.inf")
    assert result.ok is False
    assert "no longer" in result.reason.lower() or "not found" in result.reason.lower()


def test_rollback_reports_when_the_package_folder_has_no_inf_file(monkeypatch):
    monkeypatch.setattr(rb, "create_restore_point", lambda desc, timeout=60: (True, ""))
    monkeypatch.setattr(rb, "store_folder_for", lambda published: "display.inf_amd64_abc")
    monkeypatch.setattr(rb, "file_repository", lambda: r"C:\FileRepository")
    monkeypatch.setattr(rb.os, "listdir", lambda path: ["readme.txt"])
    result = rb.rollback("oem12.inf")
    assert result.ok is False
    assert "no inf" in result.reason.lower()


def test_rollback_reports_when_the_package_folder_cannot_be_read(monkeypatch, caplog):
    monkeypatch.setattr(rb, "create_restore_point", lambda desc, timeout=60: (True, ""))
    monkeypatch.setattr(rb, "store_folder_for", lambda published: "display.inf_amd64_abc")
    monkeypatch.setattr(rb, "file_repository", lambda: r"C:\FileRepository")

    def raise_oserror(path):
        raise OSError("access denied")

    monkeypatch.setattr(rb.os, "listdir", raise_oserror)
    with caplog.at_level("WARNING"):
        result = rb.rollback("oem12.inf")
    assert result.ok is False
    assert "could not be read" in result.reason.lower()
    assert any("rollback" in r.message.lower() for r in caplog.records)


def test_bulk_rollback_reports_every_result_even_with_a_partial_failure(monkeypatch):
    monkeypatch.setattr(rb, "create_restore_point", lambda desc, timeout=60: (True, ""))

    def fake_store_folder(published):
        return "ok_folder" if published == "oem1.inf" else None

    monkeypatch.setattr(rb, "store_folder_for", fake_store_folder)
    monkeypatch.setattr(rb, "file_repository", lambda: r"C:\FileRepository")
    monkeypatch.setattr(rb.os, "listdir", lambda path: ["real_vendor_name.inf"])
    monkeypatch.setattr(rb, "_run_pnputil_install", lambda inf: (True, ""))
    results = rb.bulk_rollback(["oem1.inf", "oem2.inf"])
    assert len(results) == 2
    assert results[0].ok is True
    assert results[1].ok is False


def test_rollback_with_no_token_refuses_without_raising():
    # snapshot_before_install legitimately returns None for a driverless
    # device or an inbox driver -- a caller that forwards that straight
    # into rollback() must get a clean refusal, not a TypeError from
    # store_folder_for's string concatenation.
    result = rb.rollback(None)
    assert result.ok is False
    assert "token" in result.reason.lower()
    assert result.restore_point_taken is False


def test_rollback_with_empty_string_token_refuses_without_raising():
    result = rb.rollback("")
    assert result.ok is False
    assert result.restore_point_taken is False


def test_bulk_rollback_survives_a_none_token_in_the_middle_of_the_list(monkeypatch):
    monkeypatch.setattr(rb, "create_restore_point", lambda desc, timeout=60: (True, ""))
    monkeypatch.setattr(rb, "store_folder_for", lambda published: "ok_folder")
    monkeypatch.setattr(rb, "file_repository", lambda: r"C:\FileRepository")
    monkeypatch.setattr(rb.os, "listdir", lambda path: ["real_vendor_name.inf"])
    monkeypatch.setattr(rb, "_run_pnputil_install", lambda inf: (True, ""))
    results = rb.bulk_rollback(["oem1.inf", None])
    assert len(results) == 2
    assert results[0].ok is True
    assert results[1].ok is False


def test_bulk_rollback_takes_exactly_one_restore_point_for_the_whole_batch(monkeypatch):
    calls = []

    def fake_create_restore_point(desc, timeout=60):
        calls.append(desc)
        return True, ""

    monkeypatch.setattr(rb, "create_restore_point", fake_create_restore_point)
    monkeypatch.setattr(rb, "store_folder_for", lambda published: "ok_folder")
    monkeypatch.setattr(rb, "file_repository", lambda: r"C:\FileRepository")
    monkeypatch.setattr(rb.os, "listdir", lambda path: ["real_vendor_name.inf"])
    monkeypatch.setattr(rb, "_run_pnputil_install", lambda inf: (True, ""))
    results = rb.bulk_rollback(["oem1.inf", "oem2.inf", "oem3.inf"])
    assert len(calls) == 1  # not one per token
    assert all(r.ok for r in results)
    assert all(r.restore_point_taken for r in results)


def test_bulk_rollback_refuses_every_token_cleanly_when_the_batch_restore_point_fails(monkeypatch):
    monkeypatch.setattr(rb, "create_restore_point", lambda desc, timeout=60: (False, "policy disabled"))
    pnputil_calls = []
    monkeypatch.setattr(rb, "_run_pnputil_install",
                        lambda inf: pnputil_calls.append(inf) or (True, ""))
    results = rb.bulk_rollback(["oem1.inf", "oem2.inf"])
    assert len(results) == 2
    assert all(r.ok is False for r in results)
    assert all(r.restore_point_taken is False for r in results)
    assert pnputil_calls == []  # never attempted -- refused up front


def test_bulk_rollback_with_no_tokens_returns_an_empty_list():
    assert rb.bulk_rollback([]) == []
