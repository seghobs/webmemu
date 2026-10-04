"""Bounded background refresh and serialized device jobs for a responsive API."""
import copy
import threading
import time
import uuid
from collections import deque


class SnapshotCache:
    def __init__(self):
        self.lock = threading.Lock()
        self.entries = {}
        self.busy = set()
        self.errors = {}
        self.generation = 0
        self.retry_after = {}

    def get(self, key, loader, ttl, fallback):
        with self.lock:
            entry = self.entries.get(key)
            if key not in self.busy and time.monotonic() >= self.retry_after.get(key, 0) and (entry is None or time.monotonic() - entry[0] >= ttl):
                self.busy.add(key)
                generation = self.generation
                threading.Thread(target=self._refresh, args=(key, loader, generation), daemon=True, name=f'refresh-{key}').start()
            return copy.deepcopy(entry[1] if entry else fallback)

    def _refresh(self, key, loader, generation):
        try:
            value = loader()
            with self.lock:
                if generation == self.generation:
                    self.entries[key] = (time.monotonic(), value)
                    self.errors.pop(key, None)
        except Exception as exc:
            with self.lock:
                self.errors[key] = str(exc)
                self.retry_after[key] = time.monotonic() + 2
                # Back off after failures instead of starting a retry on every poll.
                if key in self.entries:
                    self.entries[key] = (time.monotonic(), self.entries[key][1])
        finally:
            with self.lock:
                self.busy.discard(key)

    def invalidate(self):
        with self.lock:
            self.generation += 1
            self.retry_after.clear()
            self.entries = {key: (0, value) for key, (_, value) in self.entries.items()}


class JobRegistry:
    def __init__(self):
        self.lock = threading.Lock()
        self.jobs = {}
        self.queue = deque()
        self.worker = None

    def submit(self, key, fn):
        with self.lock:
            for job in self.jobs.values():
                if job['key'] == key and job['state'] in ('queued', 'running'):
                    return job['id']
            if len(self.queue) >= 16:
                raise ValueError('İşlem kuyruğu dolu. Devam eden işlemlerin bitmesini bekleyin.')
            completed = [id for id, job in self.jobs.items() if job['state'] in ('completed', 'failed')]
            for id in completed[:-32]:
                del self.jobs[id]
            id = uuid.uuid4().hex
            self.jobs[id] = {'id': id, 'key': key, 'state': 'queued', 'result': None}
            self.queue.append((id, fn))
            if self.worker is None or not self.worker.is_alive():
                self.worker = threading.Thread(target=self._run, daemon=True, name='device-actions')
                self.worker.start()
            return id

    def _run(self):
        while True:
            with self.lock:
                if not self.queue:
                    self.worker = None
                    return
                id, fn = self.queue.popleft()
                self.jobs[id]['state'] = 'running'
            try:
                result = fn()
                state = 'failed' if result.get('success') is False else 'completed'
            except Exception as exc:
                state, result = 'failed', {'success': False, 'error': str(exc)}
            with self.lock:
                self.jobs[id].update(state=state, result=result)

    def running(self):
        with self.lock:
            return any(job['state'] == 'running' for job in self.jobs.values())

    def get(self, id):
        with self.lock:
            job = self.jobs.get(id)
            return copy.deepcopy({k:v for k,v in job.items() if k != 'key'}) if job else None
