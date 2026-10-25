"""Review snapshots for File Forensics in both application themes."""
import os
os.environ['QT_QPA_PLATFORM']='offscreen'
import sys,tempfile
from pathlib import Path
from datetime import datetime,timedelta
from types import SimpleNamespace
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from PyQt6.QtWidgets import QApplication
from PyQt6.QtCore import Qt
from modules.file_forensics.file_forensics_module import FileForensicsModule
from modules.file_forensics.engine.analysis import FileAnalysis
from modules.file_forensics.engine.file_metadata import FileMetadata
from modules.file_forensics.engine.locking_processes import LockingProcess
from modules.file_forensics.engine.creator_heuristic import CreatorCandidate
from core.procengine.signatures import SignatureFacts, NOT_SIGNED, VALID
from modules.file_forensics import history_log
app=QApplication([])
from PyQt6.QtGui import QFontDatabase
QFontDatabase.addApplicationFont('C:/Windows/Fonts/segoeui.ttf')
with tempfile.TemporaryDirectory(prefix='wct-forensics-render-') as scratch:
    history_log._history_path=lambda: str(Path(scratch)/'history.json')
    m=FileForensicsModule();m.on_start(SimpleNamespace(config=None));w=m.create_widget()
    now=datetime.now();rows=[]
    for i in range(60):
        path=rf'C:\Users\iorda\AppData\Local\Temp\session-{i:02d}.exe'
        meta=FileMetadata(path,(i+1)*123456,now-timedelta(minutes=i),now,now,'iorda',False)
        candidate=CreatorCandidate(4242,'setup.exe',r'C:\Downloads\setup.exe','iorda',now,1.2)
        sig=SignatureFacts(path=candidate.path,status=NOT_SIGNED if i%3==0 else VALID,signer='' if i%3==0 else 'Example software')
        rows.append(FileAnalysis(meta,[LockingProcess(4242,'setup.exe','File')] if i%4==0 else [],
            'Shared readable handle snapshot; 3 protected processes refused inspection.',[candidate],sig,None))
        m._detail_cache[path]={'metadata':('Attributes: archive; cloud placeholder: no',)}
    m._populate_table(rows);m._table.selectRow(0)
    m._progress_label.setText('Completed — 60 matches; 3 skipped; one shared handle snapshot')
    for theme,width,height,filename in [('dark',1280,900,'forensics_shot.png'),('light',1280,900,'forensics_shot_light.png'),('light',760,850,'forensics_shot_narrow.png')]:
        from core.theme_manager import ThemeManager
        ThemeManager('src/ui/styles').apply_theme(theme)
        w.resize(width,height);w.show();app.processEvents();w.resize(width,height);app.processEvents()
        path=Path(tempfile.gettempdir())/filename
        assert w.grab().save(str(path))
        print(path, 'size=',w.width(),w.height(),'minimum=',w.minimumSizeHint().width(), flush=True)
    m.on_stop();w.close()
