"""D-Bus client for the root helper service and systemctl helpers (no shell anywhere).

Every privileged operation goes through the org.veloscroll.gui system-bus service
(helpers/helper_service.py), gated per call by polkit (org.veloscroll.gui.policy). `run_priv` keeps
the old [cmd, args...] spelling and translates it to a D-Bus method call, so the tab code barely
changes; `PrivJob` runs that blocking call in its worker thread — for auth_admin actions the native
polkit prompt blocks only this thread, never the GUI.

QtDBus blocking calls carry no client-side deadline: the helper enforces per-operation caps itself
(git 60 s, journalctl/systemctl 20 s) and an abandoned auth dialog is inherently interactive, so the
`timeout` parameter is kept for API compatibility but is advisory only. A dead service fails fast —
dbus-daemon answers ServiceUnknown immediately instead of hanging.

The button-capture listener (`CaptureThread`) receives the service's PressEvent / CaptureStopped
signals through a single dedicated receiver thread (`_SignalReceiver`): PySide6's QDBusConnection.connect
cannot connect D-Bus signals in this build, so that one path uses dbus-python (a system package the
service already requires) on a private GLib main context. Streams are torn down by StopCapture, by caller
disconnect (the service watches NameOwnerChanged), or by its 1 h cap — there is no re-arm.
"""
import os
import sys
import threading
import time

from PySide6.QtCore import QThread, Signal
from PySide6.QtDBus import QDBus, QDBusConnection, QDBusInterface, QDBusMessage

from . import BUS_NAME, HELPER_IFACE, HELPER_OBJECT_PATH, SERVICE

TIMEOUT_S = 30
CAPTURE_MAX_S = 3600   # requested stream lifetime; the service caps at its own 1 h backstop and never re-arms


def _system_bus_address():
    return os.environ.get("DBUS_SYSTEM_BUS_ADDRESS", "unix:path=/run/dbus/system_bus_socket")


try:   # libdbus thread support — must be initialized before the signal-receiver thread is created (idempotent)
    from dbus.mainloop.glib import threads_init as _threads_init
    _threads_init()
except Exception:   # noqa: BLE001  — without it, capture signals simply never arrive; method calls still work
    pass

ERR_SERVICE_MISSING = "org.freedesktop.DBus.Error.ServiceUnknown"   # name not owned (service down / not installed)
ERR_NAME_NO_OWNER = "org.freedesktop.DBus.Error.NameHasNoOwner"     # owner vanished mid-call
ERR_NOT_AUTHORIZED = "org.veloscroll.gui.NotAuthorized"             # polkit denied/cancelled/no agent/unknown action
ERR_FAILED = "org.veloscroll.gui.Failed"                            # any other helper failure (detail text in the message)


class PrivError(Exception):
    KIND_SERVICE_MISSING = "service_missing"   # helper service not running / unreachable on the system bus
    KIND_NOT_AUTHORIZED = "not_authorized"     # polkit denied, cancelled, or no agent for an auth_admin action
    KIND_COMMAND_FAILED = "command_failed"     # any other failure; .stderr holds the detail text

    def __init__(self, kind, message, stderr=""):
        super().__init__(message)
        self.kind = kind
        self.stderr = stderr


def _iface():
    return QDBusInterface(BUS_NAME, HELPER_OBJECT_PATH, HELPER_IFACE, QDBusConnection.systemBus())


def _to_priv_error(name, message):
    """Map a D-Bus error reply to a PrivError; the detail text keeps whatever the helper sent."""
    detail = (message or "").strip()
    if name == ERR_NOT_AUTHORIZED:
        # dbus-python prefixes async error replies with "NotAuthorizedError: " — strip it, keep the rest
        return PrivError(PrivError.KIND_NOT_AUTHORIZED, detail.removeprefix("NotAuthorizedError: ").strip())
    if name in (ERR_SERVICE_MISSING, ERR_NAME_NO_OWNER):
        return PrivError(PrivError.KIND_SERVICE_MISSING,
                         f"the helper service ({BUS_NAME}) is not running on the system bus")
    if name == ERR_FAILED:
        detail = detail.removeprefix("HelperFailed: ").strip()
    return PrivError(PrivError.KIND_COMMAND_FAILED, detail)


def _translate(args, input_text=None):
    """Old [cmd, args...] spelling -> (dbus_method, dbus_args). Raises PrivError on unknown commands."""
    cmd = str(args[0]) if args else ""
    rest = [str(a) for a in args[1:]]
    if cmd == "ping":
        return "Ping", []
    if cmd == "apply-config":
        # the config text used to go over stdin; it is now an explicit argument (root never opens caller-chosen paths either way)
        if rest or input_text is None:
            raise PrivError(PrivError.KIND_COMMAND_FAILED, "apply-config needs the config text")
        return "ApplyConfig", [input_text]
    if cmd == "logs":
        try:
            n = int(rest[0]) if rest else 50
        except ValueError:
            raise PrivError(PrivError.KIND_COMMAND_FAILED, f"logs: bad line count {rest[0]!r}") from None
        return "Logs", [n]
    if cmd == "devices":
        return "Devices", []
    if cmd == "service":
        if not rest:
            raise PrivError(PrivError.KIND_COMMAND_FAILED, "service needs an action (stop | start)")
        return "Service", [rest[0]]
    if cmd == "preset":
        return "Preset", rest          # list of str -> D-Bus array 'as'
    if cmd == "daemon-reload":
        return "DaemonReload", []
    if cmd == "update":
        return "Update", []
    if cmd == "update-gui":
        return "UpdateGui", []
    raise PrivError(PrivError.KIND_COMMAND_FAILED, f"unknown helper command {cmd!r}")


def run_priv(args, timeout=TIMEOUT_S, input_text=None):
    """Blocking call to the helper service. Returns the result string on success; raises PrivError.

    `input_text` carries the config text for apply-config (the old stdin path). The `timeout` parameter
    is advisory only — see the module docstring."""
    method, dbus_args = _translate(list(args), input_text)
    reply = _iface().callWithArgumentList(QDBus.CallMode.Block, method, dbus_args)
    if reply.type() == QDBusMessage.MessageType.ErrorMessage:
        raise _to_priv_error(reply.errorName(), reply.errorMessage())
    values = reply.arguments()
    if not values:
        return ""
    first = values[0]
    if isinstance(first, bool):      # ApplyConfig -> (b, s): a rejected write is a failure with the reason as detail
        ok, msg = bool(first), str(values[1]) if len(values) > 1 else ""
        if not ok:
            raise PrivError(PrivError.KIND_COMMAND_FAILED, msg or "config rejected")
        return msg.strip()
    return str(first).strip()


class PrivJob(QThread):
    """Run an allowlisted privileged operation off the GUI thread (same signals/contract as before)."""

    finished_ok = Signal(str)      # helper result text on success
    failed = Signal(str, str)      # (PrivError.kind, detail incl. any captured transcript)

    def __init__(self, args, parent=None, timeout=TIMEOUT_S, input_text=None):
        super().__init__(parent)
        self._args = [str(a) for a in args]
        self._timeout = timeout
        self._input_text = input_text

    def run(self):
        try:
            out = run_priv(self._args, timeout=self._timeout, input_text=self._input_text)
        except PrivError as e:
            detail = (e.stderr or str(e)).strip()
            self.failed.emit(e.kind, detail)
        except Exception as exc:   # a worker must never die silently
            self.failed.emit(PrivError.KIND_COMMAND_FAILED, f"unexpected error: {exc}")
        else:
            self.finished_ok.emit(out)


# --- capture listener ---------------------------------------------------------------

def start_capture(paths, max_s):
    """Blocking StartCapture (auth_admin — the native polkit prompt appears here). Returns the stream id."""
    reply = _iface().callWithArgumentList(QDBus.CallMode.Block, "StartCapture", [list(paths), int(max_s)])
    if reply.type() == QDBusMessage.MessageType.ErrorMessage:
        raise _to_priv_error(reply.errorName(), reply.errorMessage())
    return int(reply.arguments()[0])


def stop_capture():
    """Blocking StopCapture — stops every stream (ungated on the service side). Raises PrivError."""
    reply = _iface().callWithArgumentList(QDBus.CallMode.Block, "StopCapture", [])
    if reply.type() == QDBusMessage.MessageType.ErrorMessage:
        raise _to_priv_error(reply.errorName(), reply.errorMessage())
    return str(reply.arguments()[0])


class _SignalReceiver(threading.Thread):
    """Dedicated GLib-loop thread receiving the helper's PressEvent / CaptureStopped signals.

    PySide6's QDBusConnection.connect cannot connect D-Bus signals in this build (verified: it corrupts
    the slot name and fails even for org.freedesktop.DBus.NameOwnerChanged), so signal reception uses
    dbus-python — a system package the helper service already requires — on a private GMainContext. The
    GUI thread's context belongs to Qt's own Glib event dispatcher and must not be touched, hence the
    fresh MainContext + explicit acquire(). Handlers run on THIS thread: they only touch dicts under a
    lock, threading.Events, and emit Qt signals (cross-thread emission is queued onto the listener's
    GUI thread — safe)."""

    def __init__(self):
        super().__init__(daemon=True, name="veloscroll-dbus-signals")
        self._ready = threading.Event()      # set once the signal filters are installed on the connection
        self._press_handlers = {}            # stream_id -> callable(code)
        self._stop_waits = {}                # stream_id -> [threading.Event, {"reason": str}]
        self._lock = threading.Lock()

    def run(self):
        try:
            from gi.repository import GLib
            from dbus.mainloop.glib import DBusGMainLoop   # noqa: F401  (import validates availability)
            import dbus
            ctx = GLib.MainContext.new()
            if not ctx.acquire():
                return                          # cannot own a context — capture will fail with service errors anyway
            loop = GLib.MainLoop(ctx)
            DBusGMainLoop(set_as_default=True)  # must run on THIS thread, before the connection is created
            conn = dbus.bus.BusConnection(_system_bus_address())
            # this build's add_signal_receiver names the sender parameter `bus_name` (not `sender`)
            conn.add_signal_receiver(self._on_press, signal_name="PressEvent", bus_name=BUS_NAME,
                                     path=HELPER_OBJECT_PATH, dbus_interface=HELPER_IFACE)
            conn.add_signal_receiver(self._on_stopped, signal_name="CaptureStopped", bus_name=BUS_NAME,
                                     path=HELPER_OBJECT_PATH, dbus_interface=HELPER_IFACE)
            self._ready.set()
            loop.run()
        except Exception as exc:   # a dead receiver must never take the GUI down with it
            print(f"veloscroll signal receiver failed to start: {exc}", file=sys.stderr)

    def register(self, stream_id, event, holder, on_press):
        self._ready.wait(5)       # the filters must be installed before a live stream can emit into us
        with self._lock:
            self._press_handlers[int(stream_id)] = on_press
            self._stop_waits[int(stream_id)] = [event, holder]

    def unregister(self, stream_id):
        with self._lock:
            self._press_handlers.pop(int(stream_id), None)
            self._stop_waits.pop(int(stream_id), None)

    def _on_press(self, stream_id, code):
        with self._lock:
            handler = self._press_handlers.get(int(stream_id))
        if handler is not None:
            handler(int(code))

    def _on_stopped(self, stream_id, reason):
        with self._lock:
            wait = self._stop_waits.pop(int(stream_id), None)
            self._press_handlers.pop(int(stream_id), None)
        if wait is not None:
            wait[1]["reason"] = str(reason)
            wait[0].set()


_SIGNAL_RECEIVER = None
_SIGNAL_RECEIVER_LOCK = threading.Lock()


def _signal_receiver():
    """The shared signal receiver (started once; safe from any thread)."""
    global _SIGNAL_RECEIVER
    with _SIGNAL_RECEIVER_LOCK:
        if _SIGNAL_RECEIVER is None or not _SIGNAL_RECEIVER.is_alive():
            _SIGNAL_RECEIVER = _SignalReceiver()
            _SIGNAL_RECEIVER.start()
        return _SIGNAL_RECEIVER


class CaptureThread(QThread):
    """One capture stream off the GUI thread (D-Bus based; replaces the old `priv.py capture` process).

    run() issues StartCapture (blocking — for auth_admin this is where the native polkit prompt appears),
    then waits until the service reports that exact stream stopped via CaptureStopped, or stop() is called.
    Press edges arrive as pressed(int) on the GUI thread; BTN_LEFT is dropped here: assigning a code IS a
    left click in this dialog, so capturing it would flood the list with UI noise on every assignment.

    There is no re-arm: when the stream ends (nodes vanished / 1 h cap / StopCapture from any dialog)
    failed(kind, detail) fires once and the dialog offers [Refresh source] to start a new one (fresh auth
    prompt). kind is a PrivError.* value for start failures — the dialog closes itself on not_authorized
    (the user cancelled/denied the polkit prompt) — or "stream_ended" when a live stream ends."""

    pressed = Signal(int)      # key code of a press edge (repeats are value == 2 and ignored by the service)
    starting = Signal()        # StartCapture is in flight — an auth prompt may appear right now
    failed = Signal(str, str)  # (kind, detail): PrivError.* when start fails, "stream_ended" otherwise

    def __init__(self, paths, parent=None):
        super().__init__(parent)
        self._paths = list(paths)
        self._stop = False
        self._wake = threading.Event()      # set by stop() so run() never outlives a dead service
        _signal_receiver()                  # ensure the shared signal receiver thread is running

    def stop(self):
        """Ask for shutdown and issue StopCapture; any exit after this is normal (no error banner)."""
        self._stop = True
        self._wake.set()
        try:
            stop_capture()                  # stops all streams — an open sibling dialog re-arms via Refresh source
        except PrivError:
            pass                            # service gone — nothing to stop (its streams die with it / on disconnect)

    def run(self):
        self.starting.emit()
        try:
            stream_id = start_capture(self._paths, CAPTURE_MAX_S)
        except PrivError as e:
            detail = (e.stderr or str(e)).strip()
            hint = " — install the helper service and its policy (see the Permissions panel)" \
                if e.kind == PrivError.KIND_SERVICE_MISSING else ""
            self.failed.emit(e.kind, f"{detail}{hint}")
            return

        bus = _signal_receiver()
        event = threading.Event()
        holder = {}

        def on_press(code):
            # runs on the GUI thread (the receiver's thread); emitting from there is safe for our listeners
            if code != _btn_left():
                self.pressed.emit(code)

        bus.register(stream_id, event, holder, on_press)
        try:
            if self._stop:                  # stop() raced the start — tear our stream down again (its StopCapture ran first)
                try:
                    stop_capture()
                except PrivError:
                    pass
                return
            deadline = time.monotonic() + CAPTURE_MAX_S + 30   # generous bound; the service's cap fires CaptureStopped first
            while time.monotonic() < deadline:
                if event.wait(0.2) or self._wake.is_set():
                    break
        finally:
            bus.unregister(stream_id)
        if self._stop:                      # intentional stop — any exit after stop() is normal (no error banner)
            return
        reason = holder.get("reason")
        if reason is None:                  # the wait bound expired without a CaptureStopped — service wedged?
            self.failed.emit("stream_ended", "listener did not report a stop (the helper service may be wedged)")
        else:
            self.failed.emit("stream_ended", reason)


def _btn_left():
    """BTN_LEFT (0x110), dropped as UI noise — the evdev name when importable, else the ABI constant."""
    try:
        from evdev import ecodes as e
        return e.BTN_LEFT
    except Exception:
        return 0x110


# --- systemctl helpers (unchanged: plain user-level systemctl queries) ---------------

def _systemctl(action):
    import subprocess
    try:
        p = subprocess.run(["systemctl", action, SERVICE], capture_output=True, text=True, timeout=5)
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return "unknown"
    return (p.stdout or "").strip() or "unknown"


def systemctl_state():
    """Printed state string from `systemctl is-active` ('active', 'inactive', 'failed', ...)."""
    return _systemctl("is-active")


def systemctl_enabled():
    """Printed state string from `systemctl is-enabled` ('enabled', 'disabled', ...)."""
    return _systemctl("is-enabled")
