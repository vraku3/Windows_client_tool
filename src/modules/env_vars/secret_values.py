"""Secret detection and redaction; never retain clear values in display metadata."""
import re

HIDDEN = "•••••••• (hidden)"
_NAME = re.compile(r"key|token|secret|password|passwd|pwd|credential|apikey|api_key|auth|bearer", re.I)
_PREFIX = re.compile(r"^(?:sk-|ghp_|xox|AKIA)", re.I)
_ENCODED = re.compile(r"[A-Za-z0-9+/_=-]{33,}")


def is_secret(name, value):
    value = str(value or "")
    return bool(_NAME.search(name) or _PREFIX.match(value) or _ENCODED.fullmatch(value))


def display_value(name, value):
    return HIDDEN if is_secret(name, value) else value


def redact_snapshot(snapshot):
    return {scope: {name: display_value(name, value) for name, value in rows.items()}
            for scope, rows in snapshot.items()}


def snapshot_differences(old, new):
    """Keep evidence of changed secrets without revealing either value."""
    lines = []
    for scope in ("system", "user"):
        before, after = old.get(scope, {}), new.get(scope, {})
        for name in sorted(set(before) | set(after), key=str.lower):
            if name not in before:
                lines.append(f"[{scope}] added {name} = {display_value(name, after[name])}")
            elif name not in after:
                lines.append(f"[{scope}] removed {name} (was {display_value(name, before[name])})")
            elif before[name] != after[name]:
                sensitive = is_secret(name, before[name]) or is_secret(name, after[name])
                first = HIDDEN if sensitive else before[name]
                last = HIDDEN if sensitive else after[name]
                lines.append(f"[{scope}] changed {name}: {first}  ->  {last}")
    return lines
