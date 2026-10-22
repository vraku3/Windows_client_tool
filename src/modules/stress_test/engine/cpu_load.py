"""Known-answer integer/hash/compression load; C operations release the GIL."""
import hashlib
import logging
import lzma
import os
import threading
import time
import zlib

logger = logging.getLogger(__name__)


def workloads():
    return {'sha256': lambda b: hashlib.sha256(b).digest(),
            'sha512': lambda b: hashlib.sha512(b).digest(),
            'blake2b': lambda b: hashlib.blake2b(b).digest(),
            'zlib': lambda b: zlib.compress(b),
            'lzma': lambda b: lzma.compress(b, preset=0)}


class CpuLoad:
    def __init__(self, threads, stop, *, operations=None, transform=None):
        if not 1 <= threads <= (os.cpu_count() or 1):
            raise ValueError('Thread count exceeds logical CPU count')
        self.stop = stop
        self.operations = workloads() if operations is None else operations
        if not self.operations:
            raise ValueError('At least one CPU workload is required')
        self.data = hashlib.shake_256(b'WinClientTool CPU stability v1').digest(64 * 1024)
        self.expected = {}  # Prepared by the session on its orchestration thread.
        self.transform = transform
        self.threads = []
        self._counts = [0] * threads
        self._errors = []
        self._error_count = 0
        self._failures = []
        self._lock = threading.Lock()

    def start(self):
        if self.threads:
            raise RuntimeError('CPU load cannot be restarted')
        self.expected = {name: fn(self.data) for name, fn in self.operations.items()}
        try:
            for index in range(len(self._counts)):
                if self.stop.is_set():
                    break
                thread = threading.Thread(target=self._run, args=(index,), name=f'StressCPU-{index}', daemon=True)
                thread.start()
                self.threads.append(thread)
        except BaseException:
            self.stop.set()
            self.join()
            raise

    def _run(self, index):
        try:
            while not self.stop.is_set():
                for name, fn in self.operations.items():
                    if self.stop.is_set():
                        return
                    got = fn(self.data)
                    if self.transform:
                        got = self.transform(name, got)
                    with self._lock:
                        self._counts[index] += 1
                        if got != self.expected[name]:
                            self._error_count += 1
                            if len(self._errors) < 100:
                                self._errors.append({'time': time.time(), 'thread': index, 'workload': name,
                                                     'expected': self.expected[name][:16].hex(),
                                                     'got': got[:16].hex()})
        except Exception as exc:
            logger.exception('CPU load thread failed')
            with self._lock:
                self._failures.append(f'Thread {index}: {exc}')
            self.stop.set()

    def join(self):
        for thread in self.threads:
            thread.join()

    def snapshot(self):
        with self._lock:
            return {'iterations': list(self._counts), 'error_count': self._error_count,
                    'errors': list(self._errors), 'failures': list(self._failures)}
