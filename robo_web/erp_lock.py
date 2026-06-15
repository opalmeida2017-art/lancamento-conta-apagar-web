import threading

from tenant_context import get_tenant_slug


class TenantERPLock:
    def __init__(self):
        self._locks = {}
        self._guard = threading.RLock()
        self._local = threading.local()

    def _tenant_key(self):
        return get_tenant_slug() or "__default__"

    def _lock_for_current_tenant(self):
        key = self._tenant_key()
        with self._guard:
            return self._locks.setdefault(key, threading.RLock())

    def __enter__(self):
        lock = self._lock_for_current_tenant()
        lock.acquire()
        stack = getattr(self._local, "stack", None)
        if stack is None:
            stack = []
            self._local.stack = stack
        stack.append(lock)
        return lock

    def __exit__(self, exc_type, exc, tb):
        stack = getattr(self._local, "stack", [])
        lock = stack.pop()
        lock.release()
        return False


ERP_LOCK = TenantERPLock()
