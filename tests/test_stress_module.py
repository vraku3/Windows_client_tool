import threading
import time

from PyQt6.QtCore import QThreadPool

from modules.stress_test.stress_module import StressModule
from modules.stress_test.engine.session import StressSession, SessionConfig
from test_stress_session import FakeSensors


def test_widget_builds_without_sensor_calls_and_shutdown_stops_run(qapp, tmp_path):
    module = StressModule()
    module.on_start(object())
    widget = module.create_widget()
    assert widget is not None
    session = StressSession(SessionConfig(threads=1), FakeSensors(), report_dir=tmp_path)
    module._session = session
    thread = threading.Thread(target=session.run)
    thread.start()
    deadline = time.monotonic() + 1
    while session.cpu is None and time.monotonic() < deadline:
        time.sleep(.005)
    module.on_deactivate()
    assert not session.stop_event.is_set()
    module.on_stop()
    thread.join(1)
    assert not thread.is_alive()
    assert not session.running
    assert session.stop_event.is_set()
    widget.deleteLater()
    qapp.processEvents()


def test_module_can_stop_before_build():
    module = StressModule()
    module.on_start(object())
    module.on_deactivate()
    module.on_stop()


def test_qt_worker_shutdown_and_chart_gaps(qapp, tmp_path):
    module = StressModule()
    module.on_start(object())
    widget = module.create_widget()
    widget.resize(1100, 1100)
    for chart in widget.charts.values():
        chart.set_series([50, None, 55, 56])
    assert not widget.grab().isNull()
    session = StressSession(SessionConfig(threads=1), FakeSensors(), report_dir=tmp_path)
    module.start_session(session)
    deadline = time.monotonic() + 1
    while session.cpu is None and time.monotonic() < deadline:
        qapp.processEvents()
        time.sleep(.005)
    assert session.cpu is not None
    module.on_stop()
    qapp.processEvents()
    assert session.done.is_set()
    assert not any(t.is_alive() for t in session.cpu.threads)
    assert not module._workers
    widget.deleteLater()
    qapp.processEvents()


def test_registration_and_every_frozen_import():
    import main
    from pyinstaller_common import HIDDEN_IMPORTS
    from pathlib import Path
    class Registry:
        modules = []
        def register(self, m):
            self.modules.append(m)
    class App:
        module_registry = Registry()
    main.register_all_modules(App())
    stress = next(m for m in App.module_registry.modules if m.name == 'Stress Test')
    assert stress.requires_admin is False
    assert stress.group == 'TOOLS'
    for path in Path('src/modules/stress_test').rglob('*.py'):
        parts = list(path.relative_to('src').with_suffix('').parts)
        if parts[-1] == '__init__':
            parts.pop()
        assert '.'.join(parts) in HIDDEN_IMPORTS
