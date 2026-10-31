"""Application safety, separate from detecting the current Windows setting.

Service functionality is protected on every PC. Machine-scoped recovery
records prevent an incident change being reapplied by ID or by an equivalent
structured step. A grouped recovery is evidence for quarantine, not proof
that each quarantined change individually caused the incident.
"""

import json
import logging
from pathlib import Path
import platform

logger = logging.getLogger(__name__)

_PROTECTED_SERVICES = {
    "licensemanager": "Windows app licensing is functionality, not telemetry.",
    "pimindexmaintenancesvc": "Contacts indexing is functionality, not a telemetry-only service.",
    "amd3dvcachesvc": "The AMD 3D V-Cache optimizer is needed for supported Ryzen X3D scheduling.",
    **dict.fromkeys(
        (
            "rpcss",
            "rpceptmapper",
            "dcomlaunch",
            "bfe",
            "mpssvc",
            "windefend",
            "lsm",
            "power",
            "schedule",
            "gpsvc",
            "audiosrv",
            "audioendpointbuilder",
            "themes",
            "dnscache",
            "plugplay",
            "eventlog",
            "profsvc",
            "samss",
        ),
        "This is a core Windows service.",
    ),
}
_HIVES = {"hkey_local_machine": "hklm", "hkey_current_user": "hkcu"}


def _key(value):
    parts = str(value).replace("/", "\\").strip("\\").casefold().split("\\")
    parts[0] = _HIVES.get(parts[0], parts[0])
    return "\\".join(parts)


def _start_type(value):
    normalized = str(value).casefold()
    return {"disabled": 4, "manual": 3, "automatic": 2, "4": 4, "3": 3, "2": 2}.get(
        normalized, normalized
    )


def _signature(step):
    kind = step.get("type")
    if kind == "registry":
        return (
            kind,
            _key(step.get("key", "")),
            str(step.get("value", "")).casefold(),
            str(step.get("kind", "DWORD")).casefold(),
            json.dumps(step.get("data"), sort_keys=True),
        )
    if kind == "service":
        return kind, str(step.get("name", "")).casefold(), _start_type(step.get("start_type"))
    return None


def _protected_service_reason(step):
    name = ""
    if step.get("type") == "service" and _start_type(step.get("start_type")) == 4:
        name = str(step.get("name", "")).casefold()
    elif step.get("type") in ("registry", "registry_delete"):
        parts = _key(step.get("key", "")).split("\\")
        if (
            len(parts) == 5
            and parts[:2] == ["hklm", "system"]
            and (parts[2] == "currentcontrolset" or parts[2].startswith("controlset"))
            and parts[3] == "services"
        ):
            value = str(step.get("value", "")).casefold()
            if (
                step.get("type") == "registry_delete"
                and value in ("", "start")
                or value == "start"
                and step.get("data") == 4
            ):
                name = parts[4]
    for protected, reason in _PROTECTED_SERVICES.items():
        if name == protected or name.startswith(protected + "_"):
            return f"Protected service {name}: {reason}"
    return ""


class SafetyPolicy:
    def __init__(self, blocks=(), error=""):
        self.blocks = tuple(blocks)
        self.error = error

    @classmethod
    def from_file(cls, path, machine_name=None):
        path = Path(path)
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            if (
                not isinstance(raw, dict)
                or raw.get("schema_version") != 1
                or not isinstance(raw.get("machine"), str)
                or not isinstance(raw.get("blocks"), list)
            ):
                raise ValueError("unsupported safety policy format")
            if raw["machine"].casefold() != (machine_name or platform.node()).casefold():
                return cls()
            blocks = []
            for block in raw["blocks"]:
                if (
                    not isinstance(block, dict)
                    or not isinstance(block.get("tweak_id"), str)
                    or not block["tweak_id"]
                    or not isinstance(block.get("reason"), str)
                    or not block["reason"]
                    or not isinstance(block.get("steps"), list)
                    or any(not isinstance(s, dict) or _signature(s) is None for s in block["steps"])
                ):
                    raise ValueError("invalid safety block")
                blocks.append(block)
            return cls(blocks)
        except FileNotFoundError:
            return cls()
        except (OSError, ValueError, TypeError) as error:
            logger.error("Cannot load tweak safety policy %s: %s", path, error)
            return cls(
                error=f"Safety policy could not be read: {error}. Repair it before applying tweaks."
            )

    def reason(self, tweak):
        if self.error:
            return self.error
        for step in tweak.get("steps", []):
            reason = _protected_service_reason(step)
            if reason:
                return reason
        signatures = {_signature(step) for step in tweak.get("steps", [])} - {None}
        for block in self.blocks:
            if block["tweak_id"] == tweak.get("id") or signatures.intersection(
                _signature(s) for s in block["steps"]
            ):
                return block["reason"]
        return ""
