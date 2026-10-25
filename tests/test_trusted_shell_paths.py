"""Shell executables must never be selected by PATH, cwd, or ComSpec."""
import ast
from pathlib import Path
import re

import pytest

from core import system_paths


BARE = {"powershell", "powershell.exe", "pwsh", "pwsh.exe", "cmd", "cmd.exe"}
EXCLUDED = {"file_forensics", "nav_pane", "stress_test"}


def bare_shell_launches(source):
    tree = ast.parse(source)
    parents = {child: parent for parent in ast.walk(tree) for child in ast.iter_child_nodes(parent)}
    failures = set()
    for node in ast.walk(tree):
        # Includes argv variables subsequently handed to subprocess or a runner.
        if isinstance(node, (ast.List, ast.Tuple)) and node.elts:
            parent = parents.get(node)
            # Destructured metadata such as POWERSHELL, CMD = "powershell", "cmd"
            # is not an executable argv sequence.
            if isinstance(node, ast.Tuple) and isinstance(parent, ast.Assign) and any(
                isinstance(target, ast.Tuple) for target in parent.targets
            ):
                continue
            first = node.elts[0]
            if isinstance(first, ast.Constant) and first.value in BARE:
                failures.add(first.lineno)
        if isinstance(node, ast.Call):
            name = getattr(node.func, "attr", getattr(node.func, "id", ""))
            args = node.args[2:3] if name == "ShellExecuteW" else node.args[:1]
            if name in {"run", "Popen", "call", "check_call", "check_output", "ShellExecuteW", "system"}:
                for arg in args:
                    if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                        if arg.value.split(maxsplit=1)[0].lower() in BARE:
                            failures.add(arg.lineno)
            if any(k.arg == "shell" and isinstance(k.value, ast.Constant)
                   and k.value.value is True for k in node.keywords):
                if not any(k.arg == "executable" for k in node.keywords):
                    failures.add(node.lineno)
        # Covers command prefixes used by catalog/revert and RunOnce writers.
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            if re.match(r"^(?:powershell(?:\.exe)?|pwsh(?:\.exe)?|cmd(?:\.exe)?)\s+[-/]", node.value):
                failures.add(node.lineno)
    return sorted(failures)


def test_src_has_no_bare_shell_launches():
    failures = []
    for path in (Path(__file__).resolve().parents[1] / "src").rglob("*.py"):
        if EXCLUDED.intersection(path.parts):
            continue
        failures.extend(f"{path}:{line}" for line in bare_shell_launches(path.read_text(encoding="utf-8-sig")))
    assert not failures, "Untrusted shell executable:\n" + "\n".join(failures)


@pytest.mark.parametrize("source", [
    'subprocess.run(["powershell", "-Command", script])',
    'argv = ["pwsh", "-File", path]; subprocess.Popen(argv)',
    'subprocess.call("cmd.exe /c echo hello")',
    'ShellExecuteW(None, "runas", "powershell.exe", params, None, 1)',
    'prefix = "powershell -NoProfile -Command "',
    'subprocess.run(command, shell=True)',
])
def test_guard_detects_launch_forms(source):
    assert bare_shell_launches(source)


def test_guard_allows_destructured_script_kind_labels():
    assert not bare_shell_launches('POWERSHELL, CMD = "powershell", "cmd"')


@pytest.mark.parametrize("helper,suffix", [
    (system_paths.powershell_exe, r"WindowsPowerShell\v1.0\powershell.exe"),
    (system_paths.cmd_exe, "cmd.exe"),
])
def test_helpers_ignore_environment(monkeypatch, helper, suffix):
    monkeypatch.setenv("SystemRoot", r"C:\planted")
    monkeypatch.setenv("PATH", r"C:\planted")
    monkeypatch.setenv("ComSpec", r"C:\planted\cmd.exe")

    def system_directory(buf, size):
        buf.value = r"C:\trusted\System32"
        return len(buf.value)

    monkeypatch.setattr(system_paths.ctypes.windll.kernel32, "GetSystemDirectoryW", system_directory)
    monkeypatch.setattr(system_paths.os.path, "isfile", lambda path: True)
    assert helper() == str(Path(r"C:\trusted\System32") / suffix)


@pytest.mark.parametrize("helper", [system_paths.powershell_exe, system_paths.cmd_exe])
def test_helpers_refuse_an_unreadable_system_directory(monkeypatch, helper):
    monkeypatch.setattr(system_paths.ctypes.windll.kernel32, "GetSystemDirectoryW", lambda *a: 0)
    with pytest.raises(OSError, match="could not be read"):
        helper()
