"""Stress Test sidebar owner: navigation never stops a running session."""
import logging

from PyQt6 import sip
from core.base_module import BaseModule
from core.module_groups import ModuleGroup
from core.worker import Worker, retire
from core.long_op_pool import get_long_op_pool

from .engine.sensors import SensorAdapter
from .stress_tab import StressTab

logger = logging.getLogger(__name__)


def running_thermal_service():
    # Existing read-only accessor. Import kept above the Qt-free engine boundary.
    from modules.thermal_control.thermal_service import ThermalService
    service = ThermalService.instance
    return service if service is not None and not service._stopped else None


class StressModule(BaseModule):
    name = 'Stress Test'
    icon = '\u26a1'
    description = 'CPU and RAM stability testing with live sensors and automatic safety cutoff'
    group = ModuleGroup.TOOLS
    requires_admin = False

    def __init__(self):
        super().__init__()
        self._widget = None
        self._session = None
        self._stopped = False

    def create_widget(self):
        self._widget = StressTab(self)
        return self._widget

    def make_sensors(self):
        return SensorAdapter(running_thermal_service)

    def start_session(self, session):
        if self._stopped or self._workers or (self._session and self._session.running):
            raise RuntimeError('A stress test is already running or the application is stopping')
        if self._session and self._session._sampler and self._session._sampler.is_alive():
            raise RuntimeError('The previous sensor read is still returning; restart is unavailable until it finishes')
        self._session = session
        worker = Worker(lambda _w: session.run())
        self._workers.append(worker)
        worker.signals.finished.connect(self._worker_done)
        get_long_op_pool().start(worker)

    def _worker_done(self):
        retire(self._workers)
        if self._widget is not None and not sip.isdeleted(self._widget):
            self._widget.poll()

    def on_activate(self):
        if self._widget is not None and not sip.isdeleted(self._widget):
            self._widget.activate()

    def on_deactivate(self):
        # Owned by the module: load and safety continue across navigation.
        if self._widget is not None and not sip.isdeleted(self._widget):
            self._widget.poll()
            if self._session is None or not self._session.running:
                self._widget.pause_idle()

    def on_stop(self):
        self._stopped = True
        if self._session:
            self._session.stop()
            if self._session.running:
                self._session.done.wait()  # Sensor reads never block session shutdown.
        retire(self._workers)
        if self._widget is not None and not sip.isdeleted(self._widget):
            self._widget.shutdown()
        super().on_stop()
