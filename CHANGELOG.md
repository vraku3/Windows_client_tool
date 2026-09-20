# Changelog for Windows Client Tool

## Unreleased

### Added
- **New Scripts tab, with File Forensics as its first tool.** A redesigned,
  professionalized rebuild of a one-off PowerShell script
  (`Find-FileCreator.ps1`): search a folder and see, per file, who has it
  open right now and a ranked guess at which process created it, built on
  the app's own existing process-forensics engine (the same native handle
  enumeration and process-detail lookups Process Explorer uses) rather than
  reimplementing any of it. Adds live folder watching (native
  `ReadDirectoryChangesW`, not polling), SHA256 + VirusTotal lookup,
  kill-locking-process/reveal-in-Explorer actions, and persistent, searchable
  run history with CSV export.
- **Group Policy: the pane was rebuilt, and it now shows the settings.** It
  listed GPO names and nothing else — double-clicking a row did nothing,
  because the rows had no children: the settings each GPO delivered were
  parsed into a dict key no code ever read. Settings are now the bulk of the
  tree, nested by their own category path the way gpedit nests them, under
  separate **Computer Configuration** and **User Configuration** roots, with a
  filter box over the lot.
- **Group Policy: local policy is read from `Registry.pol` directly.**
  `gpresult` refuses the computer half of the report without elevation, but
  the policy *file* is world-readable — so on a machine that is not
  domain-joined, where local policy is the only policy there is, the pane now
  shows it in full with no UAC prompt (`pol_parser.py`, a PReg decoder).
- **Group Policy: each local-policy row says whether it actually took
  effect.** The `.pol` file states what policy *asks* for; the registry says
  what the machine *does*. `policy_drift.py` reads both and reports `applied`,
  `different`, `missing` or `unreadable` — the fourth being the point, since
  "we were denied access to look" is not "the value is not set".
- **Group Policy: a Policy Audit root**, for two findings Windows itself will
  never report:
  - *Set outside Group Policy* (tattooed values) — things sitting in policy's
    four managed branches that no `Registry.pol` accounts for. They survive
    `gpupdate /force` forever, never appear in gpedit, and are attributed to
    no GPO. Grouped by branch, because a flat list reads as far more alarming
    than it is: Windows' own shipped UAC defaults are technically tattooed.
  - *Tweaks that write into policy keys* — 286 of this app's own registry
    steps write into managed branches, where the Registry client-side
    extension can undo them with no error and no trace in the revert log. A
    tweak writing the same data as policy is reported as duplicating it; one
    writing different data as fighting it.
- **Group Policy: raw registry locations are resolved to their gpedit names.**
  `admx_catalog.py` builds an offline index over the 224 ADMX files in
  `C:\Windows\PolicyDefinitions` (3,552 policies and 4,629 (key, value) pairs
  here, built in 0.17s), so a row reading `...\CloudContent  DisableWindowsConsumerFeatures
  = 1` is labelled "Turn off Microsoft consumer experiences" with its explain
  text and its path under Administrative Templates.
- **Group Policy: a "Refresh Policy" dialog** that runs `gpupdate` with live
  streamed output, a target selector and a working Cancel. The verdict comes
  from the output text, not the exit code — unelevated, refreshing computer
  policy fails in the middle of an otherwise normal run that still exits 0,
  and that is a *partial* refresh, neither success nor failure.
- **Group Policy: snapshots and comparison.** "Snapshot" freezes the current
  report to `%APPDATA%/WindowsTweaker/gpresult_snapshots`; "Compare..." diffs
  the report on screen against a saved one — added, removed and changed
  settings, GPOs and extensions. A scope that merely *became visible* (running
  elevated after running unelevated) is reported as a visibility change and
  its contents are not walked, so several hundred settings appearing at once
  is not announced as the machine having changed.
- **Group Policy: Export HTML**, Microsoft's own full RSOP report, plus
  buttons for `gpedit.msc` and `rsop.msc` that disable themselves with an
  explanation on editions where those consoles are not installed.
- **Firewall Rules: Unblock Program and Unblock Folder.** Pick an executable
  (or a folder, for everything under it) and every Block rule pointing at it,
  in either direction, is listed for confirmation and then deleted. Paths are
  compared env-expanded and case-folded, because netsh reports plenty of
  built-in rules with the variable unexpanded
  (`%SystemRoot%\system32\svchost.exe`) and a raw string compare misses them.
  Each rule is deleted narrowed by name **and** program **and** direction —
  firewall rule names are not unique, and deleting by name alone takes
  same-named rules for other programs with it.
- **Firewall Rules: the table zooms.** Ctrl+wheel, Ctrl+plus/minus/0, or the
  A- / A / A+ buttons; the chosen size is remembered across launches. "Fit
  Columns" sizes every column to its widest visible value.
- **Restore Manager: restore points can be deleted.** "Delete Selected" and
  "Keep Only Latest", both confirmed first, via `srclient.dll`'s
  `SRRemoveRestorePoint`. Points with no usable sequence number are skipped
  rather than guessed at — a wrong number deletes somebody else's restore
  point — and without elevation the OS's `ERROR_ACCESS_DENIED` is reported
  rather than swallowed.
- **Tweaks: 306 new tweaks across 10 new categories** — Explorer, Taskbar &
  Start, Power, Input, Windows Update, Defender & Firewall, Browsers, Storage,
  Multimedia and Remote Access. The tab now carries ~700 tweaks over 20
  categories.
- **Tweaks: a "Not Applicable" status.** A tweak whose target service,
  scheduled task or app is not on this machine, or whose `applies_to` gate
  excludes this Windows build, edition or architecture, now says so instead of
  reporting "Unknown" or a permanent "Not Applied". Such rows cannot be ticked
  or applied, and the search bar reads the reason text, so typing "not
  installed" collects them.
- **Tweaks: every status carries a reason.** The Status column's tooltip and
  the details panel state which value was read, what it was, and what the
  tweak wanted — "HKLM\...\EnableLMHOSTS is 1, this tweak wants 0".
- **Tweaks: a "Partial" status** for a multi-step tweak where only some steps
  are in place. Previously only the *first* step was ever checked, so a
  half-applied tweak reported whatever its first step happened to say.
- `applies_to` on a tweak definition, gating on `min_build`/`max_build` (build
  number or alias such as `"23H2"`), `os`, `editions`, `not_editions`, `arch`,
  `requires_gpedit` (Home and S editions write policy values and then ignore
  them), `client_only` and `server_only`.
- `detect` on a tweak definition — a read-only probe that makes a
  `command`/`script` tweak checkable. Probe types: any step type plus
  `registry_key_exists`, `registry_key_absent`, `powershell`, `file_exists`,
  `file_absent`, and `none` for repair actions with no state to read back.
- A `registry_delete` step type, for behaviour that is only off when the value
  is absent rather than zero.
- `tests/test_tweak_definitions.py` — structural validation of every JSON
  definition file: unique ids, known hives and step types, DWORD-vs-SZ data
  types, absolute scheduled-task paths, and a `detect` block on every
  command-driven tweak.

- **Composite modules** — a `CompositeModule` hosts other modules as tabs, so
  a feature made of sibling views is one sidebar entry instead of four. Child
  widgets are built when their tab is first shown, the visible child alone
  receives `on_activate`/`on_deactivate` (so hidden tabs stop polling), and a
  child needing elevation becomes a disabled tab rather than a missing one.
- Session logs are now capped by count as well as age
  (`app.log_retention_count`, default 20). One file per launch meant the
  30-day rule never fired on a working day; 105 had accumulated in two.

- **TreeSize** — a disk-space analyser built to match TreeSize Professional.
  Two scan engines: the NTFS `$MFT` read directly when elevated on an NTFS
  volume, and a `FindFirstFileExW` walk everywhere else. Squarified treemap,
  pie and bar charts; Details, Extensions, File groups, Users, Age of Files
  and Top Files views; saved scans, snapshots, comparison and History; a
  duplicate finder and file search; scheduled scans; live watching of the
  scanned folder; file operations through `IFileOperation`; exports to CSV,
  Excel, PDF, HTML, XML, JSON, SQLite and text; and eight scan targets
  including SSH, WebDAV, Outlook, S3, Azure Blob, SharePoint and Google Drive.
- **Log Viewer** — a CMTrace-style viewer for ConfigMgr and plain-text logs,
  with severity colouring, live tailing, a case-insensitive filter, find, and
  bounded reads so a 300 MB log opens at its tail in about a second.
- OAuth 2.0 device flow (RFC 8628) for SharePoint and Google Drive, replacing
  pasted access tokens that expired after an hour.
- Explorer-style quick scan locations on the TreeSize Home tab, resolved from
  the environment and from `SHGetKnownFolderPath` so redirected Desktop and
  Documents folders are found.

### Changed
- **Tweaks status detection was rewritten.** It checked only the first step of
  each tweak and treated a missing registry key as "Unknown"; on a clean
  install that made most of the Network tab unreadable. All steps are now
  checked and aggregated, and a key or value that does not exist is reported as
  "Not Applied" — Windows sitting at its default is a definite answer, not an
  absence of one. "Unknown" is now reserved for cases where the check genuinely
  could not run, and always says why.
- Tweaks status sweeps run across a thread pool. The cost is dominated by
  process launches (schtasks, powercfg, PowerShell) that block with the GIL
  released, so the full 696-tweak sweep went from ~17 s to ~6 s. Installed
  UWP packages are now enumerated once per session instead of one
  `Get-AppxPackage` launch per row.
- Tweaks category tabs show `(applied/applicable)` counts, and the status
  filter gained Applicable, Partial and Not Applicable. The search bar shows
  which Windows build, edition and architecture the verdicts are judged
  against.

- The sidebar is 34 entries, down from 41, with `TOOLS` down from 15 to 10.
  Nothing was dropped except Duplicate Finder:
  - **Debloat** now hosts Store Apps.
  - **Startup & Boot** replaces Startup Manager, Boot Analyzer and Power & Boot.
  - **Network Diagnostics** now hosts Wi-Fi Analyzer, Hosts Editor and Network
    Extras. Network Extras' duplicate HOSTS tab is gone; the Hosts Editor
    module was the better of the two editors.
  - **Diagnose** now hosts six real modules instead of building all six panes
    itself. `LogPane` is the single implementation behind every one of them.
- **Duplicate Finder was removed.** It MD5-hashed every file in full; TreeSize
  groups by size first and never hashes a file whose size is unique. The name
  still navigates to TreeSize.
- **The Management + Network consolidation.** The sidebar is 28 entries, down
  from 32:
  - **System Management** replaces Scheduled Tasks, Services and Windows
    Features — three unrelated-by-code, thematically-similar `MANAGE` panes,
    now one hub with a tab each.
  - **Network Diagnostics** additionally hosts Shared Resources and Remote
    Tools, on top of the diagnostics/Wi-Fi/HOSTS/Network Extras tabs it
    already had — six tabs total, all network-facing `TOOLS` entries that had
    nothing to do with their old sidebar neighbors.
  - All five absorbed modules are pure re-hosts: none of their own code
    changed, and each is still reachable by name through `route_map()`.

### Fixed
- **The admin banner took half the window.** Unelevated, "Some features
  require administrator privileges." was drawn 450px tall on a 900px window,
  with the entire application squeezed into the bottom half. The banner is a
  plain QWidget (Preferred, so growable) and the splitter under it is a
  *horizontal* QSplitter, which Qt gives the Expanding flag only across, not
  down — so nothing in the root layout claimed the spare height and Qt split
  it equally between the two. The splitter now carries the stretch factor.
  Elevated there is no banner, and it also vanished whenever the search
  results table appeared, which is how it lasted this long.
- **Group Policy: user policy was reported as computer policy.** The parser
  walked the whole document with a single `root.iter()` and filed everything
  it found under `computer_gpos`, so every user-scope GPO was mislabelled.
  `<ComputerResults>` and `<UserResults>` are now walked separately and cannot
  be confused.
- **Group Policy: unelevated, half the report went missing silently.**
  `gpresult /x` exits 0 and writes well-formed XML containing only
  `<UserResults>` — the computer half is refused with no error anywhere. The
  pane showed the user half and said nothing. It now asks for each scope
  explicitly, which is what makes the refusal visible, and a scope that was
  not collected says so in a banner with the reason.
- **Firewall Rules: package rules were listed under their resource strings.**
  Enabling or deleting a rule named `@{Microsoft.WindowsStore_...?ms-resource://...}`
  went through the raw name; it is now resolved to its display name via
  `SHLoadIndirectString` first, falling back to the raw name if that fails.
- **Tweaks: "Select Applied" also selected every "Not Applied" row.** The
  filter tested `"Applied" in label_text`, and one string contains the other.
  Selection and filtering now match on the stored status code.
- Five tweaks targeted service names that exist on no current Windows build
  and so could never do anything: `utcsvc` (DiagTrack's display name, not its
  service name), `wmisvc` (a typo for `wisvc`), `wmpcsvc`, `UxSvc` (DWM
  stopped being a service in Windows 8) and `SrumSvc`. Each duplicated a
  correctly-named entry elsewhere, and all five are gone. `TabletInputService`
  became `TextInputManagementService` and `CldStorSvc` became
  `WinHttpAutoProxySvc`. Surfacing these was what the new "Not Applicable"
  status was for.
- Four tweak ids were used twice across definition files, so one row's status
  silently overwrote the other's: `disable_wifi_sense`, `disable_llmnr`,
  `disable_wpad` and `disable_search_highlights`.
- `enable_numlock_on_boot` wrote to `HKEY_USERS\...`, which the hive parser
  does not recognise — it fell back to HKLM and wrote into the wrong hive
  entirely. Now `HKU\.DEFAULT\...`.
- Five registry steps declared `kind: SZ` with integer data, which raises
  inside `SetValueEx` at apply time, after a restore point has been taken.
- `enable_secure_boot` only ran `Confirm-SecureBootUEFI`, which reads the
  state and changes nothing — Secure Boot is a firmware setting. It is now
  honestly named "Check Secure Boot Status".
- `disable_auto_logger` added a Deny *audit* rule, which does not stop the
  trace session; it now sets the listener's own `Start` value.
- `use_24hour_clock` called `Set-Culture en-GB`, changing the entire system
  locale to reach a clock format. It now writes the two time-format values.
- `win11_copilot_disable` renamed a System32 binary, which Windows Resource
  Protection restores at the next servicing pass. It now uses the supported
  `TurnOffWindowsCopilot` policy.
- `disable_loading_drivers_test` wrote a value under `KnownDLLs` that does not
  exist; driver test signing lives in the boot configuration.
- `disable_tcp_scaling` ran `netsh ... rss=enabled autotuninglevel=normal`,
  which does not disable window scaling and contradicted `disable_rss`.
- `enable_dns_over_https` only set two DNS resolvers without enabling DoH; it
  now sets `EnableAutoDoh` as well.
- 26 `schtasks /change ... /disable` shell-outs became real `scheduled_task`
  steps, so they are detectable and revertable rather than opaque commands.

- Six diagnostic search sources answered nothing. Folding Event Viewer, CBS,
  DISM, Windows Update, Reliability and Crash Dumps into Diagnose left their
  providers registered with no module, while the filter panel went on offering
  all six as sources. A module may now contribute several search providers,
  and the composite hands over its children's.
- Wi-Fi Analyzer crashed on shutdown if its widget had never been built:
  `_stop_scan` reached `self._progress`, which only `create_widget` creates.
  Harmless while every module's widget was built eagerly at startup, fatal for
  a composite tab nobody opened.
- `list_restore_points` ordered by timestamp alone. Restore points created in
  the same clock tick came back in the order SQLite happened to scan a random
  UUID index, so reverting "the most recent" could revert a different one.
- PerfMon's `cleanup_old` deleted records strictly older than the cutoff, so
  `days=0` left behind anything written in the same tick as the call.
- Excel exports past 1,048,576 rows produced a file Excel refuses to open.
  openpyxl does not enforce the limit; the export reported success.
- The text and SQLite exporters crashed on a row shorter than the header
  while the Excel one tolerated it.
- Added comprehensive docstrings to core modules

### Added (earlier)
- LICENSE file (MIT)
- CHANGELOG.md

## [0.1.0] - 2026-03-23

### Added
- Initial release with 18 system optimization modules
