# Audit & Logging Readiness + Persistence Hunting v2 — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Answer the two questions a senior Windows admin asks first on an unknown client — "will this machine still have the evidence I need when something happens?" and "what runs here that Windows did not put there?" — with readings that are verified on this machine and actions that are revertible.

**Architecture:** (A) a Qt-free `log_readiness.py` engine and an `audit_policy.py` engine feed a NEW Security Dashboard category, **Audit & Logging**, built from ordinary `SecurityControl`s, so staging, apply, revert, read-back verification, profiles and the refusal sweep all work unchanged. (B) five new readers in `startup_manager/persistence.py` add the persistence points the existing ten miss, filtering out what Windows itself ships so the list stays a list of findings, not of Windows.

**Tech Stack:** Python 3.12, PyQt6 (UI already exists — no new widgets), `winreg`, `wevtutil`, `auditpol`, WMI over COM (`win32com.client`), pytest.

**Spec:** No separate spec; the requirements are the measurements below (this machine, 2026-10-15, unelevated unless stated).

### Background — measured

| # | Fact | How measured |
|---|---|---|
| M1 | Security, System, Application logs are at the 20 MB default (`maxSize: 20971520`), circular (`retention: false`, `autoBackup: false`). CIS Windows 11 L1 recommends Security >= 196,608 KB, System and Application >= 32,768 KB. | `wevtutil gl <log>` |
| M2 | `Microsoft-Windows-PowerShell/Operational` is FULL (15.0 of 15.0 MB) and its oldest event is 2026-10-14 18:15 — about ONE day of history — because PowerShell Script Block Logging is ON (`HKLM\SOFTWARE\Policies\Microsoft\Windows\PowerShell\ScriptBlockLogging\EnableScriptBlockLogging = 1`) and fills it. | `Get-WinEvent -ListLog` / `-Oldest` |
| M3 | System and Application go back to 2026-09-04 (6 weeks) — size alone is not the metric; COVERAGE (now − oldest event) is. | same |
| M4 | `Microsoft-Windows-TaskScheduler/Operational` is DISABLED (`enabled: false`) — task history, the first thing checked when "something runs at night", is not recorded. | `wevtutil gl` |
| M5 | Reading the Security log, and `auditpol /get`, need elevation: unelevated `Get-WinEvent -LogName Security` = "Attempted to perform an unauthorized operation"; `auditpol` = error 0x522 "A required privilege is not held". Both must be reported as refused, never as empty/clean. The user normally runs the app elevated. | PowerShell |
| M6 | Sysmon is not installed (`Microsoft-Windows-Sysmon/Operational` channel does not exist). | `wevtutil gl` |
| M7 | The existing `check_audit_policy` (security_reader.py ~2309) only COUNTS audited subcategories by parsing localized text ("12 of 60"). Nothing reads or sets the specific subcategories an investigation needs (Process Creation, Logon, Account Lockout, ...). | code read |
| M8 | WMI `root/subscription` is readable unelevated. It holds exactly ONE subscription here: `__EventFilter` "SCM Event Log Filter" -> `NTEventLogEventConsumer` "SCM Event Log Consumer" — WINDOWS' OWN default. Listing it as a finding would be a false alarm on every PC. | `Get-CimInstance -Namespace root/subscription` |
| M9 | `HKLM\SOFTWARE\Microsoft\NetSh` has 21 helper DLLs, all Windows' own (`ifmon.dll`, `authfwcfg.dll`, ...); `...\Control\Print\Monitors` has 6 subkeys; `...\Active Setup\Installed Components` has 25; `...\Windows NT\CurrentVersion\SilentProcessExit` does not exist. Again: list only what is NOT Windows'. | PowerShell |
| M10 | `startup_manager/persistence.py` already covers Run/RunOnce, Startup folders, logon/boot tasks, services, Winlogon, IFEO Debugger, AppInit_DLLs, LSA packages, BootExecute, logon script. Proxy (WinINet vs WinHTTP) is already in Network Health — not part of this plan. | code read |

## Global Constraints

- Windows 10/11 x64, Python 3.12+. English UI strings only, no `tr()`.
- Engines (`log_readiness.py`, `audit_policy.py`, persistence readers) import no PyQt6.
- A refused read is `None`/`available: False` with the reason — never "0 days", "not audited" or "nothing found" (CLAUDE.md Security Dashboard: "a refusal is not an answer").
- Every new `SecurityControl` must satisfy `tests/test_security_catalog_binding.py` and the refusal sweep (`tools/security_refusal_sweep.py`, run unelevated, must report no control answering after a refusal).
- Commands built by this code use only app-owned constants (log names, subcategory GUIDs, byte sizes) — never user text (CLAUDE.md `shell=True` discipline).
- Silent exception swallowing forbidden; colours via theme roles; functions under the length ratchet.
- pytest with `-p no:cacheprovider`, default basetemp.
- Ship: full suite, build portable, deploy to `C:/Users/iorda/OneDrive/1 Personal/Aplicații/`, push.

## Review Focus

1. **Unelevated run** — the Security log, `auditpol` and some channels are refused: every control/reader reports "needs administrator" (`available: False`), never a value. → Task 2 and Task 3 tests; the refusal sweep in Task 6.
2. **Windows' own entries** (the SCM WMI subscription, the 21 netsh helpers, Microsoft Active Setup components, the standard print monitors) never appear as findings. → Task 4/5 tests with the exact names from M8/M9.
3. **A log that does not exist** (Sysmon not installed, a channel missing on an edition) is "not present", distinct from "refused". → Task 1 test.
4. **Localized `auditpol`** — subcategories are addressed by GUID and the CSV (`/r`) output, never by English names in text. → Task 3 test.
5. **Revert** — every write has an off_steps that puts Windows' default back (log sizes 20 MB / 15 MB, channel disabled, cmdline auditing absent, subcategory at its default). → Task 2/3 catalog tests.

---

## File Structure

| File | New? | Responsibility |
|---|---|---|
| `src/modules/security_dashboard/log_readiness.py` | new | Qt-free: `LogStatus` per channel (enabled, max bytes, mode, oldest event, coverage days, refused/absent reason) from `wevtutil gl` + `wevtutil qe /c:1 /rd:false /f:xml`; `sysmon_status()`. |
| `src/modules/security_dashboard/audit_policy.py` | new | Qt-free: read one subcategory by GUID via `auditpol /get /subcategory:{GUID} /r` (CSV); `SUBCATEGORIES` table (GUID, name, wanted setting, Windows default). |
| `src/modules/security_dashboard/catalog/audit_logging.py` | new | `CONTROLS` for the new category; readers wrap the two engines into the dashboard's reader dict shape. |
| `src/modules/security_dashboard/catalog/model.py` | modify | `Category.AUDIT_LOGGING = "Audit & Logging"`. |
| `src/modules/security_dashboard/catalog/__init__.py` | modify | register the new catalog module (follow how the other seven are registered). |
| `src/modules/startup_manager/persistence.py` | modify | `read_wmi_subscriptions`, `read_silent_process_exit`, `read_active_setup`, `read_print_monitors`, `read_netsh_helpers`; wired into `collect()`; `Item.source` doc updated. |
| `pyinstaller_common.py` | modify | `HIDDEN_IMPORTS` += the three new modules. |
| `CLAUDE.md` | modify | Security Dashboard + Startup & Boot notes. |
| tests | new | `tests/test_log_readiness.py`, `tests/test_audit_policy_engine.py`, `tests/test_audit_logging_catalog.py`, `tests/test_persistence_v2.py`. |

---

### Task 1: `log_readiness.py` — what each important log keeps, and for how long

**Files:** Create `src/modules/security_dashboard/log_readiness.py`; Test `tests/test_log_readiness.py`.

**Interfaces — Produces:**
```python
@dataclass
class LogStatus:
    name: str
    present: Optional[bool]          # False = channel does not exist; None = could not ask
    enabled: Optional[bool] = None
    max_bytes: Optional[int] = None
    mode: Optional[str] = None       # "circular" | "archive" (autoBackup) | "retain" (retention, no backup)
    oldest: Optional[datetime] = None  # UTC
    refused: Optional[str] = None    # reason when the read was refused (access denied)
    def coverage_days(self, now: datetime) -> Optional[float]  # None unless oldest is known

IMPORTANT_LOGS = ("Security", "System", "Application",
                  "Microsoft-Windows-PowerShell/Operational",
                  "Microsoft-Windows-TaskScheduler/Operational")
def parse_gl(text: str) -> Dict[str, str]          # `wevtutil gl` -> flat {"enabled": "true", "maxSize": "...", "retention": ..., "autoBackup": ...}
def parse_oldest(xml_text: str) -> Optional[datetime]   # TimeCreated SystemTime of the first <Event>
def read_log(name: str, run=None) -> LogStatus       # run(args) -> (rc, stdout, stderr); default runs wevtutil with CREATE_NO_WINDOW, timeout 20 s
def read_all(run=None) -> List[LogStatus]
def sysmon_status(run=None) -> LogStatus              # read_log("Microsoft-Windows-Sysmon/Operational")
```

Rules (each from M1–M6):
- `wevtutil gl` failing with "channel ... was not found" / exit 15007 → `present=False` (not refused). Access denied (exit 5 or text "Access is denied") → `refused="needs administrator rights"`, `present=None`.
- mode: `autoBackup: true` → "archive"; `retention: true` and autoBackup false → "retain"; else "circular".
- oldest: `wevtutil qe <name> /c:1 /rd:false /f:xml`; an empty log (no `<Event`) → `oldest=None` with no refusal; access denied → `refused`.
- Never raise; a timeout is `refused="wevtutil timed out"`.

- [ ] **Step 1: tests** (synthetic + one real-machine):

```python
# tests/test_log_readiness.py
from datetime import datetime, timezone
from modules.security_dashboard import log_readiness as lr

GL = """name: System
enabled: true
type: Admin
owningPublisher:
isolation: Application
channelAccess: O:BAG:SYD:(A;;0xf0007;;;SY)
logging:
  logFileName: %SystemRoot%\\System32\\Winevt\\Logs\\System.evtx
  retention: false
  autoBackup: false
  maxSize: 20971520
publishing:
  fileMax: 1
"""
XML = "<Event xmlns='http://schemas.microsoft.com/win/2004/08/events/event'><System><TimeCreated SystemTime='2026-09-04T18:23:11.5310000Z'/></System></Event>"


def fake_run(gl=GL, qe=XML, gl_rc=0, qe_rc=0):
    def run(args):
        if args[1] == "gl":
            return gl_rc, gl if gl_rc == 0 else "", "" if gl_rc == 0 else gl
        return qe_rc, qe if qe_rc == 0 else "", "" if qe_rc == 0 else qe
    return run


def test_parse_gl_reads_the_logging_section():
    d = lr.parse_gl(GL)
    assert (d["enabled"], d["maxSize"], d["retention"], d["autoBackup"]) == ("true", "20971520", "false", "false")


def test_a_circular_20mb_log_with_its_oldest_event():
    s = lr.read_log("System", run=fake_run())
    assert (s.present, s.enabled, s.max_bytes, s.mode) == (True, True, 20971520, "circular")
    assert s.oldest == datetime(2026, 9, 4, 18, 23, 11, 531000, tzinfo=timezone.utc)
    assert round(s.coverage_days(datetime(2026, 10, 15, 18, 23, 11, 531000, tzinfo=timezone.utc))) == 41


def test_a_missing_channel_is_absent_not_refused():
    s = lr.read_log("Microsoft-Windows-Sysmon/Operational",
                    run=fake_run(gl="Failed to read configuration for log Microsoft-Windows-Sysmon/Operational. The specified channel could not be found.", gl_rc=15007))
    assert s.present is False and s.refused is None


def test_access_denied_is_refused_never_empty():
    s = lr.read_log("Security", run=fake_run(qe="Access is denied.", qe_rc=5))
    assert s.refused and s.oldest is None and s.coverage_days(datetime.now(timezone.utc)) is None


def test_archive_and_retain_modes():
    assert lr.read_log("X", run=fake_run(gl=GL.replace("autoBackup: false", "autoBackup: true"))).mode == "archive"
    assert lr.read_log("X", run=fake_run(gl=GL.replace("retention: false", "retention: true"))).mode == "retain"


def test_real_system_log_is_readable_unelevated():
    s = lr.read_log("System")
    assert s.present is True and s.max_bytes and s.oldest is not None
```

- [ ] **Step 2:** run, see them fail (ImportError).
- [ ] **Step 3:** implement per the interface and rules above (`subprocess.run([...], capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=20, creationflags=subprocess.CREATE_NO_WINDOW)`; regex `SystemTime='([^']+)'`, parse with `datetime.fromisoformat` after trimming to microseconds and replacing `Z` with `+00:00`).
- [ ] **Step 4:** tests pass; ratchets `tests/test_no_silent_swallow.py tests/test_function_lengths.py`.
- [ ] **Step 5:** commit `feat(security): log readiness engine -- size, mode and real coverage of the logs an investigation needs`.

---

### Task 2: "Audit & Logging" category — log size, channel and command-line controls

**Files:** Modify `catalog/model.py`, `catalog/__init__.py`; Create `catalog/audit_logging.py`; Test `tests/test_audit_logging_catalog.py`.

**Interfaces — Consumes:** `log_readiness.read_log`, `LogStatus`. **Produces:** `Category.AUDIT_LOGGING`; controls with ids
`security_log_size`, `system_log_size`, `application_log_size`, `powershell_log_size`, `taskscheduler_log_enabled`, `process_cmdline_auditing`, `sysmon_installed`.

Control table (desired value is what `read_value` compares; writes are `command` steps with constant arguments, matching how `guest_account` uses `net user`):

| id | reader returns | desired | on_steps | off_steps (Windows default) | risk |
|---|---|---|---|---|---|
| security_log_size | `enabled` = max_bytes >= 196,608 KB (201,326,592 B) | True | `wevtutil sl Security /ms:201326592` | `wevtutil sl Security /ms:20971520` | LOW |
| system_log_size | max_bytes >= 32,768 KB (33,554,432 B) | True | `wevtutil sl System /ms:33554432` | `/ms:20971520` | LOW |
| application_log_size | same as System | True | `wevtutil sl Application /ms:33554432` | `/ms:20971520` | LOW |
| powershell_log_size | max_bytes >= 256 MB (268,435,456 B) — M2: 15 MB covered ONE day with script block logging on | True | `wevtutil sl Microsoft-Windows-PowerShell/Operational /ms:268435456` | `/ms:15728640` | LOW |
| taskscheduler_log_enabled | enabled | True | `wevtutil sl Microsoft-Windows-TaskScheduler/Operational /e:true` | `/e:false` | LOW |
| process_cmdline_auditing | registry `HKLM\SOFTWARE\Microsoft\Windows\CurrentVersion\Policies\System\Audit` `ProcessCreationIncludeCmdLine_Enabled` == 1 | True | registry step DWORD 1 (use the catalog's existing `_dword` helper pattern) | `registry_delete` of that value | LOW |
| sysmon_installed | `enabled` = Sysmon channel present | True | — (`read_only_reason`: "Sysmon is a Sysinternals kernel driver; install and configure it deliberately, with a reviewed configuration -- this app does not deploy drivers.") | — | — |

Reader dict shape (follow `check_powershell_v2`): `{"status", "color", "available", "enabled", "details": [(label, text), ...]}`. Details must include, for log controls: current size in MB, mode, and **coverage** ("oldest event 2026-10-14 18:15 -- 1.0 day of history") or the refusal reason. A refused read → `available: False`, status "Unknown — needs administrator rights", amber. The size control reads `max_bytes` from `wevtutil gl`, which works unelevated even for Security (M1) — only the coverage line needs elevation there; the control's value is still known, the coverage detail says it could not be read.

Writing `wevtutil sl` takes effect immediately (no reboot); `requires_reboot=False`. `requires_admin=True` for all writes.

- [ ] **Step 1: tests** — `tests/test_audit_logging_catalog.py`:
  - every new control is in the catalog index under `Category.AUDIT_LOGGING` and passes the generic binding test (run `tests/test_security_catalog_binding.py`);
  - with `log_readiness.read_log` monkeypatched to return a 20 MB Security log → `security_log_size.read()` is False; 200 MB → True; refused (present None, refused set) → `read()` is None;
  - `powershell_log_size` details mention coverage in days when oldest is known;
  - every writable control's on/off steps are `command`/`registry`/`registry_delete` dicts whose command strings contain only the constant log names and sizes above;
  - `sysmon_installed` is read-only with a reason.
- [ ] **Step 2:** fail; **Step 3:** implement (add the enum member; register the module like the others in `catalog/__init__.py`; the dashboard UI iterates `Category`, so the tab appears without UI code — confirm by `tests/test_security_unelevated_pane.py`).
- [ ] **Step 4:** run `tests/test_audit_logging_catalog.py tests/test_security_*.py` and the ratchets. Update any test that pins the number of categories or controls with the new totals (state the new numbers in the commit).
- [ ] **Step 5:** commit `feat(security): Audit & Logging category -- logs sized for an investigation, task history on, command lines in 4688`.

---

### Task 3: audit subcategories by GUID, with apply and revert

**Files:** Create `src/modules/security_dashboard/audit_policy.py`; Modify `catalog/audit_logging.py`; Test `tests/test_audit_policy_engine.py`.

**Interfaces — Produces:**
```python
@dataclass(frozen=True)
class Subcategory:
    guid: str            # "{0CCE922B-69AE-11D9-BED3-505054503030}"
    name: str            # English display name, for the UI only
    want_success: bool
    want_failure: bool
    default: str         # Windows client default: "No Auditing" | "Success" | "Failure" | "Success and Failure"

SUBCATEGORIES = (
    Subcategory("{0CCE922B-69AE-11D9-BED3-505054503030}", "Process Creation", True, False, "No Auditing"),
    Subcategory("{0CCE9215-69AE-11D9-BED3-505054503030}", "Logon", True, True, "Success and Failure"),
    Subcategory("{0CCE9217-69AE-11D9-BED3-505054503030}", "Account Lockout", False, True, "Success"),
    Subcategory("{0CCE9237-69AE-11D9-BED3-505054503030}", "Security Group Management", True, False, "Success"),
    Subcategory("{0CCE9235-69AE-11D9-BED3-505054503030}", "User Account Management", True, True, "Success"),
    Subcategory("{0CCE922F-69AE-11D9-BED3-505054503030}", "Audit Policy Change", True, False, "Success"),
)
def parse_r(csv_text: str) -> Optional[Tuple[bool, bool]]   # (success, failure) from the `/r` CSV "Inclusion Setting" column
def read(sub: Subcategory, run=None) -> Tuple[Optional[Tuple[bool, bool]], Optional[str]]   # (setting, refusal reason)
def set_command(sub: Subcategory, success: bool, failure: bool) -> str  # "auditpol /set /subcategory:{GUID} /success:enable /failure:disable"
```

**Before coding, verify the GUIDs and the defaults on THIS machine** (`auditpol /list /subcategory:* /v` — try unelevated; if refused, keep the table and state in the report that the GUIDs are the documented ones and were not re-listed). The `/r` CSV columns are `Machine Name,Policy Target,Subcategory,Subcategory GUID,Inclusion Setting,Exclusion Setting`; `Inclusion Setting` text is localized — map by containing "Success"/"Failure" and treat "No Auditing" as (False, False); an unrecognised value → `None` with a reason (never a guess). Error 0x522 / "privilege" in the output → refused "needs administrator rights".

Controls: one per subcategory, id `audit_<snake name>` (e.g. `audit_process_creation`), category AUDIT_LOGGING, `read_value` = True when the current setting includes everything `want_*` asks for; on_steps = `command` `set_command(sub, want_success or current..., ...)` — simplest correct rule: enable exactly the wanted flags plus keep whatever the DEFAULT already enables; off_steps = command restoring `default`. Risk LOW, `requires_admin=True`.

Keep the existing aggregate `audit_policy` control in `accounts.py` unchanged (it is a different, coarse reading).

- [ ] **Step 1: tests** — synthetic CSV for each of the four inclusion texts; a localized-looking unknown value → None; refusal text → refused; `set_command` produces the GUID form; each control's off_steps restores its `default`; reading refused → `control.read()` is None.
- [ ] **Step 2–4:** fail, implement, pass + ratchets + `tests/test_security_*.py`.
- [ ] **Step 5:** commit `feat(security): audit the subcategories an investigation needs -- by GUID, not localized text`.

---

### Task 4: persistence — WMI event subscriptions

**Files:** Modify `src/modules/startup_manager/persistence.py`; Test `tests/test_persistence_v2.py`.

**Interfaces — Produces:** `read_wmi_subscriptions(inv: Inventory, query=None) -> None` where `query(class_name) -> list[dict]` defaults to WMI over COM on `root\subscription` (`win32com.client.GetObject("winmgmts:\\\\.\\root\\subscription")` + `ExecQuery("SELECT * FROM <class>")`; `collect()` already runs on a COMWorker). Item: `source="WMI Subscription"`, `scope="Machine"`, `name` = filter name, `command` = consumer's `CommandLineTemplate` (CommandLineEventConsumer) or `ScriptText`/`ScriptFileName` (ActiveScriptEventConsumer) or the consumer class + name otherwise, `location="root\\subscription"`, `extra` = the filter's `Query`.

Rules:
- A binding links `Filter` and `Consumer` by their WMI paths; list one Item per binding (an unbound filter or consumer is listed too, with extra "not bound" — attackers leave pieces).
- **Windows' own pair is skipped:** filter `SCM Event Log Filter` bound to `NTEventLogEventConsumer` `SCM Event Log Consumer` (M8). Skip by BOTH names AND the consumer class, never by name alone.
- `CommandLineEventConsumer` and `ActiveScriptEventConsumer` get `Note("wmi", "warn", "a WMI subscription that runs a program/script when its event fires -- a common persistence technique")`; other consumer classes get an `info` note.
- A refused/failed query appends to `inv.problems` (never an empty result).

- [ ] **Step 1: tests** — fake `query` returning (a) only the SCM pair → no items; (b) SCM pair + a filter "Updater" bound to a CommandLineEventConsumer `powershell -enc ...` → exactly one item with the warn note and the command; (c) query raising → `inv.problems` names root\subscription; (d) real machine: `read_wmi_subscriptions(inv)` on this PC yields no items and no problems (M8) — run inside `pythoncom.CoInitialize()`.
- [ ] **Step 2–4:** fail, implement, pass + ratchets. Wire into `collect()` after `read_boot_execute`.
- [ ] **Step 5:** commit `feat(startup): WMI event subscriptions -- Windows' own SCM pair excluded`.

---

### Task 5: persistence — SilentProcessExit, Active Setup, Print Monitors, Netsh helpers

**Files:** Modify `src/modules/startup_manager/persistence.py`; Test `tests/test_persistence_v2.py` (append).

**Produces:** `read_silent_process_exit(inv)`, `read_active_setup(inv)`, `read_print_monitors(inv)`, `read_netsh_helpers(inv)`, each wired into `collect()`.

- **SilentProcessExit**: `HKLM\SOFTWARE\Microsoft\Windows NT\CurrentVersion\SilentProcessExit\<image>` with a `MonitorProcess` value → Item `source="SilentProcessExit"`, command = MonitorProcess, extra "runs when <image> exits". The key not existing (M9) → nothing, no problem. Also requires the IFEO `GlobalFlag` 0x200 for it to fire — read `...\Image File Execution Options\<image>\GlobalFlag`; note `info` "inactive: GlobalFlag lacks 0x200" when unset.
- **Active Setup**: `HKLM\SOFTWARE\Microsoft\Active Setup\Installed Components\<GUID>` (and `WOW6432Node`) `StubPath` — runs once per user at logon. Item `source="Active Setup"`.
- **Print Monitors**: `HKLM\SYSTEM\CurrentControlSet\Control\Print\Monitors\<name>` `Driver` (a DLL loaded into spoolsv as SYSTEM). Item `source="Print Monitor"`; a bare DLL name resolves against `%SystemRoot%\System32` (CLAUDE.md driver-path note).
- **Netsh helpers**: values of `HKLM\SOFTWARE\Microsoft\NetSh` (name → DLL, loaded by netsh.exe). Item `source="Netsh Helper"`; bare names resolve against System32.

**Windows' own entries are not findings (M9):** after `enrich()` sets trust/publisher, these four sources keep an item only when `not item.is_microsoft` (Microsoft-signed file) — implement as a post-enrich filter in `collect()` for exactly these four sources (and NOT for the existing sources, whose behaviour is unchanged). A file that cannot be verified (trust UNKNOWN) is KEPT (unverifiable is not "Windows'").

- [ ] **Step 1: tests** — registry readers with a fake hive (monkeypatch `winreg` OpenKey/EnumKey/EnumValue/QueryValueEx as `tests/test_app_buster.py` does), covering: a MonitorProcess entry with and without GlobalFlag 0x200; an Active Setup StubPath; a print monitor with a bare DLL name resolving to System32; a netsh helper; the post-enrich filter keeps a non-Microsoft item and drops a Microsoft-signed one; real machine: `collect()` on this PC produces no `Netsh Helper` items (all 21 are Windows') and no `SilentProcessExit` items.
- [ ] **Step 2–4:** fail, implement, pass + ratchets + existing startup tests (`-k startup or persistence`).
- [ ] **Step 5:** commit `feat(startup): SilentProcessExit, Active Setup, print monitors and netsh helpers -- only what Windows did not ship`.

---

### Task 6: real-machine verification, docs, ship

- [ ] Run unelevated: `python tools/security_refusal_sweep.py` (must report no control answering after a refusal — the new Security-log coverage and audit subcategories are refused unelevated); `python tools/security_catalog_check.py` and read the new category's rows; a short script printing `log_readiness.read_all()` and `persistence.collect()` counts per source. Paste outputs in the report.
- [ ] Off-screen screenshot of the Security Dashboard on the new "Audit & Logging" tab (skill step 7) — save under the session scratchpad and read it back.
- [ ] CLAUDE.md: add to "Security Dashboard" the measured facts M1–M7 and the GUID/CSV rule; to "Startup & Boot" the M8/M9 rule (Windows' own entries filtered by signature, never by name alone; the SCM WMI pair).
- [ ] `HIDDEN_IMPORTS` += `modules.security_dashboard.log_readiness`, `modules.security_dashboard.audit_policy`, `modules.security_dashboard.catalog.audit_logging`.
- [ ] Full suite; commit docs; build portable; deploy; push.

## Out of scope

- Deploying Sysmon (a kernel driver plus a configuration that needs review) — read-only control only.
- Elevated round-trips of the audit/log writes: owed to a run with the user present to approve UAC; until then the writes are verified by the unit tests and by the dashboard's own read-back after apply.
- Proxy, DNS, time sync: already covered by Network Health / other modules (M10).
