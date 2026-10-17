# src/modules/tweaks/tweak_engine.py
import json
import logging
import subprocess
import winreg
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

from core.backup_service import BackupService, StepRecord
from core.windows_utils import ps_quote
from modules.tweaks.os_context import get_os_context

logger = logging.getLogger(__name__)

_START_TYPE_MAP = {
    "boot": 0, "system": 1, "automatic": 2, "manual": 3, "disabled": 4,
}
_START_TYPE_LABEL = {v: k for k, v in _START_TYPE_MAP.items()}
_HIVE_MAP = {
    "HKLM": winreg.HKEY_LOCAL_MACHINE,
    "HKCU": winreg.HKEY_CURRENT_USER,
    "HKCR": winreg.HKEY_CLASSES_ROOT,
    "HKU":  winreg.HKEY_USERS,
    "HKCC": winreg.HKEY_CURRENT_CONFIG,
}
_KIND_MAP = {
    "DWORD":     winreg.REG_DWORD,
    "QWORD":     winreg.REG_QWORD,
    "SZ":        winreg.REG_SZ,
    "EXPAND_SZ": winreg.REG_EXPAND_SZ,
    "BINARY":    winreg.REG_BINARY,
    "MULTI_SZ":  winreg.REG_MULTI_SZ,
}

# -- status vocabulary -------------------------------------------------------
# "unknown" now means what it says: we genuinely could not find out. A missing
# registry key is NOT unknown — it is the Windows default, i.e. not applied.
APPLIED = "applied"
NOT_APPLIED = "not_applied"
PARTIAL = "partial"
NOT_APPLICABLE = "not_applicable"
UNKNOWN = "unknown"

STATUS_LABELS = {
    APPLIED: "Applied",
    NOT_APPLIED: "Not Applied",
    PARTIAL: "Partially Applied",
    NOT_APPLICABLE: "Not Applicable",
    UNKNOWN: "Unknown",
}

# Windows error codes we care about when reading the registry.
_ERROR_FILE_NOT_FOUND = 2
_ERROR_ACCESS_DENIED = 5

#: Phrases a Windows admin command uses to refuse while still exiting 0.
#: netsh and dism both write their real complaint to STDOUT, not stderr, so a
#: return code is never the deciding signal here.
_REFUSAL_MARKERS = (
    "access is denied", "elevated permissions are required",
    "requires elevation", "you do not have permission",
    "the requested operation requires elevation",
    "no rules match the specified criteria",
)


class StepRefused(RuntimeError):
    """A command step ran and Windows declined to do it.

    Carries the StepRecord built from the evidence (rc/stdout/stderr) that was
    captured just before this was raised — str(exc) is still just the payload
    (blob or "exit code N"), so on_error() callers see the same message either
    way; `.record` is there for a caller that wants the structured fields too.
    """

    def __init__(self, message: str, record: Optional[StepRecord] = None):
        super().__init__(message)
        self.record = record


def _parse_key(full_key: str):
    parts = full_key.split("\\", 1)
    hive = _HIVE_MAP.get(parts[0].upper(), winreg.HKEY_LOCAL_MACHINE)
    sub = parts[1] if len(parts) > 1 else ""
    return hive, sub


def _short_key(full_key: str, value_name: str = "") -> str:
    """`HKLM\\...\\Explorer\\Advanced\\HideFileExt` — enough to identify the
    target in a one-line tooltip without wrapping to three lines."""
    parts = full_key.split("\\")
    tail = "\\".join(parts[-2:]) if len(parts) > 2 else full_key
    head = parts[0]
    shown = f"{head}\\...\\{tail}" if len(parts) > 3 else full_key
    return f"{shown}\\{value_name}" if value_name else shown


def _normalise_expected(expected: Any, kind: int, actual: Any) -> Any:
    """JSON has no bytes and is loose about int-vs-string, so line the expected
    value up with whatever winreg actually handed back before comparing."""
    if kind == winreg.REG_BINARY and isinstance(expected, str):
        text = expected.strip()
        try:
            if " " in text:
                return bytes(int(b, 16) for b in text.split())
            return bytes.fromhex(text)
        except ValueError:
            return expected
    if isinstance(actual, int) and isinstance(expected, str):
        try:
            return int(expected, 0)
        except ValueError:
            return expected
    if isinstance(actual, str) and isinstance(expected, int):
        return str(expected)
    if isinstance(actual, (list, tuple)) and isinstance(expected, str):
        return [expected]
    return expected


def _is_key_existence_step(step: Dict) -> bool:
    """Some tweaks work by the mere presence of a key (the classic
    `...\\Network\\NewNetworkWindowOff`). There is no value to compare — the
    key existing IS the applied state."""
    return step.get("value", "") == "" and step.get("data", "") == ""


@dataclass
class StepStatus:
    """One step's verdict, plus why."""
    status: str
    reason: str = ""
    target: str = ""


@dataclass
class DetectionResult:
    """A tweak's verdict, plus why, plus the per-step breakdown behind it."""
    status: str
    reason: str = ""
    steps: List[StepStatus] = field(default_factory=list)

    @property
    def label(self) -> str:
        return STATUS_LABELS.get(self.status, self.status)


class TweakEngine:
    """Applies and detects tweak definitions (JSON step lists).

    BackupService is the sole undo mechanism — no undo_steps in the JSON.
    """

    def __init__(self, backup_service: BackupService):
        self._backup = backup_service
        self._os = get_os_context()

    # ==================================================================
    # Apply
    # ==================================================================

    def apply_tweak(
        self,
        tweak: Dict,
        rp_id: str,
        on_error: Optional[Callable[[str], None]] = None,
    ) -> bool:
        """Apply all steps. Backs up state first, records steps on success.
        Returns True if all steps succeeded."""
        applicable = self.check_applicable(tweak)
        if not applicable.applicable:
            msg = f"{tweak.get('name', tweak.get('id'))}: not applicable — {applicable.reason}"
            logger.info(msg)
            if on_error:
                on_error(msg)
            return False

        success = True
        for step in tweak.get("steps", []):
            if self._step_is_moot(step):
                continue
            try:
                record = self._apply_step(step, rp_id)
                if record:
                    # Recorded as soon as it is made, not after the whole
                    # tweak: a crash, power loss or failed later step must
                    # not leave a change on the machine with no way back.
                    self._backup.record_steps(tweak["id"], [record], rp_id)
            except Exception as e:
                # A `script` step keeps its command under "command", not
                # "cmd" -- looking only for the latter produced "Step failed
                # (script ): ..." for every refused Set-MpPreference, which
                # names nothing in a batch that runs eighteen of them.
                key_info = (step.get("key") or step.get("name")
                            or step.get("cmd") or step.get("command")
                            or step.get("task_name") or step.get("package")
                            or "")
                msg = f"Step failed ({step.get('type')} {key_info}): {e}"
                logger.error(msg)
                if on_error:
                    on_error(msg)
                success = False
        return success

    def _step_is_moot(self, step: Dict) -> bool:
        """Skip steps whose target is not on this machine — writing a start
        type for a service that was never installed only produces an error the
        user then has to interpret."""
        if step.get("type") == "service":
            return self._os.service_exists(step.get("name", "")) is False
        if step.get("type") == "scheduled_task":
            return self._os.scheduled_task_exists(step.get("task_name", "")) is False
        return False

    def check_applicable(self, tweak: Dict):
        """Does this tweak make sense on *this* Windows install?

        Answers from the `applies_to` block first, then falls back to whether
        the things the steps target exist at all.
        """
        verdict = self._os.evaluate(tweak.get("applies_to"))
        if not verdict.applicable:
            return verdict

        steps = tweak.get("steps", [])
        if not steps:
            return verdict

        reasons: List[str] = []
        for step in steps:
            stype = step.get("type")
            if stype == "service":
                name = step.get("name", "")
                if self._os.service_exists(name) is False:
                    reasons.append(f"service '{name}' is not installed")
                    continue
            elif stype == "scheduled_task":
                task = step.get("task_name", "")
                if self._os.scheduled_task_exists(task) is False:
                    reasons.append(f"scheduled task '{task}' does not exist")
                    continue
            elif stype == "appx":
                pkg = step.get("package", "")
                if self._os.appx_installed(pkg) is False:
                    reasons.append(f"'{pkg}' is not installed")
                    continue
            # Any step that is not provably moot makes the tweak applicable.
            return verdict

        from modules.tweaks.os_context import Applicability
        return Applicability(False, "; ".join(dict.fromkeys(reasons)))

    def _apply_step(self, step: Dict, rp_id: str) -> Optional[StepRecord]:
        step_type = step["type"]
        if step_type == "registry":
            return self._apply_registry(step, rp_id)
        elif step_type == "registry_delete":
            return self._apply_registry_delete(step, rp_id)
        elif step_type == "service":
            return self._apply_service(step, rp_id)
        elif step_type == "command":
            return self._apply_command(step)
        elif step_type == "appx":
            return self._apply_appx(step, rp_id)
        elif step_type == "scheduled_task":
            return self._apply_scheduled_task(step, rp_id)
        elif step_type == "script":
            return self._apply_script(step)
        logger.warning("Unknown step type: %s", step_type)
        return None

    def _apply_registry(self, step: Dict, rp_id: str) -> StepRecord:
        full_key = step["key"]
        value_name = step.get("value", "")
        data = step["data"]
        kind = _KIND_MAP.get(step.get("kind", "DWORD"), winreg.REG_DWORD)
        hive, sub = _parse_key(full_key)

        before = None
        try:
            with winreg.OpenKey(hive, sub) as k:
                before, _ = winreg.QueryValueEx(k, value_name)
        except OSError:
            logger.debug("Ignored OSError", exc_info=True)

        outcome = self._backup.backup_registry_key(full_key, rp_id)
        if not outcome.ok:
            # Not fatal -- the step still applies -- but the log has to
            # name the key we are changing with no way back to it.
            logger.error("applying %s with NO registry backup: %s",
                         full_key, outcome.reason)

        if kind == winreg.REG_BINARY and isinstance(data, str):
            text = data.strip()
            data = (bytes(int(b, 16) for b in text.split())
                    if " " in text else bytes.fromhex(text))

        try:
            with winreg.CreateKeyEx(hive, sub, access=winreg.KEY_SET_VALUE) as k:
                winreg.SetValueEx(k, value_name, 0, kind, data)
        except PermissionError as e:
            # A handful of Windows 11 keys (Feeds, Communications, WindowsAI, ...) are
            # ACL-locked to TrustedInstaller/SYSTEM — even a full Administrator gets
            # denied here. This is an OS permission wall, not a missing-elevation bug;
            # re-raise with that context instead of a bare WinError 5 (confirmed
            # 2026-08-14 on disable_taskbar_news / win11_chat_disable).
            raise PermissionError(
                f"{e}. This key is locked down by Windows itself on some builds "
                "(TrustedInstaller/SYSTEM-only ACL) — Administrator rights alone can't "
                "write it. Try the equivalent Settings app toggle instead if one exists."
            ) from e

        return StepRecord("registry", full_key, before, data,
                          value_name=value_name, reg_kind=kind,
                          # `ok` with nothing exported means the key was not
                          # there to export -- so CreateKeyEx above made it,
                          # and the revert has a key to remove as well as a
                          # value. This is the only moment that knows.
                          key_created=outcome.ok and not outcome.exported)

    def _apply_registry_delete(self, step: Dict, rp_id: str) -> Optional[StepRecord]:
        """Remove a value. Some Windows behaviour is only truly off when the
        value is gone, not when it is set to 0."""
        full_key = step["key"]
        value_name = step.get("value", "")
        hive, sub = _parse_key(full_key)

        before = None
        kind = winreg.REG_DWORD
        try:
            with winreg.OpenKey(hive, sub) as k:
                before, kind = winreg.QueryValueEx(k, value_name)
        except OSError:
            return None  # already gone — nothing to record, nothing to undo

        outcome = self._backup.backup_registry_key(full_key, rp_id)
        if not outcome.ok:
            # Not fatal -- the step still applies -- but the log has to
            # name the key we are changing with no way back to it.
            logger.error("applying %s with NO registry backup: %s",
                         full_key, outcome.reason)
        with winreg.OpenKey(hive, sub, 0, winreg.KEY_SET_VALUE) as k:
            winreg.DeleteValue(k, value_name)
        return StepRecord("registry", full_key, before, None,
                          value_name=value_name, reg_kind=kind)

    def _apply_service(self, step: Dict, rp_id: str) -> StepRecord:
        import win32service
        name = step["name"]
        _st = step.get("start_type", "manual")
        new_start = int(_st) if isinstance(_st, int) else _START_TYPE_MAP.get(str(_st).lower(), 3)

        try:
            self._backup.backup_service_state(name, rp_id)
        except Exception as e:
            logger.warning("backup_service_state failed for %s: %s", name, e)

        before = None
        hscm = None
        hs = None
        try:
            hscm = win32service.OpenSCManager(None, None, win32service.SC_MANAGER_CONNECT)
            hs = win32service.OpenService(
                hscm, name,
                win32service.SERVICE_QUERY_CONFIG | win32service.SERVICE_CHANGE_CONFIG)
            config = win32service.QueryServiceConfig(hs)
            before = config[1]
            try:
                win32service.ChangeServiceConfig(
                    hs, win32service.SERVICE_NO_CHANGE,
                    new_start, win32service.SERVICE_NO_CHANGE,
                    None, None, False, None, None, None, None)
            except Exception as e:
                # Windows protects a few services beyond their own ACL, and
                # refuses an elevated Administrator here. Measured with
                # tools/service_config_probe.py: DoSvc refuses while
                # RemoteRegistry, DiagTrack, SysMain, WSearch, MapsBroker,
                # RetailDemo, WMPNetworkSvc and lfsvc all accept the identical
                # call — and `sc sdshow DoSvc` grants Builtin Administrators
                # DC (SERVICE_CHANGE_CONFIG), so the DACL is not what stops it.
                # The raw pywin32 tuple reads as a bug in this app; it is not.
                if getattr(e, "winerror", None) == 5:
                    raise PermissionError(
                        f"Windows refused to change the start type of "
                        f"'{name}' even with administrator rights — it "
                        "protects this service beyond its own permissions. "
                        "Nothing was changed. Where Windows offers the same "
                        "setting (Settings, or Group Policy), that is the way "
                        "to change it."
                    ) from e
                raise
        finally:
            if hs:
                win32service.CloseServiceHandle(hs)
            if hscm:
                win32service.CloseServiceHandle(hscm)

        return StepRecord("service", name, before, new_start)

    def _apply_command(self, step: Dict) -> StepRecord:
        cmd = step["cmd"]
        proc = subprocess.run(
            cmd, shell=True, check=False, capture_output=True, text=True,
            creationflags=subprocess.CREATE_NO_WINDOW,
            # A `command` step runs an arbitrary line from a bundled tweak
            # definition, and some of them are genuinely slow (DISM, a
            # whole-machine Get-AppxPackage sweep). Generous enough that a
            # working tweak is never reported as a timeout, bounded so a
            # wedged one cannot hold the apply thread for the session.
            # TimeoutExpired surfaces through apply_tweak's per-step handler
            # as "Step failed (command <cmd>): ...".
            timeout=300,
        )
        record = StepRecord("command", cmd, None, None,
                            rc=proc.returncode,
                            stdout=(proc.stdout or "").strip(),
                            stderr=(proc.stderr or "").strip())
        self._raise_if_refused(record, proc.returncode)
        return record

    def _apply_script(self, step: Dict) -> StepRecord:
        cmd = step.get("command", step.get("cmd", ""))
        proc = subprocess.run(
            cmd, shell=True, check=False, capture_output=True, text=True,
            creationflags=subprocess.CREATE_NO_WINDOW,
            timeout=300,   # same reasoning as the `command` step above
        )
        revert_cmd = step.get("revert_command")
        record = StepRecord("script", cmd, None, None, revert_command=revert_cmd,
                            rc=proc.returncode,
                            stdout=(proc.stdout or "").strip(),
                            stderr=(proc.stderr or "").strip())
        self._raise_if_refused(record, proc.returncode)
        return record

    @staticmethod
    def _raise_if_refused(record: StepRecord, returncode: int) -> None:
        """Windows admin commands routinely exit 0 while refusing — netsh and
        dism both write their real complaint to STDOUT. Build the StepRecord
        first so the captured evidence exists, then raise: this is what turns
        a refused command into a failed step instead of a silently "applied"
        one."""
        blob = f"{record.stdout}\n{record.stderr}".strip()
        low = blob.lower()
        if returncode != 0 or any(m in low for m in _REFUSAL_MARKERS):
            raise StepRefused(blob or f"exit code {returncode}", record=record)

    def _apply_appx(self, step: Dict, rp_id: str) -> StepRecord:
        pkg = step["package"]
        self._backup.backup_appx_package(pkg, rp_id)
        proc = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             f"Get-AppxPackage '{ps_quote(pkg)}' | Remove-AppxPackage"],
            check=False, capture_output=True, text=True,
            creationflags=subprocess.CREATE_NO_WINDOW,
            timeout=120,
        )
        self._os.invalidate_appx_cache()
        # `Get-AppxPackage X | Remove-AppxPackage` exits 0 while removing
        # nothing (CLAUDE.md, Apps tab), so only a read-back decides -- of the
        # CURRENT user's registrations, the scope the removal acted on (the
        # shared cached list is -AllUsers when elevated).
        still = self._appx_count_for_current_user(pkg)
        if still != 0:
            detail = ((proc.stdout or "") + (proc.stderr or "")).strip()[-300:]
            raise RuntimeError(
                (f"'{pkg}' is still installed" if still else
                 f"could not confirm '{pkg}' was removed (the package list could not be read)")
                + (f": {detail}" if detail else ""))
        return StepRecord("appx", pkg, pkg, None)

    @staticmethod
    def _appx_count_for_current_user(pkg: str) -> Optional[int]:
        """How many of this user's packages match `pkg`; None if unreadable."""
        try:
            proc = subprocess.run(
                ["powershell", "-NoProfile", "-Command",
                 f"@(Get-AppxPackage '{ps_quote(pkg)}' -ErrorAction Stop).Count"],
                check=False, capture_output=True, text=True,
                creationflags=subprocess.CREATE_NO_WINDOW, timeout=60)
        except (OSError, subprocess.TimeoutExpired) as e:
            logger.warning("could not re-read appx package %s: %s", pkg, e)
            return None
        text = (proc.stdout or "").strip()
        if proc.returncode != 0 or not text.isdigit():
            logger.warning("could not re-read appx package %s (rc=%s): %s",
                           pkg, proc.returncode, (proc.stderr or text)[-200:])
            return None
        return int(text)

    def _apply_scheduled_task(self, step: Dict, rp_id: str) -> StepRecord:
        """Disable a scheduled task. Records the current state for revert."""
        task_name = step["task_name"]
        before = "Unknown"
        try:
            result = subprocess.run(
                ["schtasks", "/query", "/tn", task_name, "/fo", "LIST"],
                capture_output=True, text=True,
                creationflags=subprocess.CREATE_NO_WINDOW,
                timeout=30,
            )
            for line in result.stdout.splitlines():
                if line.startswith("Status:"):
                    before = line.split(":", 1)[1].strip()
                    break
        except Exception as e:
            logger.debug("Could not query scheduled task status for %s: %s", task_name, e)
        proc = subprocess.run(
            ["schtasks", "/change", "/tn", task_name, "/disable"],
            check=False, capture_output=True, text=True,
            creationflags=subprocess.CREATE_NO_WINDOW,
            timeout=30,
        )
        if proc.returncode != 0:
            # Protected tasks (UpdateOrchestrator and co.) refuse even an
            # elevated admin; nothing changed, so nothing is recorded.
            raise RuntimeError(((proc.stdout or "") + (proc.stderr or "")).strip()
                               or f"schtasks exited with code {proc.returncode}")
        return StepRecord("scheduled_task", task_name, before, "Disabled")

    # ==================================================================
    # Detect
    # ==================================================================

    def detect_status(self, tweak: Dict) -> str:
        """Back-compatible string form: 'applied' | 'not_applied' | 'partial'
        | 'not_applicable' | 'unknown'."""
        return self.detect(tweak).status

    def detect_many(
        self,
        tweaks: List[Dict],
        on_result: Callable[[Dict, DetectionResult], None],
        is_cancelled: Optional[Callable[[], bool]] = None,
        workers: int = 8,
    ) -> None:
        """Detect a whole list, running the probes concurrently.

        Sequentially this is dominated by process launches — schtasks, powercfg
        and PowerShell probes cost a fifth of a second each no matter how
        trivial the question, and a few hundred of those is most of a minute.
        They spend that time blocked in `subprocess`, which releases the GIL,
        so a small thread pool collapses the wall time without touching the
        registry probes' cost at all.

        `on_result` is called from worker threads as each answer lands — it
        must be thread-safe. The Qt caller emits a signal, which is.
        """
        from concurrent.futures import ThreadPoolExecutor

        def _one(tweak: Dict) -> None:
            if is_cancelled is not None and is_cancelled():
                return
            try:
                on_result(tweak, self.detect(tweak))
            except Exception as e:  # one bad definition must not stop the sweep
                logger.warning("detect failed for %s: %s", tweak.get("id"), e)
                on_result(tweak, DetectionResult(UNKNOWN, f"check failed: {e}"))

        with ThreadPoolExecutor(max_workers=workers,
                                thread_name_prefix="tweak-detect") as pool:
            list(pool.map(_one, tweaks))

    def detect(self, tweak: Dict) -> DetectionResult:
        """Full verdict for a tweak, with a reason the UI can put in a tooltip.

        Order of business:
          1. Does it apply to this OS at all? (`applies_to`, then whether the
             targeted service/task/package exists) -> `not_applicable`.
          2. An explicit `detect` block on the tweak wins over the steps —
             that is how command/script tweaks become checkable.
          3. Otherwise every step is checked and the results aggregated.
        """
        verdict = self._os.evaluate(tweak.get("applies_to"))
        if not verdict.applicable:
            return DetectionResult(NOT_APPLICABLE, verdict.reason)

        probes = tweak.get("detect")
        if probes is not None:
            if isinstance(probes, dict):
                probes = [probes]
            results = [self._detect_probe(p) for p in probes]
        else:
            steps = tweak.get("steps", [])
            if not steps:
                return DetectionResult(
                    UNKNOWN, "this tweak defines no steps to check")
            results = [self._detect_probe(s) for s in steps]

        return self._aggregate(results)

    # -- aggregation -------------------------------------------------------

    @staticmethod
    def _aggregate(results: List[StepStatus]) -> DetectionResult:
        if not results:
            return DetectionResult(UNKNOWN, "nothing to check")

        live = [r for r in results if r.status != NOT_APPLICABLE]
        if not live:
            reasons = list(dict.fromkeys(r.reason for r in results if r.reason))
            return DetectionResult(NOT_APPLICABLE, "; ".join(reasons), results)

        applied = [r for r in live if r.status == APPLIED]
        not_applied = [r for r in live if r.status == NOT_APPLIED]
        unknown = [r for r in live if r.status == UNKNOWN]

        def _why(bucket: List[StepStatus]) -> str:
            return "; ".join(dict.fromkeys(r.reason for r in bucket if r.reason))

        if len(applied) == len(live):
            return DetectionResult(APPLIED, _why(applied), results)
        if len(unknown) == len(live):
            return DetectionResult(UNKNOWN, _why(unknown), results)
        if len(not_applied) == len(live):
            return DetectionResult(NOT_APPLIED, _why(not_applied), results)

        if applied:
            reason = (f"{len(applied)} of {len(live)} steps are in place"
                      + (f"; {len(unknown)} could not be checked" if unknown else ""))
            return DetectionResult(PARTIAL, reason, results)

        # Nothing is applied; some steps just weren't checkable.
        reason = _why(not_applied) or "not in place"
        if unknown:
            reason += f" ({len(unknown)} step(s) could not be checked)"
        return DetectionResult(NOT_APPLIED, reason, results)

    # -- individual probes -------------------------------------------------

    def _detect_probe(self, step: Dict) -> StepStatus:
        stype = step.get("type", "")
        try:
            if stype == "registry":
                return self._detect_registry(step)
            if stype == "registry_delete":
                return self._detect_registry_delete(step)
            if stype in ("registry_key_exists", "registry_key_absent"):
                return self._detect_registry_key(step)
            if stype == "service":
                return self._detect_service(step)
            if stype == "appx":
                return self._detect_appx(step)
            if stype == "scheduled_task":
                return self._detect_scheduled_task(step)
            if stype in ("file_exists", "file_absent"):
                return self._detect_file(step)
            if stype == "powershell":
                return self._detect_powershell(step)
            if stype == "none":
                return StepStatus(UNKNOWN, step.get(
                    "reason", "this tweak has no reliable way to be checked"))
            if stype in ("command", "script"):
                nested = step.get("detect")
                if isinstance(nested, dict):
                    return self._detect_probe(nested)
                cmd = str(step.get("cmd", step.get("command", "")))[:60]
                return StepStatus(
                    UNKNOWN,
                    "runs a command, and reading its state would mean running "
                    f"something ({cmd}...) — apply it to be sure",
                    target=cmd)
        except PermissionError as e:
            return StepStatus(UNKNOWN, f"access denied while checking: {e}")
        except Exception as e:
            logger.debug("detect probe failed (%s): %s", stype, e)
            return StepStatus(UNKNOWN, f"check failed: {e}")
        return StepStatus(UNKNOWN, f"no checker for step type '{stype}'")

    def _detect_registry(self, step: Dict) -> StepStatus:
        full_key = step["key"]
        value_name = step.get("value", "")
        target = _short_key(full_key, value_name)
        hive, sub = _parse_key(full_key)
        # "absent_means" lets a definition say that a *missing* value is the
        # applied state — true whenever Windows' own default is the thing the
        # tweak wants and the tweak only writes it to be explicit.
        absent_status = APPLIED if step.get("absent_means") == "applied" else NOT_APPLIED

        try:
            key = winreg.OpenKey(hive, sub)
        except OSError as e:
            if getattr(e, "winerror", None) == _ERROR_ACCESS_DENIED:
                return StepStatus(
                    UNKNOWN, f"no permission to read {target} "
                             "(run the app as Administrator to check this one)",
                    target)
            if _is_key_existence_step(step):
                return StepStatus(NOT_APPLIED, f"the key {target} does not exist", target)
            return StepStatus(
                absent_status,
                f"{target} does not exist, so Windows is using its default",
                target)

        with key:
            if _is_key_existence_step(step):
                return StepStatus(APPLIED, f"the key {target} exists", target)
            try:
                actual, actual_kind = winreg.QueryValueEx(key, value_name)
            except OSError as e:
                if getattr(e, "winerror", None) == _ERROR_ACCESS_DENIED:
                    return StepStatus(UNKNOWN, f"no permission to read {target}", target)
                return StepStatus(
                    absent_status,
                    f"{target} is not set, so Windows is using its default",
                    target)

        kind = _KIND_MAP.get(step.get("kind", "DWORD"), winreg.REG_DWORD)
        expected = _normalise_expected(step.get("data"), kind, actual)
        if actual == expected:
            return StepStatus(APPLIED, f"{target} = {_fmt(actual)}", target)
        return StepStatus(
            NOT_APPLIED,
            f"{target} is {_fmt(actual)}, this tweak wants {_fmt(expected)}",
            target)

    def _detect_registry_delete(self, step: Dict) -> StepStatus:
        full_key = step["key"]
        value_name = step.get("value", "")
        target = _short_key(full_key, value_name)
        hive, sub = _parse_key(full_key)
        try:
            with winreg.OpenKey(hive, sub) as k:
                actual, _ = winreg.QueryValueEx(k, value_name)
        except OSError as e:
            if getattr(e, "winerror", None) == _ERROR_ACCESS_DENIED:
                return StepStatus(UNKNOWN, f"no permission to read {target}", target)
            return StepStatus(APPLIED, f"{target} is gone", target)
        return StepStatus(NOT_APPLIED,
                          f"{target} still exists (= {_fmt(actual)})", target)

    @staticmethod
    def _detect_registry_key(step: Dict) -> StepStatus:
        """Whole-key presence. Shell tweaks that hide a namespace folder work
        by deleting a CLSID key outright, so there is no value to compare."""
        full_key = step["key"]
        target = _short_key(full_key)
        hive, sub = _parse_key(full_key)
        wants_present = step.get("type") == "registry_key_exists"
        try:
            winreg.CloseKey(winreg.OpenKey(hive, sub))
            present = True
        except OSError as e:
            if getattr(e, "winerror", None) == _ERROR_ACCESS_DENIED:
                return StepStatus(UNKNOWN, f"no permission to read {target}", target)
            present = False
        if present == wants_present:
            return StepStatus(
                APPLIED, f"{target} {'exists' if present else 'is gone'}", target)
        return StepStatus(
            NOT_APPLIED,
            f"{target} {'still exists' if present else 'does not exist'}", target)

    def _detect_service(self, step: Dict) -> StepStatus:
        name = step["name"]
        exists = self._os.service_exists(name)
        if exists is False:
            return StepStatus(
                NOT_APPLICABLE,
                f"the '{name}' service is not installed on this Windows build",
                name)
        if exists is None:
            return StepStatus(
                UNKNOWN, f"could not query the '{name}' service", name)

        import win32service
        hscm = win32service.OpenSCManager(None, None, win32service.SC_MANAGER_CONNECT)
        try:
            hs = win32service.OpenService(hscm, name, win32service.SERVICE_QUERY_CONFIG)
            try:
                current = win32service.QueryServiceConfig(hs)[1]
            finally:
                win32service.CloseServiceHandle(hs)
        finally:
            win32service.CloseServiceHandle(hscm)

        _st = step.get("start_type", "")
        expected = int(_st) if isinstance(_st, int) else _START_TYPE_MAP.get(str(_st).lower(), -1)
        cur_label = _START_TYPE_LABEL.get(current, str(current))
        exp_label = _START_TYPE_LABEL.get(expected, str(expected))
        if current == expected:
            return StepStatus(APPLIED, f"'{name}' start type is {cur_label}", name)
        return StepStatus(
            NOT_APPLIED,
            f"'{name}' start type is {cur_label}, this tweak wants {exp_label}",
            name)

    def _detect_appx(self, step: Dict) -> StepStatus:
        pkg = step["package"]
        installed = self._os.appx_installed(pkg)
        if installed is None:
            return StepStatus(UNKNOWN, f"could not list installed apps to check '{pkg}'", pkg)
        if installed:
            return StepStatus(NOT_APPLIED, f"'{pkg}' is still installed", pkg)
        return StepStatus(APPLIED, f"'{pkg}' is not installed", pkg)

    def _detect_scheduled_task(self, step: Dict) -> StepStatus:
        task_name = step["task_name"]
        info = self._os.scheduled_task_query(task_name)
        if info.exists is False:
            return StepStatus(
                NOT_APPLICABLE,
                f"the scheduled task '{task_name}' does not exist on this build",
                task_name)
        if info.exists is None:
            return StepStatus(UNKNOWN, f"could not query task '{task_name}'", task_name)
        if not info.status:
            return StepStatus(
                UNKNOWN, f"schtasks gave no Status line for '{task_name}'", task_name)
        if info.status.lower() == "disabled":
            return StepStatus(APPLIED, f"'{task_name}' is disabled", task_name)
        return StepStatus(NOT_APPLIED, f"'{task_name}' is {info.status}", task_name)

    @staticmethod
    def _detect_file(step: Dict) -> StepStatus:
        import os as _os_mod
        path = _os_mod.path.expandvars(step.get("path", ""))
        present = _os_mod.path.exists(path)
        wants_present = step.get("type") == "file_exists"
        if present == wants_present:
            return StepStatus(APPLIED,
                              f"{path} {'exists' if present else 'is gone'}", path)
        return StepStatus(NOT_APPLIED,
                          f"{path} {'exists' if present else 'does not exist'}", path)

    @staticmethod
    def _detect_powershell(step: Dict) -> StepStatus:
        """A read-only PowerShell probe. `script` must not change anything —
        it is run on every status sweep."""
        script = step.get("script", "")
        expected = str(step.get("applied_when", "True")).strip().lower()
        proc = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
            capture_output=True, text=True, timeout=30,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        if proc.returncode != 0:
            return StepStatus(UNKNOWN, f"probe failed: {(proc.stderr or '').strip()[:120]}")
        out = (proc.stdout or "").strip()
        if out.lower() == expected:
            return StepStatus(APPLIED, step.get("applied_reason", f"probe returned {out}"))
        return StepStatus(NOT_APPLIED,
                          step.get("not_applied_reason", f"probe returned {out or '(nothing)'}"))

    # ==================================================================
    # Metadata helpers
    # ==================================================================

    def requires_restart(self, tweak: Dict) -> bool:
        """Does this need a reboot (or at least an Explorer restart) to show?"""
        if "requires_restart" in tweak:
            return bool(tweak["requires_restart"])
        for step in tweak.get("steps", []):
            if step.get("type") == "service":
                return True
            key = str(step.get("key", "")).upper()
            if key.startswith("HKLM\\SYSTEM\\CURRENTCONTROLSET\\SERVICES"):
                return True
        return False

    @staticmethod
    def load_definitions(json_path: str) -> List[Dict]:
        """Load a list of tweak definitions from a JSON file."""
        with open(json_path, encoding="utf-8") as f:
            return json.load(f)


def _fmt(value: Any) -> str:
    """Registry data, short enough for a tooltip."""
    if isinstance(value, bytes):
        text = value.hex(" ")
        return text if len(text) <= 40 else text[:37] + "..."
    text = str(value)
    return text if len(text) <= 60 else text[:57] + "..."
