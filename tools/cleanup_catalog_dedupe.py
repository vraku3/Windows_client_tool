"""Disable the second of every pair of ENABLED catalog entries that point at
exactly the same paths (they double-count in the cleanup total). The first,
alphabetically, stays and is named in the second's disabled_reason."""
import json
from pathlib import Path

DEFS = Path(__file__).resolve().parent.parent / "src" / "modules" / "cleanup" / "cleanup_scanner" / "definitions"


def main() -> None:
    files = {f: json.loads(f.read_text(encoding="utf-8")) for f in sorted(DEFS.glob("*.json"))}
    specs = [(f, s) for f, d in files.items() for s in d["scanners"] if not s.get("disabled_reason")]
    keeper = {}
    disabled = 0
    for f, spec in sorted(specs, key=lambda fs: fs[1]["id"]):
        key = frozenset(p.lower() for p in spec["paths"])
        if key in keeper:
            spec["disabled_reason"] = (f"Same paths as '{keeper[key]}', which already covers them; "
                                       "kept once so the cleanup total is not counted twice.")
            disabled += 1
        else:
            keeper[key] = spec["id"]
    for f, data in files.items():
        f.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"disabled {disabled} duplicate entries")


if __name__ == "__main__":
    main()
