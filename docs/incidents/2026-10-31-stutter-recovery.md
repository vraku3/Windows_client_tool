# Desktop/game stutter and application startup recovery — 2026-10-31

## What changed and what was restored

The app recorded these effective changes during the day. Recovery restored
the saved values on this PC at approximately 17:55 (Europe/Bucharest).

| Action | Before the day's change | Applied | Restored |
|---|---|---|---|
| Disable Dynamic Lighting | Value absent | `DisableDynamicLighting=1` | Value absent |
| Contacts indexing service | Manual, stopped | Disabled | Manual, stopped |
| Windows License Manager | Manual, running | Disabled | Manual, running |
| Web content in Start | Value absent | `AllowWebContentOnStart=0` | Value absent |
| Visual-effects preference | `VisualFXSetting=3` | `2` | `3` |
| SmartScreen apps/files | `EnableSmartScreen=1` | `0` | `1` |
| Account content suggestions | Value absent | `SubscribedContent-310093Enabled=0` | Value absent |
| Energy recommendations | Value absent | `EnergyRecommendations=0` | Value absent |

The location-policy change recorded earlier in the day was already back at
its original value when recovery was prepared, so it was not changed again.
Many other recorded steps had written the same value that already existed.
Hardware-accelerated GPU scheduling was already enabled before that session;
the session did not introduce a new HAGS value.

## What the evidence establishes

Stutter affected both games and the desktop with WinClientTool closed.
A 20-second ETW capture found no DPC/ISR duration above approximately
0.5 ms. CPU, disk and available memory did not show resource exhaustion.
The display configuration API confirmed 2560x1440 at 144 Hz.

The first user check immediately after recovery still reported stutter.
Minutes later the user reported normal operation and confirmed fluid
gameplay with the application reopened. A requested reboot command failed
with error 203; the boot time remained unchanged. Improvement therefore
occurred in the existing session after grouped recovery, not after a reboot.

This establishes a recovery sequence, not a proven individual cause.
The original eight changes were not reapplied to reproduce the fault.
A BSOD dump from 15:35 reports `IRQL_NOT_LESS_OR_EQUAL` in
`nt!KiInsertTimerTable`, in the CyberGhost service's process context, with
an incomplete stack. It does not identify a proven third-party culprit.
No CyberGhost, antivirus or driver configuration was disabled for this test.

## Separate reason the app did not start

Both Desktop shortcuts contained a literal `?` in the target folder
`Aplica?ii`, instead of Unicode `Aplicații`; neither target existed.
They were backed up and replaced by one Unicode Shell Link to the verified
folder build, with Run as administrator enabled. The new shortcut launched
one responsive elevated app. This is separate from the stutter recovery.

## Prevention

- The application engine refuses disabling core Windows services, app
  licensing, contacts indexing and the AMD X3D optimizer, including equivalent
  structured registry operations. Licensing is no longer called telemetry.
- This PC's eight recovered changes are quarantined in its application data
  safety policy by ID and by structured operation. Presets, direct buttons,
  renamed aliases and batch application use the same preflight.
- A block is evaluated before any step in a tweak. A malformed or unreadable
  safety file refuses application rather than silently ignoring protection.
- Detection still reports the actual setting. Restoration from recorded
  backups remains available. The UI explains blocked actions and excludes
  them from selection and presets.
- The quarantine records uncertainty explicitly. It is not evidence that
  each of the eight settings individually caused stutter.

Recovery history and policy are per user/PC and survive executable rebuilds.
They are not hardcoded to a username in application code. Older executables
predating the safety policy do not enforce it; use the current release.

The native ADLX GPU fan read warning observed during testing is a separate
unresolved issue. No fan curve was activated or changed during recovery.

## Installed protective build

Both regular folder/portable copies and the share's current portable were
replaced with the protective build on 2026-10-31. The share contains the
current and immediately previous portable. Signatures are valid with one
fresh signing identity; prior WinClientTool certificates were removed from
their owning machine/user stores.

- Folder EXE SHA-256: `BEE6CD11612E2C28520B86CE83C81C021A004796AEB6D8DA0B11FFE9733C63C6`
- Portable SHA-256: `D539AA8B23DE245B2AA9C15569635E15C2172C31731BDB109342B1EE46BDD0F5`
- Functional regression suite: 157 passing tests. The safety module's 20
  tests also pass after the recovery-tool multi-step grouping check.
- Global static checks encounter UTF-8 BOMs already present in committed
  source; ten checks fail at parsing those existing files. No check was
  weakened and no unrelated source encoding was changed.

The installed folder build was refused by Windows App Control at 19:09,
with Code Integrity events 3077/3033 and policy
`{0283ac0f-fff1-49ae-ada1-8a933130cad6}`. Its Authenticode signature is valid,
but the policy requires a higher signing trust level. This is not a successful
packaged runtime verification. The portable has not passed a runtime test.

[Microsoft's Smart App Control signing guidance](https://learn.microsoft.com/en-us/windows/apps/develop/smart-app-control/code-signing-for-smart-app-control)
requires a certificate issued by a trusted provider; trusting a fresh local
self-signed certificate does not establish compliance. The signing script's
earlier claim that local trust could satisfy SAC was corrected. Windows
security policy was not weakened. A normal elevated source run was requested
for development validation, but canceled at UAC before it started. The current
protective build therefore has unit/UI-preview verification, valid signatures
and deployed hashes, but no successful post-install runtime check. Use of a
temporary source launcher versus obtaining proper distribution signing is a
user decision. No app instance remains running after these attempts.
