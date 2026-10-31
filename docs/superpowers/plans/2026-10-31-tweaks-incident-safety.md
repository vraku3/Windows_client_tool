# Tweaks incident safety implementation plan

**Goal:** Prevent accidental reapplication of the recovered incident changes, preserve truthful detection and undo, and protect Windows service functionality.

**Architecture:** A Qt-free safety policy validates every tweak before any of its steps execute. Global service protections apply to all PCs; a validated machine-scoped recovery policy quarantines this PC's eight reverted changes by ID and structured operation. The UI displays and respects the same policy. Detection and backup restoration remain separate from application safety.

**Tech stack:** Python, JSON, PyQt6, pytest, existing BackupService and PyInstaller configurations.

- [x] Add failing tests: protected services including registry aliases, whole-tweak preflight, machine scope, corrupt-policy refusal, normal allowed actions, disabled row/preset selection and single/bulk preflight.
- [x] Implement `src/modules/tweaks/safety_policy.py` and integrate `TweakEngine.safety_reason` before mutation. Do not run suspect changes on the real PC.
- [x] Show blocked rows, preserve detected state and undo, and refuse handler calls before creating restore points. Correct misleading catalog descriptions and risk levels.
- [x] Write a durable incident record with original and restored values, evidence limitations and the separate malformed-shortcut cause. Persist this machine's quarantine from the verified recovery record.
- [x] Run focused regression tests, review the changed code and record the lesson. Preserve earlier uncommitted fixes.
- [x] Close the existing app, build/sign/deploy updated folder and portable artifacts, rotate the two portable share versions.
- [ ] Test one elevated installed instance after local UAC confirmation. Verify safety without applying recovered tweaks.

## Evidence boundary

Grouped recovery was followed by user-confirmed fluid operation, without a reboot. No individual tweak was established as the cause. `LicenseManager` is functional licensing infrastructure, not a telemetry-only service. Current user settings restored during recovery must not be re-applied as a causality experiment. Thermal Control's ADLX read warning is separate and outside this change.

## Verification and delivery

- Focused regression suite: 157 passing tests, including engine refusal, UI/preset selection, undo and preset-ID resolution. The safety file's 20 tests also passed after the recovery-tool multi-step grouping check.
- Ruff passes for new policy/tool/tests. The offscreen UI preview is readable with eight disabled rows, a protected-details explanation, and zero selection. No recovered change was reapplied to Windows.
- Repository-wide ratchets were run. Ten checks fail because their AST reader encounters UTF-8 BOMs already present in committed source files; the BOM in `game_mode/engine/frame_capture.py` was verified in HEAD. No ratchet was weakened and no unrelated source encoding was modified. Module inventory and remaining checks completed.
- Both folder and portable builds completed successfully, include the safety module and exclude the ambient ICU DLL. Both were signed with the same fresh build certificate and deployed to the regular application paths and canonical `dist`; hashes match. Share has exactly current and previous portable builds.
- Signing exposed an existing cleanup bug: CurrentUser Root/TrustedPublisher enumeration includes inherited LocalMachine certificates. Removing inherited certificates through the user store fails. The workflow now cleans the owning machine store first when stale own-subject certificates exist, then the user stores. Cleanup was completed elevated; only the fresh certificate remains in CurrentUser My/Root/TrustedPublisher, and no old WinClientTool certificate remains in LocalMachine stores.
- The sequential runtime helper's UAC prompt was canceled before it ran. The direct installed-folder launch was then blocked by App Control (Code Integrity 3077/3033 at 19:09). Its local signature is valid but insufficient for the policy. The portable runtime is unverified. No Windows protection was weakened and no recovered Windows setting was reapplied.
- Corrected the local signing script's misleading claim about Smart App Control compliance. Microsoft requires trusted-provider signing. The elevated source-mode development launch was canceled at UAC before it started. No installed/source app instance remains running. A temporary source launcher versus proper distribution signing is awaiting the user's choice.
