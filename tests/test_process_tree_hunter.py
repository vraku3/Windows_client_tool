"""Unit tests for Process Tree and Anomaly Hunter."""
from modules.process.process_tree import ProcessNode, evaluate_anomalies


def test_process_anomaly_evaluation():
    # 1. Benign process
    risk, anom = evaluate_anomalies(
        name="notepad.exe",
        exe=r"C:\Windows\System32\notepad.exe",
        cmdline="notepad.exe doc.txt",
        parent_name="explorer.exe",
        system32_dir=r"C:\Windows\System32",
    )
    assert risk == "CLEAN"
    assert len(anom) == 0

    # 2. Office spawning shell (Macro / Exploit)
    risk, anom = evaluate_anomalies(
        name="powershell.exe",
        exe=r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe",
        cmdline="powershell.exe -w hidden -enc JAB4...",
        parent_name="winword.exe",
        system32_dir=r"C:\Windows\System32",
    )
    assert risk == "CRITICAL"
    assert any("Office parent" in a for a in anom)
    assert any("EncodedCommand" in a for a in anom)

    # 3. Fake svchost running from Temp
    risk, anom = evaluate_anomalies(
        name="svchost.exe",
        exe=r"C:\Users\Public\svchost.exe",
        cmdline="svchost.exe",
        parent_name="explorer.exe",
        system32_dir=r"C:\Windows\System32",
    )
    assert risk == "CRITICAL"
    assert any("outside System32" in a for a in anom)
    assert any("suspicious/temporary" in a for a in anom)

    # 4. Browser spawning cmd
    risk, anom = evaluate_anomalies(
        name="cmd.exe",
        exe=r"C:\Windows\System32\cmd.exe",
        cmdline="cmd.exe /c whoami",
        parent_name="chrome.exe",
        system32_dir=r"C:\Windows\System32",
    )
    assert risk == "HIGH"
    assert any("Web browser" in a for a in anom)


def test_process_hunter_dialog_initialization(qapp):
    from ui.process_hunter_dialog import ProcessHunterDialog
    dlg = ProcessHunterDialog()
    assert dlg.windowTitle() == "Live Process Tree & Anomaly Hunter"
    assert dlg._table.columnCount() == 6
