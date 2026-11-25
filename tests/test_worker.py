from core.worker import Worker, WorkerSignals

def test_worker_cancel_flag():
    def task(worker):
        return "done"
    w = Worker(task)
    assert not w.is_cancelled
    w.cancel()
    assert w.is_cancelled

def test_worker_signals_exist():
    signals = WorkerSignals()
    assert hasattr(signals, "result")
    assert hasattr(signals, "error")
    assert hasattr(signals, "progress")
    assert hasattr(signals, "cancelled")

def test_worker_callable_receives_worker_ref():
    received_ref = []
    def task(worker):
        received_ref.append(worker)
        return 42
    w = Worker(task)
    w.run()
    assert len(received_ref) == 1
    assert received_ref[0] is w

def test_com_worker_initializes_com_without_pythoncom():
    import sys
    from core.worker import COMWorker
    sys.modules.pop("pythoncom", None)
    ran = []
    result_val = []
    def task(worker):
        ran.append(True)
        return "ok"
    cw = COMWorker(task)
    cw.signals.result.connect(result_val.append)
    cw.run()
    assert ran == [True]
    assert result_val == ["ok"]
    assert "pythoncom" not in sys.modules
