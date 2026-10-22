"""Best-effort unattended notification without a QApplication or a blocking wait."""
import base64
import logging
import os
import subprocess

from core.system_paths import powershell_exe

logger = logging.getLogger(__name__)


def notify_finished(message: str, app_data_dir: str) -> None:
    # A short-lived shell process owns the tray icon long enough to display it;
    # the maintenance process does not wait for its notification window.
    literal = message.replace("'", "''")
    script = f"""
$ErrorActionPreference = 'Stop'
try {{
    Add-Type -AssemblyName System.Windows.Forms
    Add-Type -AssemblyName System.Drawing
    $maintenanceIcon = New-Object System.Windows.Forms.NotifyIcon
    $maintenanceIcon.Icon = [System.Drawing.SystemIcons]::Information
    $maintenanceIcon.Visible = $true
    $maintenanceIcon.ShowBalloonTip(10000, 'WinClientTool', '{literal}', [System.Windows.Forms.ToolTipIcon]::Info)
    Start-Sleep -Seconds 10
    $maintenanceIcon.Dispose()
}} catch {{ Write-Error $_; exit 1 }}
"""
    try:
        folder = os.path.join(app_data_dir, "updates")
        os.makedirs(folder, exist_ok=True)
        encoded = base64.b64encode(script.encode("utf-16-le")).decode("ascii")
        with open(os.path.join(folder, "notification.log"), "ab") as output:
            subprocess.Popen([powershell_exe(), "-NoProfile", "-NonInteractive", "-WindowStyle", "Hidden",
                              "-EncodedCommand", encoded], stdout=output, stderr=output,
                             creationflags=subprocess.CREATE_NO_WINDOW)
    except Exception:
        logger.warning("Unattended completion notification failed", exc_info=True)
