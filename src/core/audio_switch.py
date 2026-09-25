"""Switch the default sound output by number: Ctrl+1 is the first sound card,
Ctrl+2 the second, ... Ctrl+9 the ninth and Ctrl+0 the tenth. No Qt.

"First" needs a stable order, and Windows has none worth trusting (the
registry lists endpoints by GUID). So the order is the user's: whatever they
arranged in the Audio outputs dialog (saved as `audio.output_order`, a list of
endpoint GUIDs), then any device not yet placed, alphabetically by name. A
device that is unplugged or disabled has no number -- the numbers close up, so
"Ctrl+2" is always the second device that is actually there.

A switch is only reported as done when the default is READ BACK and is the
device that was asked for: `SetDefaultEndpoint` returning S_OK is not proof.
"""
import logging
from dataclasses import dataclass
from typing import List, Optional, Sequence

from modules.monitor_control import display_audio as da

logger = logging.getLogger(__name__)

MAX_OUTPUTS = 10           # Ctrl+1 .. Ctrl+9, Ctrl+0


@dataclass(frozen=True)
class Output:
    number: int                 # 1..10, the key that selects it (10 is Ctrl+0)
    endpoint_id: str            # bare GUID, as the registry keys it
    label: str
    is_default: bool

    @property
    def key_name(self) -> str:
        return "Ctrl+0" if self.number == MAX_OUTPUTS else f"Ctrl+{self.number}"


def number_for_key(digit: int) -> int:
    """The output number a digit key selects: 1..9 -> 1..9, 0 -> 10."""
    return MAX_OUTPUTS if digit == 0 else digit


def usable(endpoints: Sequence[da.AudioEndpoint]) -> List[da.AudioEndpoint]:
    """Active and not hidden from the Sound settings list."""
    return [e for e in endpoints
            if e.is_active and da.is_hidden(e.raw_state) is not True]


def ordered(endpoints: Sequence[da.AudioEndpoint],
            saved_order: Optional[Sequence[str]] = None) -> List[da.AudioEndpoint]:
    """Saved order first, then everything not yet placed, by name."""
    pool = list(usable(endpoints))
    by_guid = {da.endpoint_guid(e.endpoint_id).lower(): e for e in pool}
    out: List[da.AudioEndpoint] = []
    for guid in saved_order or ():
        found = by_guid.pop(da.endpoint_guid(guid).lower(), None)
        if found is not None:
            out.append(found)
    rest = sorted(by_guid.values(), key=lambda e: (e.label or "").lower())
    return out + rest


def build_outputs(endpoints: Sequence[da.AudioEndpoint], default_id: Optional[str],
                  saved_order: Optional[Sequence[str]] = None) -> List[Output]:
    default_guid = da.endpoint_guid(default_id).lower() if default_id else None
    return [Output(number=i, endpoint_id=e.endpoint_id, label=e.label,
                   is_default=da.endpoint_guid(e.endpoint_id).lower() == default_guid)
            for i, e in enumerate(ordered(endpoints, saved_order)[:MAX_OUTPUTS], 1)]


def list_outputs(saved_order: Optional[Sequence[str]] = None) -> Optional[List[Output]]:
    """The numbered outputs, or None if Windows would not list them."""
    try:
        endpoints = da.list_render_endpoints()
    except da.AudioEndpointError as e:
        logger.warning("cannot list audio outputs: %s", e)
        return None
    default = da.default_render_endpoint_detail()
    return build_outputs(endpoints, default.endpoint_id, saved_order)


@dataclass(frozen=True)
class SwitchResult:
    ok: bool
    message: str
    output: Optional[Output] = None


def switch_to_number(number: int, saved_order: Optional[Sequence[str]] = None) -> SwitchResult:
    """Make output `number` the default. Says why when it cannot."""
    outputs = list_outputs(saved_order)
    if outputs is None:
        return SwitchResult(False, "Could not read the list of sound outputs.")
    if not outputs:
        return SwitchResult(False, "No active sound output devices were found.")
    target = next((o for o in outputs if o.number == number), None)
    if target is None:
        return SwitchResult(False, f"There is no sound output number {number} "
                                   f"(only {len(outputs)} active).")
    if target.is_default:
        return SwitchResult(True, f"{target.label} is already the default output.", target)
    try:
        da.set_default_endpoint(target.endpoint_id, user_requested=True)
    except (da.AudioPolicyError, da.AudioEndpointError) as e:
        logger.warning("switching to %s failed: %s", target.label, e)
        return SwitchResult(False, f"Could not switch to {target.label}: {e}", target)
    after = da.default_render_endpoint_detail()
    if after.endpoint_id and da.endpoint_guid(after.endpoint_id).lower() == \
            da.endpoint_guid(target.endpoint_id).lower():
        return SwitchResult(True, f"Sound output: {target.label}", target)
    return SwitchResult(False, f"Windows accepted the request but {target.label} is not the "
                               "default output (read back).", target)
