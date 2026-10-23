"""Explanation coverage for the F1 modules; construction never activates hardware."""
import importlib
import json
from pathlib import Path
import subprocess
import sys

import pytest


@pytest.mark.parametrize('name', ['Cleanup', 'Updates', 'Quick Fix', 'Thermal Control'])
def test_f1_module_explanations_have_no_gaps(tmp_path, name):
    # Keep construction/teardown of real app services out of other Qt tests.
    report = tmp_path / 'audit.json'
    source = str(Path(__file__).resolve().parents[1] / 'src')
    modules = {
        'Cleanup': ('modules.cleanup.cleanup_module', 'CleanupModule'),
        'Updates': ('modules.updates.updates_module', 'UpdatesModule'),
        'Quick Fix': ('modules.quick_fix.quick_fix_module', 'QuickFixModule'),
        'Thermal Control': ('modules.thermal_control.thermal_module', 'ThermalControlModule'),
    }
    module, cls = modules[name]
    # Audit only this owned module, independent of other modules' import state.
    code = (f"import sys,json,types; sys.path.insert(0, {source!r}); "
            f"from {module} import {cls}; "
            "registry=types.ModuleType('main'); "
            f"registry.register_all_modules=lambda stub: stub.module_registry.register({cls}()); "
            "sys.modules['main']=registry; "
            "from pathlib import Path; from ui.tooltip_audit import audit_modules; "
            f"Path({str(report)!r}).write_text(json.dumps(audit_modules({str(tmp_path)!r}, {name!r})))")
    result = subprocess.run([sys.executable, '-c', code], capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
    rows = json.loads(report.read_text())
    assert rows
    assert all(not row['error'] and row['total'] == 0 for row in rows), rows


@pytest.mark.parametrize('area', ['cleanup', 'updates', 'quick_fix', 'thermal_control'])
def test_curated_f1_help_has_no_empty_entries(area):
    help_text = importlib.import_module(f'modules.{area}.help_text')
    dictionaries = [value for key, value in vars(help_text).items()
                    if key.isupper() and isinstance(value, dict)]
    assert dictionaries
    for dictionary in dictionaries:
        assert dictionary
        assert all(str(text).strip() for text in dictionary.values())


def test_repair_help_covers_every_action():
    from modules.quick_fix.fix_actions import ALL_ACTIONS
    from modules.quick_fix.help_text import ACTIONS
    assert {action.key for action in ALL_ACTIONS} <= ACTIONS.keys()
    assert 'does not delete or rebuild' in ACTIONS['reset_search']
    assert 'does not delete font files' in ACTIONS['clear_font_cache']


def test_sensitive_help_preserves_measured_distinctions():
    from modules.cleanup.help_text import CONTROLS as cleanup
    from modules.updates.help_text import CONTROLS as updates
    from modules.thermal_control.help_text import CONTROLS as thermal
    assert 'zero freed bytes' in cleanup['Move to Recycle Bin']
    assert 'never installed' in updates['Install Selected']
    assert '20%' in thermal['Apply'] and '70%' in thermal['Apply']
    assert 'Immediately' in thermal['Zero RPM: let the fan stop when the GPU is cool']
