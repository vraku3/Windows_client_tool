"""One entry per security control: what it reads, what it writes, what it costs
to get wrong."""
import logging
from dataclasses import dataclass
from enum import Enum
from typing import Any, Callable, Dict, Optional, Tuple

logger = logging.getLogger(__name__)


class Category(Enum):
    DEFENDER = "Defender"
    FIREWALL_NETWORK = "Firewall & Network"
    ACCOUNTS = "Accounts & Credentials"
    DEVICE_BOOT = "Device & Boot"
    SERVICES = "Services"
    FEATURES = "Windows Features"
    EXPLOIT_CVE = "Exploit & CVE"
    AUDIT_LOGGING = "Audit & Logging"


class Risk(Enum):
    LOW = "low"          # reversible, no reboot, nothing depends on it
    MEDIUM = "medium"    # may break a workflow; confirm before applying
    HIGH = "high"        # boot, disk encryption, credential handling, VBS.
                         # Forces a Windows restore point on the batch.


class ControlState(Enum):
    APPLIED_VERIFIED = "applied_verified"
    APPLIED_PENDING_REBOOT = "applied_pending_reboot"
    APPLIED_UNVERIFIED = "applied_unverified"
    REFUSED = "refused"


@dataclass(frozen=True)
class SecurityControl:
    id: str
    title: str
    category: Category
    description: str
    why_it_matters: str
    reader: Callable[[], Dict[str, Any]]
    on_steps: Tuple[Dict, ...] = ()
    off_steps: Tuple[Dict, ...] = ()
    desired: Optional[Any] = None
    risk: Risk = Risk.LOW
    requires_admin: bool = True
    requires_reboot: bool = False
    read_only_reason: Optional[str] = None
    docs_url: Optional[str] = None
    #: Pull the comparable value out of the reader's dict. Defaults to the
    #: "enabled" key; multi-valued controls (NTLM level, cached logon count,
    #: cloud block level) supply their own.
    read_value: Optional[Callable[[Dict[str, Any]], Any]] = None

    def __post_init__(self):
        has_reason = bool(self.read_only_reason and self.read_only_reason.strip())
        if not self.on_steps and not self.off_steps and not has_reason:
            raise ValueError(
                f"control {self.id!r} has no on_steps/off_steps and no "
                "read_only_reason: a control we cannot write must say why")

    @property
    def writable(self) -> bool:
        return bool(self.on_steps or self.off_steps)

    def read(self) -> Optional[Any]:
        """Current value, or None if the machine could not be asked.

        None means "we could not look" and is never collapsed into False.
        """
        try:
            result = self.reader() or {}
        except Exception:
            logger.warning(
                "control %r: reader raised, treating as unavailable",
                self.id, exc_info=True)
            return None
        if result.get("available") is False:
            return None
        if self.read_value is not None:
            try:
                return self.read_value(result)
            except Exception:
                logger.warning(
                    "control %r: read_value could not extract a value from "
                    "%r", self.id, result, exc_info=True)
                return None
        return result.get("enabled")

    def steps_for(self, desired_value: Any) -> Tuple[Dict, ...]:
        if desired_value is None:
            raise ValueError(
                f"control {self.id!r} has no desired value: cannot ask a "
                "control with no opinion for its steps")
        return self.on_steps if desired_value else self.off_steps
