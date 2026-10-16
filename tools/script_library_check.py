"""Run every Script Library script against THIS machine and report problems.

    .venv\\Scripts\\python.exe tools\\script_library_check.py [out.txt] [--only id,id] [--parse-only]

Every script is first parsed by PowerShell's own parser (syntax errors fail
here even for server scripts that can never run on this PC). Then each one
runs, and a run FAILS when it exits non-zero, prints an `ERROR:` line, leaks
CLIXML, prints nothing, or times out. A script with `requires` set that
prints NOT AVAILABLE is "not applicable here", not a failure.

Run it elevated and again unelevated (runas /trustlevel:0x20000). Exits 1 on
any failure. Read-only: every built-in is read-only by contract.
"""
import base64
import ctypes
import os
import subprocess
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from modules.scripts_hub import library as lib  # noqa: E402

#: Run from a file, reading the code from JSON: a PowerShell COMMAND LINE that
#: decodes Base64 is what the antivirus here (Bitdefender) blocks outright --
#: CreateProcess failed with "Access is denied".
_PARSE = r"""param($json)
$scripts = Get-Content -Raw -Encoding UTF8 $json | ConvertFrom-Json
foreach ($s in $scripts) {
  $errs = $null; $null = [System.Management.Automation.Language.Parser]::ParseInput($s.code, [ref]$null, [ref]$errs)
  foreach ($e in $errs) { "$($s.id) :: line $($e.Extent.StartLineNumber): $($e.Message)" }
}
"""


def parse_errors(scripts):
    import json
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        data = os.path.join(tmp, "scripts.json")
        runner = os.path.join(tmp, "parse.ps1")
        with open(data, "w", encoding="utf-8") as f:
            json.dump([{"id": s.id, "code": s.body} for s in scripts if s.kind == lib.POWERSHELL], f)
        with open(runner, "w", encoding="utf-8-sig") as f:
            f.write(_PARSE)
        p = subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy",
                            "Bypass", "-File", runner, data],
                           capture_output=True, text=True, encoding="utf-8", timeout=300)
    if p.returncode:
        return [f"parser could not run: {p.stderr.strip()[:300]}"]
    return [ln for ln in p.stdout.splitlines() if ln.strip()]


def judge(script, result):
    text = "\n".join(result.lines)
    if result.timed_out:
        return "TIMEOUT"
    if script.requires and "NOT AVAILABLE" in text:
        return "N/A"
    if "#< CLIXML" in text or "<Objs Version" in text:
        return "CLIXML"
    if any(ln.startswith("ERROR:") for ln in result.lines):
        return "ERROR-LINE"
    if result.returncode not in (0, None):
        return f"RC={result.returncode}"
    if not text.strip():
        return "EMPTY"
    return "OK"


def main(argv):
    out_path = next((a for a in argv if not a.startswith("--")), None)
    only = next((a.split("=", 1)[1] for a in argv if a.startswith("--only=")), "")
    scripts = [s for s in lib.built_in() if not only or s.id in only.split(",")]
    lines = [f"admin={bool(ctypes.windll.shell32.IsUserAnAdmin())}  scripts={len(scripts)}"]
    errs = parse_errors(scripts)
    lines += ["PARSE " + e for e in errs]
    failures = len(errs)
    if "--parse-only" not in argv:
        for s in scripts:
            r = lib.run(s, lambda _l: None)
            verdict = judge(s, r)
            bad = verdict not in ("OK", "N/A")
            failures += bad
            first = next((ln for ln in r.lines if ln.startswith(("ERROR:", "NOT AVAILABLE"))), "")
            lines.append(f"{'FAIL' if bad else 'ok  '} {verdict:10} {r.seconds:5.1f}s {s.id:28} {first[:150]}")
    lines.append(f"failures={failures}")
    text = "\n".join(lines)
    if out_path:
        with open(out_path, "w", encoding="utf-8") as f:
            f.write(text + "\n")
    print(text)
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
