"""Tests for top 10 Windows kernel bugcheck parameter decoding, sysadmin explanations,
and third-party driver culprit fingerprinting.
"""
from datetime import datetime
import pytest

from core.diag_knowledge import bugcheck_info, bugcheck_label
from core.types import LogEntry
from modules.crash_dumps.driver_fingerprint import (
    KNOWN_DRIVERS,
    extract_driver_culprit,
    get_driver_info,
    is_third_party_driver,
)
from modules.crash_dumps.rca_engine import (
    correlate_incidents,
    decode_bugcheck_params,
    get_bugcheck_sysadmin_explanation,
)
from modules.crash_dumps import crash_dump_reader as cdr
from modules.crash_dumps.dump_parser import DumpInfo


TOP_10_BUGCHECKS = [
    (0x0A, "IRQL_NOT_LESS_OR_EQUAL", (0xFFFF80001234, 2, 0, 0xFFFFF8004567)),
    (0x1E, "KMODE_EXCEPTION_NOT_HANDLED", (0xC0000005, 0xFFFFF8001122, 0, 0xFFFF80009988)),
    (0x3B, "SYSTEM_SERVICE_EXCEPTION", (0xC0000005, 0xFFFFF8003344, 0xFFFF80005566, 0)),
    (0x50, "PAGE_FAULT_IN_NONPAGED_AREA", (0xFFFF90001122, 1, 0xFFFFF8007788, 0)),
    (0x7E, "SYSTEM_THREAD_EXCEPTION_NOT_HANDLED", (0xC0000005, 0xFFFFF8002233, 0xFFFF80004455, 0xFFFF80006677)),
    (0x9F, "DRIVER_POWER_STATE_FAILURE", (3, 0xFFFF8000AAAA, 0xFFFF8000BBBB, 0xFFFF8000CCCC)),
    (0xD1, "DRIVER_IRQL_NOT_LESS_OR_EQUAL", (0xFFFF80001000, 2, 1, 0xFFFFF800DDEE)),
    (0x124, "WHEA_UNCORRECTABLE_ERROR", (0, 0xFFFF80009999, 0xBE000000, 0x0000010A)),
    (0x133, "DPC_WATCHDOG_VIOLATION", (0, 0x1F4, 0x1F4, 0)),
    (0x139, "KERNEL_SECURITY_CHECK_FAILURE", (3, 0xFFFFF8005555, 0xFFFF80006666, 0)),
]


@pytest.mark.parametrize("code,expected_name,params", TOP_10_BUGCHECKS)
def test_top_10_bugchecks_have_comprehensive_decoding(code, expected_name, params):
    # 1. Label and knowledge
    label = bugcheck_label(code)
    assert expected_name in label

    info = bugcheck_info(code)
    assert info is not None
    assert info.name == expected_name

    # 2. Plain-English sysadmin explanation
    explanation = get_bugcheck_sysadmin_explanation(code, params)
    assert explanation and len(explanation) > 30

    # 3. Parameter decoding returns 4 plain-English parameter lines
    decoded = decode_bugcheck_params(code, params)
    assert len(decoded) == 4
    for i, line in enumerate(decoded):
        assert line.startswith(f"Param {i+1}:")
        assert "—" in line


def test_decode_params_specific_phrasing():
    # 0x0A: IRQL_NOT_LESS_OR_EQUAL
    dec_0a = decode_bugcheck_params(0x0A, (0x1000, 2, 1, 0x5000))
    assert "DISPATCH_LEVEL" in dec_0a[1]
    assert "Operation: Write" in dec_0a[2]

    # 0x1E: KMODE_EXCEPTION_NOT_HANDLED
    dec_1e = decode_bugcheck_params(0x1E, (0xC0000005, 0x2000, 0, 0x3000))
    assert "STATUS_ACCESS_VIOLATION" in dec_1e[0]

    # 0x3B: SYSTEM_SERVICE_EXCEPTION
    dec_3b = decode_bugcheck_params(0x3B, (0xC0000005, 0x2000, 0x3000, 0))
    assert "STATUS_ACCESS_VIOLATION" in dec_3b[0]
    assert ".cxr" in dec_3b[2]

    # 0x50: PAGE_FAULT_IN_NONPAGED_AREA
    dec_50 = decode_bugcheck_params(0x50, (0x1000, 1, 0x2000, 0))
    assert "Operation: Write" in dec_50[1]
    assert "nonpaged memory" in dec_50[3]

    # 0x7E: SYSTEM_THREAD_EXCEPTION_NOT_HANDLED
    dec_7e = decode_bugcheck_params(0x7E, (0xC0000005, 0x2000, 0x3000, 0x4000))
    assert ".exr" in dec_7e[2]
    assert ".cxr" in dec_7e[3]

    # 0x9F: DRIVER_POWER_STATE_FAILURE
    dec_9f = decode_bugcheck_params(0x9F, (3, 0x2000, 0x3000, 0x4000))
    assert "blocking a power IRP" in dec_9f[0]
    assert "!devobj" in dec_9f[1]
    assert "!irp" in dec_9f[2]
    assert "!drvobj" in dec_9f[3]

    # 0xD1: DRIVER_IRQL_NOT_LESS_OR_EQUAL
    dec_d1 = decode_bugcheck_params(0xD1, (0x1000, 2, 0, 0x2000))
    assert "IRQL at the time" in dec_d1[1]
    assert "DISPATCH_LEVEL" in dec_d1[1]
    assert "Operation: Read" in dec_d1[2]

    # 0x124: WHEA_UNCORRECTABLE_ERROR
    dec_124 = decode_bugcheck_params(0x124, (0, 0x2000, 0x3000, 0x4000))
    assert "Machine Check Exception (MCE)" in dec_124[0]
    assert "!errrec" in dec_124[1]

    # 0x133: DPC_WATCHDOG_VIOLATION
    dec_133 = decode_bugcheck_params(0x133, (0, 0x1F4, 0x1F4, 0))
    assert "Single DPC watchdog timeout" in dec_133[0]

    # 0x139: KERNEL_SECURITY_CHECK_FAILURE
    dec_139 = decode_bugcheck_params(0x139, (3, 0x2000, 0x3000, 0))
    assert "doubly-linked list corruption" in dec_139[0]


def test_third_party_driver_classification():
    # Third-party drivers
    assert is_third_party_driver("nvlddmkm.sys")
    assert is_third_party_driver("amdkmdag.sys")
    assert is_third_party_driver("rtwlane601.sys")
    assert is_third_party_driver("netwtw10.sys")
    assert is_third_party_driver("e2fnexpress.sys")
    assert is_third_party_driver("ene.sys")
    assert is_third_party_driver("asusgio3.sys")
    assert is_third_party_driver("custom_audio.sys")

    # Microsoft core components are NOT third-party drivers
    assert not is_third_party_driver("ntoskrnl.exe")
    assert not is_third_party_driver("hal.dll")
    assert not is_third_party_driver("fltmgr.sys")
    assert not is_third_party_driver("ntfs.sys")
    assert not is_third_party_driver("tcpip.sys")
    assert not is_third_party_driver("dxgkrnl.sys")
    assert not is_third_party_driver("storport.sys")


def test_extract_driver_culprit_from_wer_bucket():
    # AMD LiveKernelEvent bucket
    c1 = extract_driver_culprit("Fault bucket LKD_0x117_Tdr:3_TdrVTR:2_IMAGE_amdkmdag.sys, type 0")
    assert c1 is not None
    assert c1.filename == "amdkmdag.sys"
    assert c1.is_third_party is True
    assert c1.vendor == "AMD"
    assert "Graphics" in c1.category

    # NVIDIA Access Violation bucket
    c2 = extract_driver_culprit("Fault bucket AV_nvlddmkm.sys!unknown_function, type 0")
    assert c2 is not None
    assert c2.filename == "nvlddmkm.sys"
    assert c2.is_third_party is True
    assert c2.vendor == "NVIDIA"
    assert "Graphics" in c2.category

    # Realtek Wi-Fi DPC watchdog
    c3 = extract_driver_culprit("Problem signature: P1: 133, P9: rtwlane601.sys")
    assert c3 is not None
    assert c3.filename == "rtwlane601.sys"
    assert c3.is_third_party is True
    assert c3.vendor == "Realtek"
    assert "Network" in c3.category

    # ENE RGB utility crash
    c4 = extract_driver_culprit("Faulting driver: ene.sys")
    assert c4 is not None
    assert c4.filename == "ene.sys"
    assert c4.is_third_party is True
    assert "RGB" in c4.category


def test_detail_html_surfaces_culprit_card():
    entry = LogEntry(
        timestamp=datetime.now(),
        source="BugCheck event",
        level="Error",
        message="System restarted after bugcheck 0x000000D1 DRIVER_IRQL_NOT_LESS_OR_EQUAL [Culprit: nvlddmkm.sys (NVIDIA)]",
        raw={
            "bug_check": "0x000000D1 DRIVER_IRQL_NOT_LESS_OR_EQUAL",
            "bugcheck_code": "0xD1",
            "params": ["0x1000", "0x2", "0x0", "0x2000"],
            "culprit_driver": "nvlddmkm.sys",
            "faulting_module": "nvlddmkm.sys",
            "is_third_party": True,
            "vendor": "NVIDIA",
            "category": "Graphics (GPU)",
            "remedy": "Clean-install NVIDIA GPU drivers using DDU.",
        },
    )
    html = cdr.detail_html(entry)
    assert "Suspect Driver Culprit:" in html
    assert "nvlddmkm.sys" in html
    assert "THIRD-PARTY DRIVER" in html
    assert "NVIDIA" in html
    assert "Display Driver Uninstaller (DDU)" in html


def test_correlate_incidents_with_culprit():
    entries = [
        LogEntry(
            timestamp=datetime(2026, 11, 1, 12, 0, 0),
            source="Minidump",
            level="Error",
            message="091526-01.dmp (512 KB) - 0x000000D1 DRIVER_IRQL_NOT_LESS_OR_EQUAL [Culprit: rtwlane601.sys (Realtek)]",
            raw={
                "bug_check": "0x000000D1 DRIVER_IRQL_NOT_LESS_OR_EQUAL",
                "bugcheck_code": "0xD1",
                "params": [0x1000, 2, 0, 0x2000],
                "faulting_module": "rtwlane601.sys",
                "culprit_driver": "rtwlane601.sys",
                "is_third_party": True,
            },
        )
    ]
    rca = correlate_incidents(entries)
    assert rca.total_incidents == 1
    assert rca.bsod_count == 1
    inc = rca.incidents[0]
    assert "[Culprit: rtwlane601.sys (Realtek)]" in inc.title
    assert inc.faulting_module == "rtwlane601.sys"
    assert "Realtek" in inc.actionable_advice


def test_detail_panel_remediation_suggests_driver_manager():
    from ui.detail_panel import DetailPanel
    panel = DetailPanel()
    entry = LogEntry(
        timestamp=datetime.now(),
        source="Minidump",
        level="Error",
        message="System crash dump",
        raw={
            "bug_check": "0x000000D1 DRIVER_IRQL_NOT_LESS_OR_EQUAL",
            "culprit_driver": "nvlddmkm.sys",
            "faulting_module": "nvlddmkm.sys",
            "is_third_party": True,
        },
    )
    panel.show_entry(entry)
    assert panel._action_target == "Driver Manager"
    assert "Driver Manager" in panel._remediation_info.text()
    assert not panel._action_btn.isHidden()
