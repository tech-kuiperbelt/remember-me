"""Runtime-owned admission and single-action coordination for online maintenance."""

import threading
from contextlib import contextmanager


class MaintenanceRejected(RuntimeError):
    def __init__(self, code, detail):
        self.code = code
        super().__init__(detail)


class MaintenanceGate:
    def __init__(self):
        self._condition = threading.Condition(threading.RLock())
        self.state = "normal"
        self.action = None
        self.readers = 0
        self.writers = 0
        self.last_result = None
        self.error = None
        self.required_reason = None

    def require(self, reason):
        with self._condition:
            self.required_reason = reason
            self.state = "maintenance"

    def release_requirement(self):
        with self._condition:
            self.required_reason = None

    def snapshot(self):
        with self._condition:
            return {"state": self.state, "action": self.action,
                    "readers": self.readers, "writers": self.writers,
                    "last_result": self.last_result, "error": self.error,
                    "required_reason": self.required_reason}

    @contextmanager
    def access(self, *, write=False):
        with self._condition:
            self._check(write)
            if write:
                self.writers += 1
            else:
                self.readers += 1
        try:
            yield
        finally:
            with self._condition:
                if write:
                    self.writers -= 1
                else:
                    self.readers -= 1
                self._condition.notify_all()

    def check(self, *, write=False):
        """Validate admission without holding a reader/writer slot.

        Callers use this to reject a request early (before parsing) while the
        actual work still re-checks admission when it runs on a worker.
        """
        with self._condition:
            self._check(write)

    def _check(self, write):
        if self.required_reason:
            raise MaintenanceRejected("model_not_ready", "Model is not ready. Configure proxy and retry at /admin/console")
        if self.state == "switching":
            raise MaintenanceRejected("maintenance_switching", "database is switching; retry shortly")
        if self.state == "unavailable":
            raise MaintenanceRejected("maintenance_unavailable", "database recovery needs operator attention")
        if write and self.state != "normal":
            raise MaintenanceRejected("maintenance_mode", "memory writes are paused for maintenance; retry later")

    def enter(self, on_ready=None):
        with self._condition:
            if self.state != "normal":
                raise MaintenanceRejected("maintenance_busy", "maintenance is already active")
            self.state = "entering"
            while self.writers:
                self._condition.wait()
        try:
            if on_ready:
                on_ready()
        except BaseException:
            with self._condition:
                self.state = "normal"
                self._condition.notify_all()
            raise
        with self._condition:
            self.state = "maintenance"
            self._condition.notify_all()

    def exit(self):
        with self._condition:
            if self.required_reason:
                raise MaintenanceRejected("model_not_ready", "Model must finish downloading, loading and validation before maintenance can exit")
            if self.state != "maintenance" or self.action or self.error:
                raise MaintenanceRejected("maintenance_busy", "maintenance cannot exit while work or an error remains")
            self.state = "normal"
            self._condition.notify_all()

    def start_action(self, name):
        with self._condition:
            if self.required_reason and name not in ("restart", "shutdown"):
                raise MaintenanceRejected("model_not_ready", "Model is not ready; configure proxy and retry first")
            if self.state != "maintenance":
                raise MaintenanceRejected("maintenance_required", "enter maintenance mode first")
            if self.action:
                raise MaintenanceRejected("maintenance_busy", "another maintenance action is running")
            if self.error and name not in ("sync", "restart", "shutdown"):
                raise MaintenanceRejected("maintenance_unavailable", "repair recovery with Sync before other actions")
            self.action = name

    def finish_action(self, result=None, error=None, *, sticky=False):
        with self._condition:
            self.last_result = result
            if error and sticky:
                self.error = str(error)
            self.action = None
            self._condition.notify_all()

    def clear_error(self):
        with self._condition:
            self.error = None

    def wait_idle(self):
        with self._condition:
            while self.action or self.state == "entering":
                self._condition.wait()

    @contextmanager
    def switching(self):
        with self._condition:
            if self.state != "maintenance" or self.action != "recover":
                raise RuntimeError("recover requires an active maintenance action")
            self.state = "switching"
            while self.readers or self.writers:
                self._condition.wait()
        try:
            yield
        finally:
            with self._condition:
                if self.state == "switching":
                    self.state = "maintenance"
                self._condition.notify_all()
