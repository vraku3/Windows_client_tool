"""Where the codes ARE in a line, and comprehensive catalog verification."""
import pytest
from modules.log_viewer.error_codes import (
    Advice, advice, code_spans, describe, explain, find_codes,
)


def test_spans_point_at_the_code_in_the_text():
    text = "Failed to open package. [HRESULT = 0x800f0805 - CBS_E_INVALID]"
    spans = code_spans(text)
    assert len(spans) == 1
    start, end, code = spans[0]
    assert text[start:end] == "0x800f0805"
    assert code == 0x800F0805


def test_every_occurrence_gets_a_span_even_when_the_code_repeats():
    """find_codes de-duplicates because it answers 'which codes'; the
    delegate asks 'where', and both occurrences must be coloured."""
    text = "0x80070005 then 0x80070005 again"
    assert len(find_codes(text)) == 1
    assert len(code_spans(text)) == 2


def test_no_codes_is_an_empty_list():
    assert code_spans("nothing to see") == []
    assert code_spans("") == []


# ── Windows Update & CBS HRESULTs ──────────────────────────────────────────

WU_CBS_CODES = [
    0x80240020,  # WU_E_NO_INTERACTIVE_USER
    0x80244019,  # WU_E_PT_HTTP_STATUS_NOT_FOUND (HTTP 404)
    0x800F081F,  # CBS_E_SOURCE_MISSING
    0x800F0906,  # CBS_E_DOWNLOAD_FAILURE
    0x80070002,  # ERROR_FILE_NOT_FOUND
    0x80070005,  # ERROR_ACCESS_DENIED
    0x80070422,  # ERROR_SERVICE_DISABLED
    0x80073712,  # ERROR_SXS_COMPONENT_STORE_CORRUPT
    0x80248007,  # WU_E_DS_DATANOTFOUND
    0x80070020,  # ERROR_SHARING_VIOLATION
]


@pytest.mark.parametrize("code", WU_CBS_CODES)
def test_wu_and_cbs_codes_are_recognized(code):
    hex_str = f"0x{code:08X}"
    found = find_codes(f"Operation failed with error {hex_str}")
    assert code in found

    meaning = describe(code)
    assert meaning, f"describe({hex_str}) returned empty string"

    adv = advice(code)
    assert isinstance(adv, Advice), f"advice({hex_str}) returned None"
    assert adv.cause.strip()
    assert adv.remedy.strip()
    assert adv.reference.strip()


def test_wu_and_cbs_actionable_remedies():
    # 0x800F081F: RestoreHealth with /Source
    adv_81f = advice(0x800F081F)
    assert "DISM" in adv_81f.remedy and "/RestoreHealth" in adv_81f.remedy

    # 0x800F0906: Alternate source or FOD policy
    adv_906 = advice(0x800F0906)
    assert "DISM" in adv_906.remedy

    # 0x80070422: Service disabled -> sc config / net start
    adv_422 = advice(0x80070422)
    assert "wuauserv" in adv_422.remedy

    # 0x80073712: Component store corrupt -> RestoreHealth
    adv_712 = advice(0x80073712)
    assert "RestoreHealth" in adv_712.remedy

    # 0x80248007: Datastore corruption -> stop wuauserv, rename DataStore
    adv_datastore = advice(0x80248007)
    assert "DataStore" in adv_datastore.remedy


# ── Kernel NTSTATUS Codes ──────────────────────────────────────────────────

NTSTATUS_TEST_CODES = [
    (0xC0000005, "STATUS_ACCESS_VIOLATION"),
    (0xC0000034, "STATUS_OBJECT_NAME_NOT_FOUND"),
    (0xC0000142, "STATUS_DLL_INIT_FAILED"),
    (0xC000021A, "STATUS_SYSTEM_PROCESS_TERMINATED"),
    (0xC0000022, "STATUS_ACCESS_DENIED"),
    (0xC000001D, "STATUS_ILLEGAL_INSTRUCTION"),
]


@pytest.mark.parametrize("code,mnemonic", NTSTATUS_TEST_CODES)
def test_ntstatus_codes_are_recognized(code, mnemonic):
    hex_str = f"0x{code:08X}"
    found = find_codes(f"Crash report: exception code {hex_str} in module.dll")
    assert code in found

    meaning = describe(code)
    assert meaning, f"describe({hex_str}) returned empty string"
    assert mnemonic in meaning

    adv = advice(code)
    assert isinstance(adv, Advice), f"advice({hex_str}) returned None"
    assert adv.cause.strip()
    assert adv.remedy.strip()
    assert adv.reference.strip()


def test_ntstatus_actionable_remedies():
    # 0xC0000005: Memory dump / sfc / mdsched
    adv_av = advice(0xC0000005)
    assert "sfc" in adv_av.remedy.lower() or "mdsched" in adv_av.remedy.lower()

    # 0xC0000034: BCD / boot repair
    adv_obj = advice(0xC0000034)
    assert "bcd" in adv_obj.remedy.lower() or "bootrec" in adv_obj.remedy.lower()

    # 0xC0000142: Visual C++ Redistributable / AppInit_DLLs
    adv_dll = advice(0xC0000142)
    assert "Visual C++" in adv_dll.remedy or "sfc" in adv_dll.remedy.lower()

    # 0xC000021A: WinRE / DISM / sfc
    adv_subsys = advice(0xC000021A)
    assert "DISM" in adv_subsys.remedy or "sfc" in adv_subsys.remedy.lower()


# ── Windows Installer (MSI) Error Codes ─────────────────────────────────────

MSI_TEST_CODES = [
    (1603, 0x80070643, "ERROR_INSTALL_FAILURE"),
    (1618, 0x80070652, "ERROR_INSTALL_ALREADY_RUNNING"),
    (1601, 0x80070641, "ERROR_INSTALL_SERVICE_FAILURE"),
    (1619, 0x80070653, "ERROR_INSTALL_PACKAGE_OPEN_FAILED"),
    (1620, 0x80070654, "ERROR_INSTALL_PACKAGE_INVALID"),
]


@pytest.mark.parametrize("dec_code,hr_code,mnemonic", MSI_TEST_CODES)
def test_msi_codes_detected_in_log_lines(dec_code, hr_code, mnemonic):
    # Decimal detection in typical MSI log formats
    line1 = f"MainEngineThread is returning {dec_code}"
    assert find_codes(line1) == [dec_code]

    line2 = f"Product installation failed with error {dec_code}."
    assert find_codes(line2) == [dec_code]

    # HRESULT hex detection
    hex_str = f"0x{hr_code:08X}"
    line3 = f"Installation ended with {hex_str}"
    assert find_codes(line3) == [hr_code]

    # Both dec and hr describe with mnemonic or clear text
    desc_dec = describe(dec_code)
    desc_hr = describe(hr_code)
    assert desc_dec
    assert desc_hr

    # Both dec and hr resolve to actionable advice
    adv_dec = advice(dec_code)
    adv_hr = advice(hr_code)
    assert adv_dec is not None
    assert adv_hr is not None
    assert adv_dec.remedy.strip()
    assert adv_hr.remedy.strip()


def test_msi_code_spans():
    text = "Error 1603 occurred during setup, then 1618 blocked retry"
    spans = code_spans(text)
    assert len(spans) == 2
    s1, e1, c1 = spans[0]
    s2, e2, c2 = spans[1]
    assert text[s1:e1] == "1603"
    assert c1 == 1603
    assert text[s2:e2] == "1618"
    assert c2 == 1618


def test_msi_actionable_remedies():
    # 1603: verbose logging and Return value 3
    adv_1603 = advice(1603)
    assert "msiexec" in adv_1603.remedy and "Return value 3" in adv_1603.remedy

    # 1618: kill hung msiexec
    adv_1618 = advice(1618)
    assert "taskkill" in adv_1618.remedy and "msiexec" in adv_1618.remedy

    # 1601: msiexec /regserver
    adv_1601 = advice(1601)
    assert "msiexec /regserver" in adv_1601.remedy


def test_explain_detects_and_explains_mixed_codes():
    text = "CBS error 0x800F081F occurred, while MSI gave 1603 and kernel logged 0xC0000005"
    results = explain(text)
    codes = [c for c, _m in results]
    assert "0x800F081F" in codes
    assert "1603" in codes
    assert "0xC0000005" in codes
