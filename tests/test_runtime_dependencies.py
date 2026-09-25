"""Every hard requirement must actually be importable.

`WMI>=1.5.1` sat in requirements.txt for months while `.venv` had no `wmi`,
and nothing said so. Five modules import it inside a `try:` and quietly lose
their data — Hardware Info, Reliability, Services, System Report, and the
Security Dashboard, which went as far as reporting a red "Insecure" verdict
because it could not ask about the TPM. The only trace was one line in the
session log:

    [ERROR] modules.reliability.reliability_reader: wmi package not installed

The requirements file could not raise the alarm either, because it also
listed packages the code deliberately does not use (`pyqtgraph`, whose only
mention in the source is `HAS_PYQTGRAPH = False  # intentionally False`), so
"declared but absent" had stopped meaning anything.
"""
import os
import re

import importlib.util

import pytest

REPO_ROOT = os.path.join(os.path.dirname(__file__), "..")
REQUIREMENTS = os.path.join(REPO_ROOT, "requirements.txt")

#: Distributions whose import name differs from the name pip installs under.
IMPORT_NAME = {
    "pywintrace": "etw",
    "pywin32": "win32api",
    "WMI": "wmi",
}


def _hard_requirements():
    """The requirements listed before the first '# Optional' block."""
    names = []
    with open(REQUIREMENTS, encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if line.lower().startswith("# optional"):
                break
            if not line or line.startswith("#"):
                continue
            names.append(re.split(r"[<>=!~\[]", line)[0].strip())
    return names


def test_requirements_file_lists_some_hard_dependencies():
    """Guards the parser above: an empty list would make the next test vacuous."""
    assert len(_hard_requirements()) >= 4


@pytest.mark.parametrize("distribution", _hard_requirements())
def test_hard_requirement_is_importable(distribution):
    module = IMPORT_NAME.get(distribution, distribution)
    assert importlib.util.find_spec(module) is not None, (
        f"requirements.txt declares {distribution} but `import {module}` "
        f"fails. Either install it or stop declaring it — a hard requirement "
        f"nobody can import is how the wmi outage went unnoticed."
    )


def test_every_wmi_consumer_can_reach_the_package():
    """Names the blast radius of the outage this test exists to prevent."""
    consumers = [
        "modules.hardware_inventory.hardware_reader",
        "modules.reliability.reliability_reader",
        "modules.security_dashboard.security_reader",
        "modules.services_manager.services_module",
        "modules.system_report.report_module",
    ]
    import wmi  # noqa: F401  — the import under test

    for name in consumers:
        assert importlib.util.find_spec(name) is not None, name
