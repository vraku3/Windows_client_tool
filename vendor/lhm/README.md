# LibreHardwareMonitor 0.9.6 (library only)

From the official release `LibreHardwareMonitor.zip` v0.9.6
(https://github.com/LibreHardwareMonitor/LibreHardwareMonitor/releases/tag/v0.9.6,
zip SHA-256 086d9f1b5a99e643edc2cfaaac16051685b551e4c5ac0b32a57c58c0e529c001),
keeping `LibreHardwareMonitorLib.dll` and its runtime dependencies; the UI
assemblies (Aga.Controls, OxyPlot, TaskScheduler, the .exe) are left out.
MPL-2.0 -- see LICENSE.txt. Loaded on .NET Framework 4.8 through pythonnet by
`src/modules/thermal_control/engine/lhm_bridge.py`. Hardware access goes
through the PawnIO driver (`winget install namazso.PawnIO`), which is not
bundled.
