#!/usr/bin/env python3
"""Root-owned D-Bus helper service for the VeloScroll GUI.

A long-lived root process on the system bus that performs every privileged operation, gated per call by
polkit (see org.veloscroll.gui.policy). The subject is always the unique bus name of the caller taken from
the incoming message — never an argument — so no client can act as someone else.

Bus:      org.veloscroll.gui          (system bus; owned via veloscroll-gui-helper.service)
Object:   /org/veloscroll/gui/Helper
Interface org.veloscroll.gui.Helper

Methods (in -> out):
  Ping()                       -> s        liveness probe for the permissions panel
  ApplyConfig(text)            -> (b, s)   validate with the daemon's own parser + atomic replace of
                                           /etc/veloscroll/config.ini; (False, reason) on rejection.
                                           Silent config tier — unless the new text changes the effective
                                           TICK value, in which case it is gated by the auth_admin tick action
  Logs(n)                      -> s        last n journal lines of the veloscroll service
  Devices()                    -> s        JSON list of every /dev/input/event* node readable as root
  StartCapture(paths[], max_s) -> u        stream EV_KEY press edges from input nodes; returns a stream id.
                                           Strictly read-only (O_RDONLY, never written — no injection).
                                           One polkit auth_admin prompt per call.
  StopCapture()                -> s        stop every capture stream (no gate: nuisance-level only)
  Service(action)              -> s        systemctl stop|start veloscroll
    Preset(ops[])                -> s        strict passthrough to the deployed veloscroll_preset.py in /usr/lib/veloscroll
                                             (same grammar as the CLI); fail-closed if that copy is missing. Silent config tier — unless a loadall or
                                             restore would put a different effective TICK into config.ini, then the auth_admin tick action gates it
   DaemonReload()               -> s        systemctl daemon-reload
   Update()                     -> s        one-click daemon update: pull as the checkout owner, re-deploy changed files to
                                            /usr/lib/veloscroll (self-healing pre-v1.4 installs), conditional unit refresh +
                                            restart; transcript returned (may take ~2 min)
  UpdateGui()                  -> s        one-click GUI update; transcript returned

Signals:
  PressEvent(stream:i, code:i)      one per EV_KEY press edge of that stream
  CaptureStopped(stream:i, reason:s)  stream ended (stopped by caller / caller disconnected / all nodes
                                       vanished / max time reached)

Auth tiers (org.veloscroll.gui.policy): read/config/service are `yes` (silent); capture/update/tick are
`auth_admin` (native polkit prompt every call). ApplyConfig and Preset normally run on the silent config
tier — but a resolver decides per call, read-only and before authorization: when such a write would actually
change TICK (a different effective tick in the new text, or in a preset loadall / restored backup), it is
raised to the auth_admin `tick` action instead. Denial, cancellation or a missing agent surface as the D-Bus
error org.veloscroll.gui.NotAuthorized; any other failure is org.veloscroll.gui.Failed with the detail text.

Threading: the GLib main loop is the only D-Bus I/O thread (dispatch + signal emission). Every method
body runs in a worker thread — including the polkit CheckAuthorization, which blocks on the auth prompt
for auth_admin actions — and replies via GLib.idle_add. Capture streams run their own select-loop
threads; teardown happens on StopCapture, caller disconnect (NameOwnerChanged with an empty new owner),
all nodes vanished, or the 1 h cap.

This process must never run unprivileged: it refuses to start when euid != 0. (The update path deliberately
drops to the checkout's owning user for its git steps via setpriv — see _git.) The DEPLOYED copy at
HELPER_DEPLOY_PATH is what must stay root-owned 755 — the permissions panel flags ownership drift there and
offers a copy-paste fix; the repo copy (REPO_HELPER_PATH) is plain source, updated by git like everything else.

Same model for the daemon: this helper imports veloscroll / veloscroll_preset from DEPLOY_DIR
(/usr/lib/veloscroll, root-owned 755) and runs the preset CLI from there — never from the user-writable
checkout at DAEMON_DIR, which is used only for git operations and the unit-file copy. A missing deployed
copy fails closed with a "run the deploy block" message instead of falling back to the checkout; Update()
re-deploys changed files (and self-heals pre-v1.4 installs) before any restart.
"""
import configparser
import datetime
import glob
import itertools
import json
import os
import pwd
import re
import shutil
import subprocess
import sys
import threading
import time

from dbus.mainloop.glib import DBusGMainLoop, threads_init
DBusGMainLoop(set_as_default=True)   # must be set before any connection is created (dispatch on the GLib loop)
threads_init()                       # libdbus-glib thread support — required before this process spawns worker threads
import dbus                            # noqa: E402
import dbus.exceptions                 # noqa: E402
import dbus.lowlevel                   # noqa: E402
import dbus.service                    # noqa: E402
from gi.repository import GLib         # noqa: E402

DAEMON_DIR = "/opt/veloscroll"       # user-owned source checkout — git operations + unit-file copy only, never executed by root
DEPLOY_DIR = "/usr/lib/veloscroll"   # deployed daemon copy (root-owned 755) — the unit and this helper execute from here, never DAEMON_DIR
DAEMON_DEPLOY_FILES = (              # repo-relative files that must stay in DEPLOY_DIR (the five elevated ones; veloscroll_seed.py is user-run only)
    "veloscroll.py",
    "veloscroll_preset.py",
    os.path.join("helpers", "ListDeviceNames.py"),
    os.path.join("helpers", "ListButtonPresses.py"),
    os.path.join("helpers", "veloscroll_tuning_helper.py"),
)
GUI_DIR = "/opt/veloscroll-gui"
CONFIG_PATH = "/etc/veloscroll/config.ini"
SERVICE = "veloscroll"
HELPER_SERVICE_NAME = "veloscroll-gui-helper"   # this service's own unit; op_update_gui restarts it after a redeploy
REPO_HELPER_PATH = os.path.join(GUI_DIR, "helpers", "helper_service.py")    # source copy (user-owned)
POLICY_REPO_PATH = os.path.join(GUI_DIR, "org.veloscroll.gui.policy")       # source policy (user-owned); op_update_gui redeploys it when a release changes it
HELPER_DEPLOY_DIR = "/usr/lib/veloscroll-gui"
HELPER_DEPLOY_PATH = os.path.join(HELPER_DEPLOY_DIR, "helper_service.py")   # runtime copy — must stay root-owned 755
POLICY_FILE_PATH = "/usr/share/polkit-1/actions/org.veloscroll.gui.policy"  # installed policy — polkitd watches the directory; no reload needed after a redeploy
MAX_CONFIG_BYTES = 1024 * 1024   # configs are ~5 KB — a hard cap on the incoming text (cheap defense-in-depth)

BUS_NAME = "org.veloscroll.gui"
OBJECT_PATH = "/org/veloscroll/gui/Helper"
HELPER_IFACE = "org.veloscroll.gui.Helper"

ACTION_READ = "org.veloscroll.gui.read"        # Ping / Logs / Devices
ACTION_CONFIG = "org.veloscroll.gui.config"    # ApplyConfig / Preset (silent tier — the default for both)
ACTION_SERVICE = "org.veloscroll.gui.service"  # Service / DaemonReload
ACTION_CAPTURE = "org.veloscroll.gui.capture"  # StartCapture (auth_admin)
ACTION_UPDATE = "org.veloscroll.gui.update"    # Update / UpdateGui (auth_admin)
ACTION_TICK = "org.veloscroll.gui.tick"        # ApplyConfig / Preset when they would actually change TICK (auth_admin, resolved per call)

CAPTURE_CAP_S = 3600   # hard backstop per stream: the GUI re-arms explicitly, this only bounds a leaked one


class NotAuthorizedError(Exception):
    """polkit denied, cancelled, or could not authorize the caller — clean 'not authorized' state in the GUI."""
    _dbus_error_name = "org.veloscroll.gui.NotAuthorized"


class HelperFailed(Exception):
    """Any other operation failure; str(self) is the detail text shown by the GUI (transcripts included)."""
    _dbus_error_name = "org.veloscroll.gui.Failed"


def _system_bus_address():
    return os.environ.get("DBUS_SYSTEM_BUS_ADDRESS", "unix:path=/run/dbus/system_bus_socket")


# --- polkit gate ------------------------------------------------------------------

def authorize(sender, action_id):
    """polkit gate via `pkcheck -u` — no D-Bus in this worker thread.

    The caller is identified by its unique bus name (never self-reported). Silent-tier actions (`yes` for
    active users) return immediately without consulting any agent; auth_admin ones raise the native prompt
    and block until answered (-u = user interaction allowed, as before). pkcheck replaces a direct
    CheckAuthorization call on purpose: opening/closing a dbus-python connection from this worker thread
    while DBusGMainLoop is the process default intermittently segfaulted inside _dbus_glib_bindings
    (cross-thread race in the GLib-integrated bindings) — a subprocess keeps all D-Bus traffic on the main
    loop where it belongs. Raises NotAuthorizedError on denial/cancel/no-agent/unknown action."""
    try:
        p = subprocess.run(["pkcheck", "-u", "--action-id", str(action_id), "--system-bus-name", str(sender)],
                           capture_output=True, text=True, timeout=120)   # bounds a prompt the user never answers (old dbus default was ~25 s — an accident of the binding, not a design choice)
    except FileNotFoundError:
        raise NotAuthorizedError("authorization check failed (pkcheck not found — is polkit installed?)") from None
    except subprocess.TimeoutExpired:
        raise NotAuthorizedError(f"not authorized for {action_id} (no answer within 120 s)") from None
    if p.returncode != 0:
        err = (p.stderr or "").strip()
        detail = err.splitlines()[0] if err else "unknown error"
        hint = (" — no authentication agent is available in this session (install polkit-gtk-auth "
                "or your desktop's polkit agent)" if "agent" in err.lower() else "")
        if not hint and "is not registered" in err:   # PK_ERROR_UNKNOWN_ACTION — the installed policy predates this action
            hint = (" — the installed policy may be stale (it has no such action) — run [Update GUI…] or reinstall "
                    f"{os.path.basename(POLICY_FILE_PATH)}")
        raise NotAuthorizedError(f"not authorized for {action_id} ({detail}){hint}")


# --- daemon / preset module access ---------------------------------------------------

def _daemon_module():
    """Import the daemon module from the deployed copy (DEPLOY_DIR) — fail closed, never fall back to the checkout."""
    if not os.path.isfile(os.path.join(DEPLOY_DIR, "veloscroll.py")):
        raise HelperFailed(f"deployed daemon copy missing ({DEPLOY_DIR}/veloscroll.py) — run the deploy block in docs/INSTALL.md or use Update")
    if DEPLOY_DIR not in sys.path:
        sys.path.insert(0, DEPLOY_DIR)
    import veloscroll  # noqa: PLC0415 — side-effect-free for a non-daemon process (see module docstring there)
    return veloscroll


def _preset_module():
    """Import the preset CLI from the deployed copy (DEPLOY_DIR) — fail closed, never fall back to the checkout."""
    if not os.path.isfile(os.path.join(DEPLOY_DIR, "veloscroll_preset.py")):
        raise HelperFailed(f"deployed daemon copy missing ({DEPLOY_DIR}/veloscroll_preset.py) — run the deploy block in docs/INSTALL.md or use Update")
    if DEPLOY_DIR not in sys.path:
        sys.path.insert(0, DEPLOY_DIR)
    import veloscroll_preset  # noqa: PLC0415 — top level is constants only (its config load happens inside main())
    return veloscroll_preset


# --- apply-config -----------------------------------------------------------------

def _validate_text(vs, text):
    """Parse `text` exactly like the daemon's load_config does; returns (values|None, error_str)."""
    cp = configparser.ConfigParser(interpolation=None)
    cp.optionxform = str                      # keys are exact Python names (NAME_MATCH, ...)
    try:
        cp.read_string(text)
    except configparser.Error as exc:
        return None, f"malformed INI: {exc}"
    values = dict(vs._DEFAULTS)
    errors = []
    if cp.has_section("veloscroll"):          # unknown sections/keys are ignored, same as the daemon
        for key, raw in cp["veloscroll"].items():
            if key not in vs.DAEMON_KEYS:
                continue
            raw = raw.strip()
            if not raw:                        # empty value -> code default applies
                continue
            try:
                values[key] = vs._parse_ini_value(key, raw)
            except Exception as exc:           # ConfigError (bad type/range); never let a stray exception escape
                errors.append(str(exc))
    if errors:
        return None, "config rejected by the daemon parser — " + "; ".join(errors)
    check = vs.check_config(values)
    if check:
        return None, "config failed validation — " + "; ".join(check)
    return values, None


def _read_config_file():
    """The current config.ini text; "" when missing/unreadable (= the pure code defaults apply)."""
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            return f.read()
    except OSError:
        return ""


def _effective_tick(text):
    """The TICK value the daemon would run if `text` were config.ini — its [veloscroll] TICK parsed by the
    daemon's own parser, or the code default when absent/empty. None when it cannot be determined (import or
    parse failure) — callers then gate conservatively. Read-only: never writes."""
    try:
        vs = _daemon_module()
    except BaseException:
        return None                                 # missing deploy / a malformed disk config kills the import — undeterminable
    cp = configparser.ConfigParser(interpolation=None)
    cp.optionxform = str
    try:
        cp.read_string(text if isinstance(text, str) else "")
    except (configparser.Error, TypeError):
        return None
    raw = ""
    if cp.has_section("veloscroll"):
        raw = (cp["veloscroll"].get("TICK") or "").strip()
    if not raw:                                     # absent/empty TICK -> the code default applies
        return vs._DEFAULTS.get("TICK")
    try:
        return vs._parse_ini_value("TICK", raw)     # parsed values, so "0.05" and "0.050" compare equal
    except Exception:
        return None                                 # unparseable value — undeterminable (a real write would be rejected anyway)


def _apply_config_action(text):
    """The polkit action for an ApplyConfig call, decided read-only before authorization: the auth_admin tick
    tier only when a valid new text would actually change the effective TICK value; otherwise the silent config
    tier (work() re-validates — idempotent). Undeterminable -> gate conservatively (fail closed)."""
    if len(text.encode("utf-8", "replace")) > MAX_CONFIG_BYTES:
        return ACTION_CONFIG                        # work() rejects it before any write — no prompt needed
    try:
        vs = _daemon_module()
    except HelperFailed:
        return ACTION_CONFIG                        # missing deployed daemon copy — work() surfaces that error cleanly, nothing is written
    except BaseException:
        return ACTION_TICK                          # undeterminable (e.g. a malformed disk config kills the import) -> gate conservatively
    if _validate_text(vs, text)[1] is not None:     # rejected text never writes — surface the validation error without a prompt
        return ACTION_CONFIG
    new_tick = _effective_tick(text)
    cur_tick = _effective_tick(_read_config_file())
    if new_tick is None or cur_tick is None:
        return ACTION_TICK                          # undeterminable -> gate conservatively (fail closed)
    return ACTION_TICK if new_tick != cur_tick else ACTION_CONFIG


def _backup(path):
    # %f keeps rapid successive applies in distinct backups; fixed-width, so lexicographic == chronological
    bak = path + ".bak-" + datetime.datetime.now().strftime("%Y%m%d-%H%M%S%f")
    shutil.copy2(path, bak)
    return bak


def _prune_backups(path, max_backups):
    """Keep only the newest `max_backups` files named `<basename>.bak-*` next to path; -1 = unlimited, 0/absent = no-op."""
    if not isinstance(max_backups, int) or isinstance(max_backups, bool) or max_backups < -1:
        raise ValueError(f"invalid MAX_BACKUPS {max_backups!r}")
    if max_backups <= 0:
        return []
    prefix = os.path.basename(path) + ".bak-"
    candidates = sorted(glob.glob(os.path.join(os.path.dirname(path), prefix + "*")))   # oldest first
    pruned = []
    for f in candidates[: max(0, len(candidates) - max_backups)]:
        os.remove(f)
        pruned.append(os.path.basename(f))
    return pruned


def _atomic_write(path, text):
    """Temp file + os.replace in the same directory: the daemon (mtime+size hot-reload) never parses a partial file."""
    d = os.path.dirname(os.path.abspath(path))
    tmp = os.path.join(d, "." + os.path.basename(path) + f".tmp-{os.getpid()}")
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(text)
        if os.path.exists(path):      # keep the original file's mode after replace (a root-owned config.ini stays root-owned)
            os.chmod(tmp, os.stat(path).st_mode & 0o7777)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):       # cleanup on failure only — a successful replace already moved tmp away
            try:
                os.remove(tmp)
            except OSError:
                pass


def apply_config_text(text, config_path=CONFIG_PATH):
    """Validate `text` with the daemon's own parser and atomically replace config_path.

    Returns (ok, message). A rejected write never touches the file or its backups.
    The backup decision honors the NEW text's MAX_BACKUPS (0 = no .bak-* written at all),
    matching veloscroll_preset.py; old backups are pruned to that cap when a new one is made."""
    if len(text.encode("utf-8", "replace")) > MAX_CONFIG_BYTES:
        return False, f"config text too large (cap is {MAX_CONFIG_BYTES} bytes)"
    try:
        vs = _daemon_module()
    except Exception as exc:
        return False, f"could not import the daemon module from {DEPLOY_DIR}: {exc}"
    values, err = _validate_text(vs, text)
    if err:
        return False, err
    notes = []
    try:
        if os.path.exists(config_path):
            mb = values["MAX_BACKUPS"]
            bak = None
            if not (isinstance(mb, int) and not isinstance(mb, bool)) or mb < -1:
                return False, f"invalid MAX_BACKUPS {mb!r} in the new config — refusing to write"
            if mb != 0:                       # MAX_BACKUPS = 0 disables backups entirely — no .bak-* file is written
                bak = _backup(config_path)
                notes.append(f"backup: {os.path.basename(bak)}")
                pruned = _prune_backups(config_path, mb)
                if pruned:
                    notes.append("pruned " + ", ".join(pruned) + f" (MAX_BACKUPS={mb} keeps only the newest)")
        _atomic_write(config_path, text)
    except OSError as exc:
        return False, f"could not write {config_path}: {exc}"
    msg = f"applied {config_path}"
    if notes:
        msg += " (" + "; ".join(notes) + ")"
    return True, msg


# --- logs ---------------------------------------------------------------------------

def op_logs(n):
    """Last n journal lines of the veloscroll service (n <= 0 -> default 50)."""
    try:
        p = subprocess.run(["journalctl", "-u", SERVICE, "-n", str(max(1, int(n))), "--no-pager"],
                           capture_output=True, text=True, timeout=20)
    except (ValueError, FileNotFoundError, subprocess.TimeoutExpired) as exc:
        raise HelperFailed(f"journalctl failed: {exc}") from None
    if p.returncode != 0:
        raise HelperFailed((p.stderr or p.stdout or "journalctl failed").strip())
    return p.stdout.rstrip("\n")


# --- devices ------------------------------------------------------------------------

def op_devices():
    """JSON list of every /dev/input/event* node readable as root: [{"path","name","vendor","product"}].

    vendor/product are hex strings ("0x46d") — the same form ListDeviceNames.py prints, so the GUI's
    in-process enumeration and this fallback stay directly comparable. Nodes that vanish between
    listing and opening are skipped, exactly like the daemon's find_matches()."""
    try:
        import evdev
    except ImportError as exc:
        raise HelperFailed(f"cannot import evdev (is python3-evdev installed?): {exc}") from None
    out = []
    for path in evdev.list_devices():
        try:
            d = evdev.InputDevice(path)
        except OSError:
            continue
        info = d.info
        out.append({"path": path, "name": d.name,
                    "vendor": f"0x{info.vendor:x}", "product": f"0x{info.product:x}"})
    return json.dumps(out)


# --- service ------------------------------------------------------------------------

def op_service(action):
    """`systemctl stop|start veloscroll`, nothing else — the allowlist is the two actions."""
    if action not in ("stop", "start"):
        raise HelperFailed(f"service: unknown action {action!r} — allowed: stop | start")
    try:
        p = subprocess.run(["systemctl", action, SERVICE], capture_output=True, text=True, timeout=20)
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        raise HelperFailed(f"systemctl {action} failed: {exc}") from None
    if p.returncode != 0:
        raise HelperFailed((p.stderr or p.stdout or f"systemctl {action} veloscroll failed").strip())
    return f"{SERVICE} {'stopped' if action == 'stop' else 'started'}"


# --- preset -----------------------------------------------------------------------

def op_preset(ops):
    """Strict passthrough to the deployed veloscroll_preset.py (DEPLOY_DIR) — the allowlist mirrors its _parse_ops exactly.

    Flags are rejected outright (no --config: presets always live next to /etc/veloscroll/config.ini),
    every name is fullmatch-validated against the CLI's own NAME_RE / BAK_RE, and arity follows the
    same grammar — so a malformed or traversal attempt fails here before any subprocess runs.
    The CLI re-validates everything as defense-in-depth; its stdout (list/backups tables) passes through."""
    ops = [str(o) for o in ops]
    if not ops:
        raise HelperFailed("preset: expected at least one operation (save/loaddistances/loadfeel/loadkeys/"
                           "loaddevice/loadscroll/loadall/delete NAME | list | backups | restore [BACKUP])")
    for tok in ops:
        if tok.startswith("-"):
            raise HelperFailed(f"preset: flags are not allowed ({tok!r}) — only bare operations and names")
    try:
        vp = _preset_module()
    except Exception as exc:
        raise HelperFailed(f"could not import veloscroll_preset from {DEPLOY_DIR}: {exc}") from None
    pair_cmds = tuple(vp.LOAD_GROUPS) + ("save", "loadall", "delete")
    first = ops[0]
    if first in ("list", "backups"):
        if len(ops) != 1:
            raise HelperFailed(f"preset: `{first}` takes no arguments — run it alone")
    elif first == "restore":
        if len(ops) > 2:
            raise HelperFailed("preset: `restore` takes at most one argument (a backup name from `backups`)")
        if len(ops) == 2 and not vp.BAK_RE.fullmatch(ops[1]):
            raise HelperFailed(f"preset: invalid backup name {ops[1]!r} — use a name from `backups`")
    else:
        if first not in pair_cmds:
            raise HelperFailed(f"preset: unknown command {first!r} — use one of {'/'.join(pair_cmds)} "
                               "(list/backups/restore run alone)")
        if len(ops) % 2 != 0:
            raise HelperFailed("preset: expected pairs of `COMMAND NAME`, e.g. `loadfeel fast loaddistances quiet`")
        for i in range(0, len(ops), 2):
            cmd, name = ops[i], ops[i + 1]
            if cmd not in pair_cmds:
                raise HelperFailed(f"preset: unknown command {cmd!r} — use one of {'/'.join(pair_cmds)} "
                                   "(list/backups/restore run alone)")
            if not vp.NAME_RE.fullmatch(name):
                raise HelperFailed(f"preset: invalid preset name {name!r} — use only letters, digits, '.', '_' or '-'")
    try:
        p = subprocess.run([sys.executable, os.path.join(DEPLOY_DIR, "veloscroll_preset.py"), *ops],
                           capture_output=True, text=True, timeout=30)
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        raise HelperFailed(f"veloscroll_preset.py failed: {exc}") from None
    if p.returncode != 0:
        raise HelperFailed((p.stderr or "veloscroll_preset.py failed").strip())
    return (p.stdout or "").rstrip("\n")


def _preset_action(ops):
    """The polkit action for a Preset call, decided read-only before authorization: the auth_admin tick tier
    only when an operation would actually put a different effective TICK into config.ini — a `loadall NAME`
    whose preset file carries one, or a `restore [BACKUP]` that puts such a backup back over config.ini.
    Everything else (save/delete/list/backups and the group loads — no group key set contains TICK) stays on
    the silent config tier; an operation the CLI would reject before any write never prompts either.
    Undeterminable -> gate conservatively (fail closed)."""
    ops = [str(o) for o in ops]
    if not ops:
        return ACTION_CONFIG                        # op_preset rejects it — no write, no prompt needed
    first = ops[0]
    if first in ("list", "backups"):
        return ACTION_CONFIG                        # read-only commands (op_preset rejects extra args before any write)
    try:
        vs, vp = _daemon_module(), _preset_module()
    except HelperFailed:
        return ACTION_CONFIG                        # missing deployed copy — op_preset surfaces that error cleanly, nothing is written
    except BaseException:
        return ACTION_TICK                          # undeterminable (e.g. a malformed disk config kills the import) -> gate conservatively
    if first == "restore":
        ref = ops[1] if len(ops) > 1 else None      # op_preset rejects >2 tokens / bad names before any write
        try:
            bak = vp._resolve_backup(vs, ref)       # read-only mirror of the CLI's resolution (listdir + isfile only)
        except Exception:
            return ACTION_CONFIG                    # no such backup / ambiguous — the CLI fails before any write
        target = os.path.join(os.path.dirname(bak), vp.BAK_RE.match(os.path.basename(bak)).group("orig"))
        if target != os.path.abspath(vs.CONFIG_PATH):
            return ACTION_CONFIG                    # restoring a preset file never touches config.ini
        try:
            with open(bak, "r", encoding="utf-8") as f:
                bak_tick = _effective_tick(f.read())
        except OSError:
            return ACTION_TICK                      # undeterminable -> gate conservatively (fail closed)
        cur_tick = _effective_tick(_read_config_file())
        if bak_tick is None or cur_tick is None:
            return ACTION_TICK                      # undeterminable -> gate conservatively (fail closed)
        return ACTION_TICK if bak_tick != cur_tick else ACTION_CONFIG

    if len(ops) % 2 != 0:
        return ACTION_CONFIG                        # op_preset rejects odd arity before any write
    for i in range(0, len(ops), 2):
        if ops[i] != "loadall":
            continue                                # group loads / save / delete never write TICK into config.ini
        name = ops[i + 1]                           # op_preset fullmatch-validates names before any subprocess runs
        path = os.path.join(vp._preset_dir(vs.CONFIG_PATH), name + ".ini")
        if not os.path.isfile(path):
            continue                                # the CLI fails on this pair before any write — no TICK change from it
        try:
            cp = configparser.ConfigParser(interpolation=None)
            cp.optionxform = str
            with open(path, "r", encoding="utf-8") as f:
                cp.read_string(f.read())
        except (OSError, configparser.Error):
            continue                                # the CLI fails on this pair before any write — no TICK change from it
        raw = ""
        if cp.has_section("veloscroll"):
            raw = (cp["veloscroll"].get("TICK") or "").strip()
        if not raw:                                 # absent/empty TICK is never applied by a load
            continue
        try:
            tick = vs._parse_ini_value("TICK", raw)
        except Exception:
            continue                                # the CLI's parse fails on this pair before any write — no TICK change from it
        cur_tick = _effective_tick(_read_config_file())
        if cur_tick is None or tick != cur_tick:
            return ACTION_TICK                      # this loadall would rewrite config.ini with a different effective TICK
    return ACTION_CONFIG                            # no operation in the list can change TICK — silent tier


# --- daemon-reload ------------------------------------------------------------------

def op_daemon_reload():
    """`systemctl daemon-reload`, nothing else — re-reads unit files after a new veloscroll.service is deployed."""
    try:
        p = subprocess.run(["systemctl", "daemon-reload"], capture_output=True, text=True, timeout=20)
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        raise HelperFailed(f"systemctl daemon-reload failed: {exc}") from None
    if p.returncode != 0:
        raise HelperFailed((p.stderr or "systemctl daemon-reload failed").strip())
    return "daemon reloaded"


# --- update -------------------------------------------------------------------

def _git(args, repo_dir=DAEMON_DIR, timeout=60):
    """Run git as the user who owns the checkout. The polkit gate is already the privilege boundary — running
    git as root inside a user-owned checkout only adds "dubious ownership" failures and leaves root-owned
    objects in .git. A root-owned checkout runs as root (same uid, so no dubious-ownership issue there)."""
    cmd = ["git", "-C", repo_dir, *args]
    try:
        pw = pwd.getpwuid(os.stat(repo_dir).st_uid)
    except (OSError, KeyError):
        return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    if pw.pw_uid != 0:
        cmd = ["setpriv", f"--reuid={pw.pw_uid}", f"--regid={pw.pw_gid}", "--init-groups", *cmd]
        env = dict(os.environ, HOME=pw.pw_dir, USER=pw.pw_name, LOGNAME=pw.pw_name)
    else:
        env = None
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, env=env)


def _deploy_helper(add):
    """Deploy the repo's helper source to the system path (root-owned 755); transcript lines via add().

    The unit executes HELPER_DEPLOY_PATH, never the repo copy — so a user-writable checkout can't reach root.
    Returns True on success."""
    try:
        os.makedirs(HELPER_DEPLOY_DIR, exist_ok=True)
        shutil.copy2(REPO_HELPER_PATH, HELPER_DEPLOY_PATH)
        os.chown(HELPER_DEPLOY_PATH, 0, 0)
        os.chmod(HELPER_DEPLOY_PATH, 0o755)
    except OSError as exc:
        add(f"ERROR: helper deploy failed: {exc}")
        return False
    st = os.stat(HELPER_DEPLOY_PATH)
    if st.st_uid != 0 or (st.st_mode & 0o022):
        add("ERROR: deployed helper is not root-owned and/or user-writable — fix manually before restarting")
        return False
    add(f"deployed {REPO_HELPER_PATH} -> {HELPER_DEPLOY_PATH} (root-owned 755)")
    return True


def _deploy_daemon(add):
    """Deploy the five elevated daemon files to DEPLOY_DIR (root-owned 755); transcript lines via add().

    Mirrors the canonical deploy block in docs/INSTALL.md: copy, chown 0:0, chmod 755, then verify every
    deployed file is root-owned and not group/other-writable. The unit executes DEPLOY_DIR/veloscroll.py
    and this helper imports/executes from it — never the checkout — so a user-writable checkout can't reach root.
    Returns True on success."""
    try:
        os.makedirs(os.path.join(DEPLOY_DIR, "helpers"), exist_ok=True)
        for rel in DAEMON_DEPLOY_FILES:
            shutil.copy2(os.path.join(DAEMON_DIR, rel), os.path.join(DEPLOY_DIR, rel))
        for root, dirs, files in os.walk(DEPLOY_DIR):      # chown -R + chmod 755 over the whole tree (also normalizes drift)
            os.chown(root, 0, 0)
            for name in dirs + files:
                path = os.path.join(root, name)
                os.chown(path, 0, 0)
                os.chmod(path, 0o755)
    except OSError as exc:
        add(f"ERROR: daemon deploy failed: {exc}")
        return False
    for rel in DAEMON_DEPLOY_FILES:
        st = os.stat(os.path.join(DEPLOY_DIR, rel))
        if st.st_uid != 0 or (st.st_mode & 0o022):
            add(f"ERROR: deployed {rel} is not root-owned and/or user-writable — fix manually before restarting")
            return False
    add(f"deployed {len(DAEMON_DEPLOY_FILES)} daemon file(s) -> {DEPLOY_DIR}/ (root-owned 755)")
    return True


def op_update():
    """Update the daemon per docs/UPDATING.md: `git pull --ff-only` in /opt/veloscroll (as the checkout owner);
    re-deploy any changed files to DEPLOY_DIR before anything else (self-healing pre-v1.4 installs that have no
    deployed copy at all); if the release changed veloscroll.service, deploy it + daemon-reload; restart only when
    veloscroll.py or the unit changed and tail the journal; schedule a helper self-restart ~2 s after replying
    whenever deployed files changed (drops this process's cached veloscroll/veloscroll_preset modules).

    Returns the full transcript on success (same marker lines as before: `previous commit:` +
    `result:`); raises HelperFailed with the transcript on failure so the GUI log shows either way."""
    t = []

    def add(line=""):
        t.append(line)

    def finish(ok, result=None):
        if result is not None:
            add(f"previous commit: {prev_head}")
            add(f"result: {result}")
        block = "\n".join(t) + "\n"
        if ok:
            return block
        raise HelperFailed(block)

    # -- preflight ---------------------------------------------------------------
    try:
        p = _git(["rev-parse", "HEAD"], timeout=10)
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        add(f"ERROR: could not run git in {DAEMON_DIR}: {exc}")
        finish(False)
    if p.returncode != 0:
        add(f"ERROR: {DAEMON_DIR} is not a usable git checkout: {(p.stderr or '').strip()}")
        finish(False)
    prev_head = (p.stdout or "").strip()

    dirty = _git(["status", "--porcelain"], timeout=10).stdout.strip().splitlines()
    if dirty:
        add(f"warning: working tree is dirty ({len(dirty)} uncommitted change(s)) — "
            "the pull may fail or keep them")

    # -- pull ---------------------------------------------------------------------
    add(f"$ git -C {DAEMON_DIR} pull --ff-only")
    try:
        p = _git(["pull", "--ff-only"], timeout=60)
    except subprocess.TimeoutExpired:
        add("ERROR: `git pull` timed out after 60 s — nothing was changed; retry when the network is up")
        finish(False, "update failed (git pull timed out)")
    for line in (p.stdout or "").splitlines():
        add("  " + line)
    if p.returncode != 0:
        err = (p.stderr or p.stdout or "").strip().splitlines()
        add(f"ERROR: git pull failed: {err[0] if err else 'unknown error'}")
        finish(False, f"update failed — git pull was rejected ({(err[0] if err else '').strip()[:120]})")

    new_head = _git(["rev-parse", "HEAD"], timeout=10).stdout.strip()
    if new_head == prev_head or "Already up to date." in (p.stdout or ""):
        # pre-v1.4 installs have no /usr/lib/veloscroll at all — self-heal even on a no-op pull
        if not os.path.isdir(DEPLOY_DIR):
            add(f"$ mkdir -p {DEPLOY_DIR}/helpers && cp the five daemon files -> {DEPLOY_DIR}/   (deployed copy missing)")
            if not _deploy_daemon(add):
                finish(False, "update could not create the deployed daemon copy — run the deploy block in docs/INSTALL.md")
            return finish(True, "already up to date — nothing was pulled; created the missing /usr/lib/veloscroll deploy (pre-v1.4 install)")
        return finish(True, "already up to date — nothing was pulled and the service was left running")

    count = _git(["rev-list", "--count", f"{prev_head}..{new_head}"], timeout=10).stdout.strip()
    add(f"updated {prev_head[:9]} → {new_head[:9]} ({count} new commit(s))")

    changed = set(_git(["diff", "--name-only", prev_head, new_head], timeout=10).stdout.split())
    deploy_changed = [f for f in DAEMON_DEPLOY_FILES if f in changed]
    unit_changed = "veloscroll.service" in changed

    # -- deploy the root-executed copies (before any unit refresh/restart) --------------
    # The running daemon is always the deployed copy: re-deploy when a pulled release touched one of the five
    # files, or when DEPLOY_DIR is missing entirely (pre-v1.4 install -> self-heal). Deploying first guarantees
    # the new ExecStart target exists by the time the service comes up.
    if deploy_changed or not os.path.isdir(DEPLOY_DIR):
        why = f"{len(deploy_changed)} file(s) changed" if deploy_changed else "deployed copy missing (pre-v1.4 install)"
        add(f"$ cp {DAEMON_DIR}/<five files> -> {DEPLOY_DIR}/ && chown -R root:root && chmod 755   ({why})")
        if not _deploy_daemon(add):
            finish(False, "update pulled code but the daemon deploy failed — run the deploy block in docs/INSTALL.md")

    # -- service-file refresh (only when the release changed it) -------------------
    if unit_changed:
        add("$ cp /opt/veloscroll/veloscroll.service /etc/systemd/system/ && systemctl daemon-reload")
        try:
            shutil.copy2(os.path.join(DAEMON_DIR, "veloscroll.service"), f"/etc/systemd/system/{SERVICE}.service")
            p = subprocess.run(["systemctl", "daemon-reload"], capture_output=True, text=True, timeout=20)
        except (OSError, FileNotFoundError, subprocess.TimeoutExpired) as exc:
            add(f"ERROR: service-file refresh failed: {exc}")
            finish(False, "update pulled code but the service-file refresh failed — restart veloscroll manually")
        if p.returncode != 0:
            add(f"ERROR: systemctl daemon-reload failed: {(p.stderr or '').strip()}")
            finish(False, "update pulled code but daemon-reload failed — run it manually")
        add("  service file refreshed + systemd reloaded")

    def schedule_helper_restart():
        # Restart ourselves only after replying: a synchronous `systemctl restart` would kill this process mid-call
        # and swallow the transcript. The detached child survives our death; the timer keeps it clear of the reply.
        # Needed whenever deployed files changed: this long-lived root process caches veloscroll / veloscroll_preset
        # in sys.modules, so validation must drop them to run against the new code.
        threading.Timer(2.0, lambda: subprocess.Popen(["systemctl", "restart", HELPER_SERVICE_NAME],
                                                     start_new_session=True)).start()
        add("helper service restarts itself in ~2 s to load the new daemon code (if it doesn't come back: "
            f"sudo systemctl restart {HELPER_SERVICE_NAME})")

    # -- restart only if veloscroll.py or the unit changed ------------------------------
    if not ("veloscroll.py" in deploy_changed or unit_changed):
        add("note: neither veloscroll.py nor veloscroll.service changed — the running daemon is left untouched")
        if deploy_changed:
            schedule_helper_restart()
        deployed = (f"deployed {' '.join(deploy_changed)} to {DEPLOY_DIR}" if deploy_changed
                    else f"created the missing {DEPLOY_DIR} deploy (pre-v1.4 install)")
        return finish(True, f"updated {prev_head[:9]} → {new_head[:9]}; {deployed} — no daemon restart needed")

    # -- restart + verify -----------------------------------------------------------
    since = datetime.datetime.now().astimezone().isoformat(timespec="seconds")
    add(f"$ systemctl restart {SERVICE}")
    try:
        p = subprocess.run(["systemctl", "restart", SERVICE], capture_output=True, text=True, timeout=20)
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        add(f"ERROR: restart failed: {exc}")
        finish(False, "update pulled code but the service did not restart — start it manually")
    if p.returncode != 0:
        add(f"ERROR: systemctl restart failed: {(p.stderr or '').strip()}")
        finish(False, f"update pulled code but `systemctl restart {SERVICE}` failed — start it manually")

    state = "unknown"
    for _ in range(24):                        # up to ~12 s for the unit to settle after the restart
        time.sleep(0.5)
        q = subprocess.run(["systemctl", "is-active", SERVICE], capture_output=True, text=True, timeout=5)
        state = (q.stdout or "").strip() or "unknown"
        if state == "active":
            break
    add(f"  service state after restart: {state}")
    if state != "active":
        add("ERROR: the service is not active after the update")
        finish(False, f"update pulled code but the service ended in '{state}' — check the journal and roll back if needed")

    # -- journal tail (only entries since the restart) --------------------------------
    try:
        q = subprocess.run(["journalctl", "-u", SERVICE, "--since", since, "--no-pager"],
                           capture_output=True, text=True, timeout=15)
        lines = [l for l in (q.stdout or "").splitlines() if l.strip()]
    except (FileNotFoundError, subprocess.TimeoutExpired):
        lines = []
    add("journal (new entries since restart, last 10):")
    for line in lines[-10:]:
        add("  " + line)

    joined = "\n".join(lines).lower()
    if deploy_changed:
        schedule_helper_restart()
    if "config loaded from" in joined:
        return finish(True, f"updated {prev_head[:9]} → {new_head[:9]} and restarted — the daemon logged a fresh config load")
    bad = next((m for m in ("config error:", "could not create virtual mouse", "traceback (most recent call last)")
                if m in joined), None)
    if bad:
        finish(False, f"update pulled code but the service logged a startup error ({bad}) — roll back if it misbehaves")
    return finish(True, f"updated {prev_head[:9]} → {new_head[:9]} and restarted "
                        "(LOG_LEVEL hides the config-loaded line; no startup errors in the journal)")


def op_update_gui():
    """Update the GUI: `git pull --ff-only` in /opt/veloscroll-gui; redeploy the helper (and restart its
    service) when a release changes it, and reinstall the polkit policy to POLICY_FILE_PATH when one — so a
    new auth action (e.g. org.veloscroll.gui.tick) is live without any manual step.

    The GUI is not a service — there is nothing else to restart here; the GUI's update dialog closes & reopens
    the app itself once this succeeds. Same transcript/marker contract as op_update()."""
    t = []

    def add(line=""):
        t.append(line)

    def finish(ok, result=None):
        if result is not None:
            add(f"previous commit: {prev_head}")
            add(f"result: {result}")
        block = "\n".join(t) + "\n"
        if ok:
            return block
        raise HelperFailed(block)

    # -- preflight ---------------------------------------------------------------
    try:
        p = _git(["rev-parse", "HEAD"], repo_dir=GUI_DIR, timeout=10)
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        add(f"ERROR: could not run git in {GUI_DIR}: {exc}")
        finish(False)
    if p.returncode != 0:
        add(f"ERROR: {GUI_DIR} is not a usable git checkout: {(p.stderr or '').strip()}")
        finish(False)
    prev_head = (p.stdout or "").strip()

    dirty = _git(["status", "--porcelain"], repo_dir=GUI_DIR, timeout=10).stdout.strip().splitlines()
    if dirty:
        add(f"warning: working tree is dirty ({len(dirty)} uncommitted change(s)) — "
            "the pull may fail or keep them")

    # -- pull ---------------------------------------------------------------------
    add(f"$ git -C {GUI_DIR} pull --ff-only")
    try:
        p = _git(["pull", "--ff-only"], repo_dir=GUI_DIR, timeout=60)
    except subprocess.TimeoutExpired:
        add("ERROR: `git pull` timed out after 60 s — nothing was changed; retry when the network is up")
        finish(False, "update failed (git pull timed out)")
    for line in (p.stdout or "").splitlines():
        add("  " + line)
    if p.returncode != 0:
        err = (p.stderr or p.stdout or "").strip().splitlines()
        add(f"ERROR: git pull failed: {err[0] if err else 'unknown error'}")
        finish(False, f"update failed — git pull was rejected ({(err[0] if err else '').strip()[:120]})")

    new_head = _git(["rev-parse", "HEAD"], repo_dir=GUI_DIR, timeout=10).stdout.strip()
    if new_head == prev_head or "Already up to date." in (p.stdout or ""):
        return finish(True, "already up to date — nothing was pulled and the running GUI is unaffected")

    count = _git(["rev-list", "--count", f"{prev_head}..{new_head}"], repo_dir=GUI_DIR, timeout=10).stdout.strip()
    add(f"updated {prev_head[:9]} → {new_head[:9]} ({count} new commit(s))")

    helper_changed = _git(["diff", "--name-only", prev_head, new_head, "--", "helpers/helper_service.py"],
                          repo_dir=GUI_DIR, timeout=10).stdout.strip()
    if helper_changed:
        add(f"$ deploy {REPO_HELPER_PATH} -> {HELPER_DEPLOY_PATH} && systemctl restart {HELPER_SERVICE_NAME}")
        if not _deploy_helper(add):
            finish(False, "update pulled code but the helper redeploy failed — run README step 7's deploy block")
        # Restart ourselves only after replying: a synchronous `systemctl restart` would kill this process mid-call
        # and swallow the transcript. The detached child survives our death; the timer keeps it clear of the reply.
        threading.Timer(1.5, lambda: subprocess.Popen(["systemctl", "restart", HELPER_SERVICE_NAME],
                                                     start_new_session=True)).start()
        add("helper service restarts itself in ~2 s to load the new code (if it doesn't come back: "
            f"sudo systemctl restart {HELPER_SERVICE_NAME})")
    else:
        add("note: helper unchanged — its running service is left untouched; the GUI instance will now close & "
            "reopen itself with the new code")

    policy_changed = _git(["diff", "--name-only", prev_head, new_head, "--", "org.veloscroll.gui.policy"],
                          repo_dir=GUI_DIR, timeout=10).stdout.strip()
    if policy_changed:
        add(f"$ cp {POLICY_REPO_PATH} -> {POLICY_FILE_PATH}")
        try:
            shutil.copy2(POLICY_REPO_PATH, POLICY_FILE_PATH)
            os.chown(POLICY_FILE_PATH, 0, 0)
            os.chmod(POLICY_FILE_PATH, 0o644)
        except OSError as exc:
            add(f"ERROR: policy deploy failed: {exc}")
            finish(False, "update pulled code but the policy redeploy failed — reinstall org.veloscroll.gui.policy manually")
        add("policy reinstalled (polkitd watches /usr/share/polkit-1/actions/ — no reload needed)")
    return finish(True, f"updated {prev_head[:9]} → {new_head[:9]}; closing & reopening the GUI to run the new version")


# --- capture streams ------------------------------------------------------------------

class CaptureStream:
    """One EV_KEY press-edge stream over a set of input nodes (read-only by design).

    Runs its own select-loop thread; emits PressEvent/CaptureStopped through the service (marshalled
    onto the GLib loop). stop() is idempotent and guarantees exactly one CaptureStopped."""

    def __init__(self, stream_id, paths, max_s, sender):
        self.stream_id = stream_id
        self.paths = [str(p) for p in paths]
        # min(requested, cap): the GUI passes its own window; the cap bounds a leaked stream
        self.max_s = int(max(1, min(int(max_s), CAPTURE_CAP_S)))
        self.sender = str(sender)
        self._stop_event = threading.Event()
        self._stop_lock = threading.Lock()
        self._stopped = False
        self._thread = threading.Thread(target=self._run, daemon=True)

    def start(self):
        self._thread.start()

    def stop(self, reason):
        # may race (StopCapture worker / disconnect watch / the stream's own thread) — exactly one
        # CaptureStopped must be emitted
        with self._stop_lock:
            if self._stopped:
                return
            self._stopped = True
        self._stop_event.set()
        _service.emit_capture_stopped(self.stream_id, reason)

    def _run(self):
        import select
        import struct

        try:
            from evdev import ecodes as e      # constants only (EV_KEY) — no device open, so no writable fd
        except ImportError:
            self.stop("cannot import evdev (is python3-evdev installed?)")
            return

        fds = {}
        errors = []
        for p in self.paths:
            try:
                fd = os.open(p, os.O_RDONLY | os.O_NONBLOCK)   # strictly read-only — never O_RDWR
            except OSError as exc:
                errors.append(f"{p}: {exc.strerror or exc}")
                continue
            fds[fd] = p
        if not fds:
            self.stop("could not open any input node: " + "; ".join(errors[:3]))
            return

        deadline = time.monotonic() + self.max_s
        size = struct.calcsize("llHHi")                        # struct input_event (stable kernel ABI, LP64)
        try:
            while fds and not self._stop_event.is_set():
                timeout = min(deadline - time.monotonic(), 0.2)
                if timeout <= 0:
                    break                                      # --max expiry → CaptureStopped; the GUI re-arms explicitly
                ready, _w, _x = select.select(list(fds), [], [], max(timeout, 0.01))
                for fd in ready:
                    while True:
                        try:
                            raw = os.read(fd, 65536)
                        except BlockingIOError:
                            break                              # EAGAIN on the O_NONBLOCK fd — no more data right now, NOT a vanished node
                        except OSError:
                            del fds[fd]                        # real error (EIO/ENODEV): node vanished mid-stream (unplug / daemon grabbed it)
                            break
                        if not raw:
                            del fds[fd]                        # EOF: the device was unregistered
                            break
                        for off in range(0, len(raw) - size + 1, size):
                            _sec, _usec, ev_type, code, value = struct.unpack_from("llHHi", raw, off)
                            if ev_type == e.EV_KEY and value == 1:
                                _service.emit_press(self.stream_id, int(code))   # one event per press edge
        finally:
            for fd in list(fds):
                try:
                    os.close(fd)
                except OSError:
                    pass
        if self._stop_event.is_set():
            return                                             # stop() already emitted CaptureStopped with its own reason
        if not fds:
            self.stop("all input nodes vanished mid-capture")
        else:
            self.stop(f"max time reached ({self.max_s} s)")


# --- the exported object -----------------------------------------------------------------

class Helper(dbus.service.Object):
    def __init__(self, bus_name, conn):
        super().__init__(bus_name, OBJECT_PATH)
        self._conn = conn
        self._streams = {}      # stream_id -> CaptureStream
        self._lock = threading.Lock()
        self._next_stream = itertools.count(1)

    # -- signal emission (main-loop thread only; callers marshal via GLib.idle_add) ----

    def _send_signal(self, name, signature, *args):
        msg = dbus.lowlevel.SignalMessage(OBJECT_PATH, HELPER_IFACE, name)
        msg.append(signature=signature, *args)
        self._conn.send_message(msg)

    def emit_press(self, stream_id, code):
        self._send_signal("PressEvent", "ii", int(stream_id), int(code))

    def emit_capture_stopped(self, stream_id, reason):
        with self._lock:
            stream = self._streams.pop(int(stream_id), None)
        if stream is not None:
            print(f"capture stream {stream_id} ended ({reason}); caller was {stream.sender}", flush=True)
        self._send_signal("CaptureStopped", "is", int(stream_id), str(reason))

    # -- dispatch ---------------------------------------------------------------------

    def _dispatch(self, sender, action_id, work, reply_ok, reply_err, action_resolver=None):
        """Run `work` in a worker thread behind the polkit gate (action_id=None skips the gate).

        When `action_resolver` is given it runs first — still in this worker thread, before authorize() — and
        returns the action_id to gate with; it must stay read-only (the auth decision precedes any mutation)
        and may raise a call from its default tier up to an auth_admin one (ApplyConfig / Preset -> tick when
        they would actually change TICK). The gate blocks on the native auth prompt for auth_admin actions;
        replies are marshalled back onto the GLib loop. Any exception becomes the matching D-Bus error
        (NotAuthorizedError / HelperFailed via _dbus_error_name, anything else as a generic Python error)."""
        def run():
            try:
                if action_id is not None:
                    effective = action_resolver() if action_resolver is not None else action_id
                    authorize(sender, effective)
                values = work()
            except Exception as exc:
                # bind by value: `exc` is deleted when the except block ends (and this lambda runs later, on the GLib loop)
                GLib.idle_add(lambda e=exc: reply_err(e))
            else:
                GLib.idle_add(lambda v=values: reply_ok(*v))
        threading.Thread(target=run, daemon=True).start()

    # -- disconnect watch ---------------------------------------------------------------

    def on_name_owner_changed(self, name, old_owner, new_owner):
        """Unique-name teardown: a stream's caller vanished (GUI killed / crashed) → release its fds."""
        if not str(name).startswith(":") or str(new_owner) != "":
            return
        with self._lock:
            victims = [s for s in self._streams.values() if s.sender == str(name)]
        for stream in victims:
            stream.stop("caller disconnected")

    # -- methods ------------------------------------------------------------------------

    @dbus.service.method(HELPER_IFACE, in_signature="", out_signature="s",
                         async_callbacks=("reply_ok", "reply_err"), sender_keyword="sender")
    def Ping(self, reply_ok, reply_err, sender):
        self._dispatch(sender, ACTION_READ, lambda: ("pong",), reply_ok, reply_err)

    @dbus.service.method(HELPER_IFACE, in_signature="s", out_signature="bs",
                         async_callbacks=("reply_ok", "reply_err"), sender_keyword="sender")
    def ApplyConfig(self, text, reply_ok, reply_err, sender):
        self._dispatch(sender, ACTION_CONFIG, lambda: apply_config_text(str(text)), reply_ok, reply_err,
                       action_resolver=lambda: _apply_config_action(str(text)))

    @dbus.service.method(HELPER_IFACE, in_signature="i", out_signature="s",
                         async_callbacks=("reply_ok", "reply_err"), sender_keyword="sender")
    def Logs(self, n, reply_ok, reply_err, sender):
        self._dispatch(sender, ACTION_READ, lambda: (op_logs(int(n)),), reply_ok, reply_err)

    @dbus.service.method(HELPER_IFACE, in_signature="", out_signature="s",
                         async_callbacks=("reply_ok", "reply_err"), sender_keyword="sender")
    def Devices(self, reply_ok, reply_err, sender):
        self._dispatch(sender, ACTION_READ, lambda: (op_devices(),), reply_ok, reply_err)

    @dbus.service.method(HELPER_IFACE, in_signature="asu", out_signature="u",
                         async_callbacks=("reply_ok", "reply_err"), sender_keyword="sender")
    def StartCapture(self, paths, max_s, reply_ok, reply_err, sender):
        def work():
            stream = CaptureStream(next(self._next_stream), list(paths), int(max_s), str(sender))
            with self._lock:
                self._streams[stream.stream_id] = stream
            stream.start()
            return (stream.stream_id,)
        self._dispatch(sender, ACTION_CAPTURE, work, reply_ok, reply_err)

    @dbus.service.method(HELPER_IFACE, in_signature="", out_signature="s",
                         async_callbacks=("reply_ok", "reply_err"))
    def StopCapture(self, reply_ok, reply_err):
        # deliberately ungated: it only stops capture streams this service holds (nuisance-level at worst)
        def work():
            with self._lock:
                victims = list(self._streams.values())
            for stream in victims:
                stream.stop("stopped by caller")
            return (f"stopped {len(victims)} capture stream(s)",)
        self._dispatch(None, None, work, reply_ok, reply_err)

    @dbus.service.method(HELPER_IFACE, in_signature="s", out_signature="s",
                         async_callbacks=("reply_ok", "reply_err"), sender_keyword="sender")
    def Service(self, action, reply_ok, reply_err, sender):
        self._dispatch(sender, ACTION_SERVICE, lambda: (op_service(str(action)),), reply_ok, reply_err)

    @dbus.service.method(HELPER_IFACE, in_signature="as", out_signature="s",
                         async_callbacks=("reply_ok", "reply_err"), sender_keyword="sender")
    def Preset(self, ops, reply_ok, reply_err, sender):
        self._dispatch(sender, ACTION_CONFIG, lambda: (op_preset(list(ops)),), reply_ok, reply_err,
                       action_resolver=lambda: _preset_action(list(ops)))

    @dbus.service.method(HELPER_IFACE, in_signature="", out_signature="s",
                         async_callbacks=("reply_ok", "reply_err"), sender_keyword="sender")
    def DaemonReload(self, reply_ok, reply_err, sender):
        self._dispatch(sender, ACTION_SERVICE, lambda: (op_daemon_reload(),), reply_ok, reply_err)

    @dbus.service.method(HELPER_IFACE, in_signature="", out_signature="s",
                         async_callbacks=("reply_ok", "reply_err"), sender_keyword="sender")
    def Update(self, reply_ok, reply_err, sender):
        self._dispatch(sender, ACTION_UPDATE, lambda: (op_update(),), reply_ok, reply_err)

    @dbus.service.method(HELPER_IFACE, in_signature="", out_signature="s",
                         async_callbacks=("reply_ok", "reply_err"), sender_keyword="sender")
    def UpdateGui(self, reply_ok, reply_err, sender):
        self._dispatch(sender, ACTION_UPDATE, lambda: (op_update_gui(),), reply_ok, reply_err)


_service = None   # module-level handle so CaptureStream can emit signals without a circular import at class level


def main():
    global _service
    if os.geteuid() != 0:
        print("helper_service.py: must run as root; refusing to act unprivileged", file=sys.stderr)
        sys.exit(2)
    conn = dbus.bus.BusConnection(_system_bus_address())
    # do_not_queue defaults to False: if a dying instance still holds the name, queue and take over —
    # systemd's Restart=on-failure then never races a stale owner.
    bus_name = dbus.service.BusName(BUS_NAME, conn)
    _service = Helper(bus_name, conn)
    conn.add_signal_receiver(_service.on_name_owner_changed, signal_name="NameOwnerChanged",
                             dbus_interface="org.freedesktop.DBus", bus_name="org.freedesktop.DBus")
    print(f"veloscroll-gui helper service ready on {BUS_NAME} (pid {os.getpid()})", flush=True)
    GLib.MainLoop().run()


if __name__ == "__main__":
    main()
