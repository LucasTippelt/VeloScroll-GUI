"""State-file watcher (250 ms, mtime+size compare), distance-file watcher (~33 Hz) and service poller (2 s).

A malformed read keeps the last good payload and raises a rate-limited warning —
it never crashes the GUI. Flag-off suspicion: the service is observed active AND
no state file exists for >5 s continuously; the clock starts when the service
first becomes active, so the signal fires ~5 s after startup in that situation.

The distance file (daemon key DISTANCE_FILE) is rewritten by the daemon on every
ticker tick while a scroll session is held — to keep the live curve markers smooth
it is polled at ~33 Hz instead of the state file's 4 Hz. It is optional: with the
key off the file simply never appears and no distance signal ever fires. Malformed
reads are retried silently on the next tick (atomic replace makes them near-impossible,
and a rate-limited status-bar flash at this poll rate would just be noise).
"""
import json
import os
import time

from PySide6.QtCore import QObject, QTimer, Signal

from . import DISTANCE_PATH, STATE_PATH
from .proc import systemctl_state


class StateWatcher(QObject):
    state_changed = Signal(dict)        # a new parsed payload (last good one kept on bad reads)
    distance_changed = Signal(dict)     # a new parsed distance document (only while the daemon writes it)
    state_warning = Signal(str)         # malformed/unreadable read or other watch problem
    service_changed = Signal(str)       # 'active' / 'inactive' / 'failed' / ...
    flag_off_suspected = Signal()       # service active, no state file for >5 s continuously

    STATE_INTERVAL_MS = 250
    DISTANCE_INTERVAL_MS = 30           # ~33 Hz: the daemon rewrites the file every tick (~4 ms) while held; this is how fast the markers can follow
    SERVICE_INTERVAL_MS = 2000
    FLAG_OFF_DELAY_S = 5.0
    WARNING_COOLDOWN_S = 10.0

    def __init__(self):
        super().__init__()
        self.payload = None            # last good payload (None until the first successful read)
        self.last_read_ts = None       # wall-clock of the last successful parse (status bar)
        self.state_mtime_ns = None     # st_mtime_ns at that read ("pending reload" comparison)
        self.service_state = "unknown"

        self._last_sig = None          # (st_mtime_ns, st_size) of the last handled file state
        self._present = False          # whether the last stat found the file
        self._no_state_since = None    # monotonic ts: service active without a state file since ...
        self._flag_off_emitted = False
        self._warn_ts = 0.0

        self._dist_sig = None          # (st_mtime_ns, st_size) of the last handled distance-file state
        self._t_state = QTimer(self)
        self._t_state.setInterval(self.STATE_INTERVAL_MS)
        self._t_state.timeout.connect(self._tick_state)
        self._t_distance = QTimer(self)
        self._t_distance.setInterval(self.DISTANCE_INTERVAL_MS)
        self._t_distance.timeout.connect(self._tick_distance)
        self._t_service = QTimer(self)
        self._t_service.setInterval(self.SERVICE_INTERVAL_MS)
        self._t_service.timeout.connect(self._tick_service)

    def start(self):
        """Run the first pass immediately, then arm all timers."""
        self._tick_state()
        self._tick_distance()
        self._tick_service()
        self._t_state.start()
        self._t_distance.start()
        self._t_service.start()

    def stop(self):
        self._t_state.stop()
        self._t_distance.stop()
        self._t_service.stop()

    # -- state file ---------------------------------------------------------

    def _warn(self, msg):
        now = time.monotonic()
        if now - self._warn_ts >= self.WARNING_COOLDOWN_S:
            self._warn_ts = now
            self.state_warning.emit(msg)

    def _tick_state(self):
        try:
            st = os.stat(STATE_PATH)
        except OSError:
            sig, present = None, False
        else:
            sig, present = (st.st_mtime_ns, st.st_size), True
        self._present = present
        if sig == self._last_sig:
            return                     # unchanged (including "still absent") — nothing to do
        try:
            with open(STATE_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
            if not isinstance(data, dict):
                raise ValueError("payload is not a JSON object")
        except (OSError, ValueError) as exc:
            # keep the last good payload; _last_sig stays stale so we retry next tick
            self._warn(f"state file unreadable or malformed ({exc.__class__.__name__}); keeping last good payload")
            return
        self._last_sig = sig
        self.payload = data
        self.last_read_ts = time.time()
        self.state_mtime_ns = st.st_mtime_ns
        self.state_changed.emit(data)

    def _tick_distance(self):
        """Poll the high-rate distance file; emit on every rewrite (mtime+size compare, like the state tick)."""
        try:
            st = os.stat(DISTANCE_PATH)
        except OSError:
            if self._dist_sig is not None:      # file vanished (daemon stopped writing): consumers clear from their last doc's `active`/ts — nothing to emit
                self._dist_sig = None
            return
        sig = (st.st_mtime_ns, st.st_size)
        if sig == self._dist_sig:
            return                     # unchanged — nothing to do
        try:
            with open(DISTANCE_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
            if not isinstance(data, dict):
                raise ValueError("payload is not a JSON object")
        except (OSError, ValueError):
            return                     # keep _dist_sig stale so the next tick retries; never crash the GUI at this poll rate
        self._dist_sig = sig
        self.distance_changed.emit(data)

    # -- service + flag-off suspicion ---------------------------------------

    def _tick_service(self):
        s = systemctl_state()
        if s != self.service_state:
            self.service_state = s
            self.service_changed.emit(s)
        now = time.monotonic()
        if self.service_state == "active" and not self._present:
            if self._no_state_since is None:          # clock starts when the service first becomes active (without a state file)
                self._no_state_since = now
            elif not self._flag_off_emitted and now - self._no_state_since >= self.FLAG_OFF_DELAY_S:
                self._flag_off_emitted = True
                self.flag_off_suspected.emit()
        else:
            # condition cleared (state file appeared or service no longer active): allow a fresh signal later
            self._no_state_since = None
            self._flag_off_emitted = False
