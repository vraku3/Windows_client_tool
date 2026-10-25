"""Time actual first displayed row and complete two-phase searches, offscreen."""
import os
os.environ['QT_QPA_PLATFORM'] = 'offscreen'
import sys,time,json,tempfile
from pathlib import Path
from types import SimpleNamespace
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from PyQt6.QtCore import QThreadPool
from PyQt6.QtWidgets import QApplication
from modules.file_forensics.file_forensics_module import FileForensicsModule
from modules.file_forensics import history_log
app=QApplication([])
from PyQt6.QtGui import QFontDatabase
QFontDatabase.addApplicationFont('C:/Windows/Fonts/segoeui.ttf')
with tempfile.TemporaryDirectory(prefix='wct-forensics-bench-') as scratch:
    history_log._history_path=lambda: str(Path(scratch)/'history.json')
    for folder,recursive in [(os.environ['TEMP'],False),(os.environ['TEMP'],True),(r'C:\Windows\Logs',True)]:
        m=FileForensicsModule();m.on_start(SimpleNamespace(config=None));w=m.create_widget()
        w.resize(1150,820);w.show()
        m._folder_edit.setText(folder);m._recurse_cb.setChecked(recursive)
        start=time.perf_counter();first=None;m._on_search_clicked()
        while m._search_worker is not None:
            app.processEvents()
            if first is None and m._model.rowCount():
                w.grab()  # force actual render before timing the first visible row
                first=time.perf_counter()-start
            time.sleep(.002)
        elapsed=time.perf_counter()-start
        app.processEvents()
        print(json.dumps(dict(folder=folder,recursive=recursive,first_row=first,total=elapsed,
            matches=len(m._current_results),skipped=m._last_skip_count,status=m._progress_label.text())),flush=True)
        m.on_stop();QThreadPool.globalInstance().waitForDone(10000);app.processEvents();w.close()
