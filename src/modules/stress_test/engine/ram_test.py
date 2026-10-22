"""Bounded-scratch RAM patterns; never claims to cover memory owned by Windows."""
import hashlib
import logging
import threading
import time

import psutil

logger = logging.getLogger(__name__)
GIB = 1024 ** 3
CHUNK_SIZE = 256 * 1024 ** 2
PATTERNS = ('00', 'FF', 'AA55') + tuple(f'walking ones {1 << bit:02X}' for bit in range(8)) + ('seeded random',)


def reserve_bytes(memory):
    return max(2 * GIB, int(.15 * memory.total))


def default_ram_bytes(memory=None):
    memory = psutil.virtual_memory() if memory is None else memory
    return max(0, min(int(.7 * memory.available), memory.available - reserve_bytes(memory)))


def pattern_bytes(pattern, size, offset=0, pass_index=0):
    if pattern == 'seeded random':
        seed = f'WCT RAM v1:{pass_index}:{offset}'.encode('ascii')
        return hashlib.shake_256(seed).digest(size)
    unit = (bytes([int(pattern.rsplit(' ', 1)[1], 16)]) if pattern.startswith('walking ones ')
            else {'00': b'\x00', 'FF': b'\xff', 'AA55': b'\xaa\x55'}[pattern])
    shift = offset % len(unit)
    return (unit * ((size + shift + len(unit) - 1) // len(unit)))[shift:shift + size]


class RamTest:
    def __init__(self, size, stop, *, chunk_size=CHUNK_SIZE, slice_size=1024**2,
                 memory_reader=psutil.virtual_memory, after_write=None):
        if size <= 0 or chunk_size <= 0 or slice_size <= 0:
            raise ValueError('RAM sizes must be positive')
        self.size, self.stop = int(size), stop
        self.chunk_size, self.slice_size = chunk_size, slice_size
        self.memory_reader, self.after_write = memory_reader, after_write
        self.chunks = []
        self._lock = threading.Lock()
        self._stats = {'allocated_bytes': 0, 'tested_bytes': 0, 'passes': 0, 'error_count': 0,
                       'errors': [], 'allocation_reason': '', 'failures': []}
        self.thread = None

    def allocate(self):
        remaining = self.size
        while remaining and not self.stop.is_set():
            size = min(remaining, self.chunk_size)
            memory = self.memory_reader()
            # Reserve also covers the bounded reference/verification scratch space.
            if memory.available - size - 4 * self.slice_size < reserve_bytes(memory):
                with self._lock:
                    self._stats['allocation_reason'] = 'Stopped allocation to preserve the memory reserve'
                return
            try:
                chunk = bytearray(size)
            except MemoryError:
                logger.warning('RAM allocation refused for %d bytes', size)
                with self._lock:
                    self._stats['allocation_reason'] = 'Windows refused a memory allocation'
                return
            self.chunks.append(chunk)
            with self._lock:
                self._stats['allocated_bytes'] += size
            remaining -= size

    def run_pass(self):
        for pattern in PATTERNS:
            for index, chunk in enumerate(self.chunks):
                for offset in range(0, len(chunk), self.slice_size):
                    if self.stop.is_set():
                        return
                    size = min(self.slice_size, len(chunk) - offset)
                    chunk[offset:offset + size] = pattern_bytes(pattern, size, offset, self._stats['passes'])
                if self.after_write:
                    self.after_write(chunk, index, pattern)
                for offset in range(0, len(chunk), self.slice_size):
                    if self.stop.is_set():
                        return
                    size = min(self.slice_size, len(chunk) - offset)
                    expected = pattern_bytes(pattern, size, offset, self._stats['passes'])
                    actual = memoryview(chunk)[offset:offset + size]
                    if hashlib.sha256(actual).digest() != hashlib.sha256(expected).digest():
                        first = next(i for i, value in enumerate(actual) if value != expected[i])
                        with self._lock:
                            self._stats['error_count'] += 1
                            if len(self._stats['errors']) < 100:
                                self._stats['errors'].append({'time': time.time(), 'chunk': index,
                                    'offset': offset + first, 'pattern': pattern,
                                    'expected': expected[first], 'got': actual[first]})
                    actual.release()
        if self.chunks:
            with self._lock:
                self._stats['passes'] += 1
                self._stats['tested_bytes'] = self._stats['allocated_bytes']

    def start(self):
        self.thread = threading.Thread(target=self._run, name='StressRAM', daemon=True)
        self.thread.start()

    def _run(self):
        try:
            self.allocate()
            while self.chunks and not self.stop.is_set():
                self.run_pass()
        except Exception as exc:
            logger.exception('RAM test failed')
            with self._lock:
                self._stats['failures'].append(str(exc))
            self.stop.set()
        finally:
            self.release()

    def release(self):
        self.chunks.clear()

    def join(self):
        if self.thread and self.thread.ident is not None:
            self.thread.join()

    def snapshot(self):
        with self._lock:
            return {**self._stats, 'errors': list(self._stats['errors']), 'failures': list(self._stats['failures'])}
