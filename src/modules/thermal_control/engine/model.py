"""What every thermal backend hands back. No Qt."""
from dataclasses import dataclass
from typing import Optional

TEMPERATURE = "temperature"
FAN = "fan"            # RPM
CONTROL = "control"    # duty, percent


@dataclass(frozen=True)
class Sensor:
    id: str                     # stable across runs: the backend's own identifier
    hardware: str               # "Nuvoton NCT6799D", "AMD Ryzen 9 9950X3D", ...
    name: str                   # "CPU Fan #1", "Core (Tctl/Tdie)", ...
    kind: str                   # TEMPERATURE / FAN / CONTROL
    value: Optional[float]      # None = could not be read, never 0
    source: str                 # "lhm" / "d3dkmt"
    controllable: bool = False

    @property
    def unit(self) -> str:
        return {TEMPERATURE: "°C", FAN: "RPM", CONTROL: "%"}.get(self.kind, "")

    def display(self) -> str:
        if self.value is None:
            return "n/a"
        if self.kind == FAN:
            return f"{self.value:,.0f} RPM"
        return f"{self.value:.1f} {self.unit}"


def is_pump(name: str) -> bool:
    """A pump header. An AIO or loop pump must not be slowed like a fan: many
    stall or overheat their own motor below ~70%. Matched by the header name
    the board reports ("AIO Pump", "Water Pump" on the X870E Taichi)."""
    return "pump" in (name or "").lower()
