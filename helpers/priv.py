#!/usr/bin/env python3
"""Privileged helper for the VeloScroll GUI.

Invoked as: sudo -n /opt/veloscroll-gui/helpers/priv.py <cmd> [args...]
through the scoped NOPASSWD sudoers rule (see README.md, Permissions section).
The allowlist is strict — every subcommand below exists and any write re-validates
with the daemon's own parser as defense-in-depth. This file must never act
unprivileged: it refuses to run when euid != 0.

Subcommands:
  ping                  liveness probe for the permissions panel
  apply-config <tmp>    validate + atomically replace /etc/veloscroll/config.ini
  logs [N]              last N journal lines of the veloscroll service (default 50)
  devices               enumerate every /dev/input/event* node as JSON on stdout (root fallback for the
                        device picker when in-process opens fail, e.g. user not in input group)
  service <stop|start>  stop or start the veloscroll systemd unit (button-capture fallback stops the daemon
                        so a swallowed button code can be read from the physical node)
   preset <ops...>       strict passthrough to veloscroll_preset.py: save/loaddistances/loadfeel/
                         loadkeys/loaddevice/loadscroll/loadall NAME pairs, or list / backups / restore [BACKUP]
   daemon-reload         systemctl daemon-reload after deploying a new unit file
   update                one-click daemon update per docs/UPDATING.md: git pull --ff-only in /opt/veloscroll,
                         service-file refresh + daemon-reload when the release changed veloscroll.service,
                         restart, journal tail; transcript on stdout (stderr on failure)
   update-gui            one-click GUI update: git pull --ff-only in /opt/veloscroll-gui; no service is
                         involved — the running GUI keeps its loaded code until it is restarted; transcript
                         on stdout (stderr on failure)

Exit codes: 0 = ok; 1 = usage error / unknown subcommand / rejected write; 2 = not root.
"""
import configparser
import datetime
import glob
import os
import shutil
import subprocess
import sys
import time

DAEMON_DIR = "/opt/veloscroll"
GUI_DIR = "/opt/veloscroll-gui"
CONFIG_PATH = "/etc/veloscroll/config.ini"
SERVICE = "veloscroll"


def _fail(msg, code):
    print(f"priv.py: {msg}", file=sys.stderr)
    sys.exit(code)


def _daemon_module():
    if DAEMON_DIR not in sys.path:
        sys.path.insert(0, DAEMON_DIR)
    import veloscroll  # noqa: PLC0415 — side-effect-free for a non-daemon process (see module docstring there)
    return veloscroll


def _preset_module():
    if DAEMON_DIR not in sys.path:
        sys.path.insert(0, DAEMON_DIR)
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
    try:
        vs = _daemon_module()
    except Exception as exc:
        return False, f"could not import the daemon module from {DAEMON_DIR}: {exc}"
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


def cmd_apply_config(args):
    try:
        with open(args[0], "r", encoding="utf-8") as f:
            text = f.read()
    except OSError as exc:
        _fail(f"cannot read {args[0]}: {exc}", 1)
    ok, msg = apply_config_text(text)
    if not ok:
        _fail(msg, 1)
    print(msg)


# --- logs ---------------------------------------------------------------------------

def cmd_logs(args):
    n = int(args[0]) if args else 50          # journalctl -n accepts any positive integer; a bad N is our usage error
    try:
        p = subprocess.run(["journalctl", "-u", "veloscroll", "-n", str(n), "--no-pager"],
                           capture_output=True, text=True, timeout=20)
    except (ValueError, FileNotFoundError, subprocess.TimeoutExpired) as exc:
        _fail(f"journalctl failed: {exc}", 1)
    if p.returncode != 0:
        _fail((p.stderr or p.stdout or "journalctl failed").strip(), 1)
    print(p.stdout.rstrip("\n"))


# --- devices ------------------------------------------------------------------------

def cmd_devices(args):
    """JSON list of every /dev/input/event* node readable as root: [{"path","name","vendor","product"}].

    vendor/product are hex strings ("0x46d") — the same form ListDeviceNames.py prints, so the GUI's
    in-process enumeration and this fallback stay directly comparable. Nodes that vanish between
    listing and opening are skipped, exactly like the daemon's find_matches()."""
    import json
    try:
        import evdev
    except ImportError as exc:
        _fail(f"cannot import evdev (is python3-evdev installed?): {exc}", 1)
    out = []
    for path in evdev.list_devices():
        try:
            d = evdev.InputDevice(path)
        except OSError:
            continue
        info = d.info
        out.append({"path": path, "name": d.name,
                    "vendor": f"0x{info.vendor:x}", "product": f"0x{info.product:x}"})
    print(json.dumps(out))


# --- service ------------------------------------------------------------------------

def cmd_service(args):
    """`systemctl stop|start veloscroll`, nothing else — the allowlist is the two actions."""
    action = args[0] if args else ""
    if action not in ("stop", "start"):
        _fail(f"service: unknown action {action!r} — allowed: stop | start", 1)
    try:
        p = subprocess.run(["systemctl", action, SERVICE], capture_output=True, text=True, timeout=20)
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        _fail(f"systemctl {action} failed: {exc}", 1)
    if p.returncode != 0:
        _fail((p.stderr or p.stdout or f"systemctl {action} veloscroll failed").strip(), 1)
    print(f"{SERVICE} {'stopped' if action == 'stop' else 'started'}")


# --- preset -----------------------------------------------------------------------

def cmd_preset(args):
    """Strict passthrough to veloscroll_preset.py — the allowlist mirrors its _parse_ops exactly.

    Flags are rejected outright (no --config: presets always live next to /etc/veloscroll/config.ini),
    every name is fullmatch-validated against the CLI's own NAME_RE / BAK_RE, and arity follows the
    same grammar — so a malformed or traversal attempt fails here before any subprocess runs.
    The CLI re-validates everything as defense-in-depth; its stdout (list/backups tables) passes through."""
    if not args:
        _fail("preset: expected at least one operation (save/loaddistances/loadfeel/loadkeys/loaddevice/"
              "loadscroll/loadall/delete NAME | list | backups | restore [BACKUP])", 1)
    for tok in args:
        if tok.startswith("-"):
            _fail(f"preset: flags are not allowed ({tok!r}) — only bare operations and names", 1)
    try:
        vp = _preset_module()
    except Exception as exc:
        _fail(f"could not import veloscroll_preset from {DAEMON_DIR}: {exc}", 1)
    pair_cmds = tuple(vp.LOAD_GROUPS) + ("save", "loadall", "delete")
    first = args[0]
    if first in ("list", "backups"):
        if len(args) != 1:
            _fail(f"preset: `{first}` takes no arguments — run it alone", 1)
    elif first == "restore":
        if len(args) > 2:
            _fail("preset: `restore` takes at most one argument (a backup name from `backups`)", 1)
        if len(args) == 2 and not vp.BAK_RE.fullmatch(args[1]):
            _fail(f"preset: invalid backup name {args[1]!r} — use a name from `backups`", 1)
    else:
        if first not in pair_cmds:
            _fail(f"preset: unknown command {first!r} — use one of {'/'.join(pair_cmds)} (list/backups/restore run alone)", 1)
        if len(args) % 2 != 0:
            _fail("preset: expected pairs of `COMMAND NAME`, e.g. `loadfeel fast loaddistances quiet`", 1)
        for i in range(0, len(args), 2):
            cmd, name = args[i], args[i + 1]
            if cmd not in pair_cmds:
                _fail(f"preset: unknown command {cmd!r} — use one of {'/'.join(pair_cmds)} (list/backups/restore run alone)", 1)
            if not vp.NAME_RE.fullmatch(name):
                _fail(f"preset: invalid preset name {name!r} — use only letters, digits, '.', '_' or '-'", 1)
    try:
        p = subprocess.run([sys.executable, os.path.join(DAEMON_DIR, "veloscroll_preset.py"), *args],
                           capture_output=True, text=True, timeout=30)
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        _fail(f"veloscroll_preset.py failed: {exc}", 1)
    if p.stdout:
        print(p.stdout.rstrip("\n"))
    if p.returncode != 0:
        _fail((p.stderr or "veloscroll_preset.py failed").strip(), 1)


# --- daemon-reload ------------------------------------------------------------------

def cmd_daemon_reload(args):
    """`systemctl daemon-reload`, nothing else — re-reads unit files after a new veloscroll.service is deployed."""
    if args:
        _fail("daemon-reload takes no arguments", 1)
    try:
        p = subprocess.run(["systemctl", "daemon-reload"], capture_output=True, text=True, timeout=20)
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        _fail(f"systemctl daemon-reload failed: {exc}", 1)
    if p.returncode != 0:
        _fail((p.stderr or "systemctl daemon-reload failed").strip(), 1)
    print("daemon reloaded")


# --- update -------------------------------------------------------------------

def _git(args, repo_dir=DAEMON_DIR, timeout=60):
    return subprocess.run(["git", "-C", repo_dir, *args], capture_output=True, text=True, timeout=timeout)


def cmd_update(args):
    """Update the daemon per docs/UPDATING.md: `git pull --ff-only` in /opt/veloscroll; if the release
    changed veloscroll.service, deploy it + daemon-reload; restart the service and tail the journal.

    The transcript goes to stdout on success and stderr on failure — PrivJob (proc.py) carries a
    command's stdout through on exit 0 and its stderr as the error detail otherwise, so the GUI dialog
    shows the full transcript either way. Two machine-readable marker lines are appended last:
    `previous commit:` (the rollback hash) and `result:` (a one-line summary)."""
    if args:
        _fail("update takes no arguments", 1)
    t = []

    def add(line=""):
        t.append(line)

    def finish(ok, result=None):
        if result is not None:
            add(f"previous commit: {prev_head}")
            add(f"result: {result}")
        block = "\n".join(t) + "\n"
        print(block, file=sys.stdout if ok else sys.stderr, end="")
        sys.exit(0 if ok else 1)

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
        finish(True, "already up to date — nothing was pulled and the service was left running")

    count = _git(["rev-list", "--count", f"{prev_head}..{new_head}"], timeout=10).stdout.strip()
    add(f"updated {prev_head[:9]} → {new_head[:9]} ({count} new commit(s))")

    # -- service-file refresh (only when the release changed it) -------------------
    unit_changed = _git(["diff", "--name-only", prev_head, new_head, "--", "veloscroll.service"],
                        timeout=10).stdout.strip()
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
    if "config loaded from" in joined:
        finish(True, f"updated {prev_head[:9]} → {new_head[:9]} and restarted — the daemon logged a fresh config load")
    bad = next((m for m in ("config error:", "could not create virtual mouse", "traceback (most recent call last)")
                if m in joined), None)
    if bad:
        finish(False, f"update pulled code but the service logged a startup error ({bad}) — roll back if it misbehaves")
    finish(True, f"updated {prev_head[:9]} → {new_head[:9]} and restarted "
                 "(LOG_LEVEL hides the config-loaded line; no startup errors in the journal)")


def cmd_update_gui(args):
    """Update the GUI: `git pull --ff-only` in /opt/veloscroll-gui.

    The GUI is not a service — there is nothing to restart here; the GUI's update dialog closes & reopens
    the app itself once this succeeds. Same transcript/marker contract as update(): stdout on success,
    stderr on failure, `previous commit:` + `result:` lines appended last."""
    if args:
        _fail("update-gui takes no arguments", 1)
    t = []

    def add(line=""):
        t.append(line)

    def finish(ok, result=None):
        if result is not None:
            add(f"previous commit: {prev_head}")
            add(f"result: {result}")
        block = "\n".join(t) + "\n"
        print(block, file=sys.stdout if ok else sys.stderr, end="")
        sys.exit(0 if ok else 1)

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
        finish(True, "already up to date — nothing was pulled and the running GUI is unaffected")

    count = _git(["rev-list", "--count", f"{prev_head}..{new_head}"], repo_dir=GUI_DIR, timeout=10).stdout.strip()
    add(f"updated {prev_head[:9]} → {new_head[:9]} ({count} new commit(s))")
    add("note: the GUI is not a service — the running instance will now close & reopen itself with the "
        "new code")
    finish(True, f"updated {prev_head[:9]} → {new_head[:9]}; closing & reopening the GUI to run the new version")


# name -> (handler, min_arg_count)
COMMANDS = {
    "ping": (lambda a: print("pong"), 0),
    "apply-config": (cmd_apply_config, 1),
    "logs": (cmd_logs, 0),
    "devices": (cmd_devices, 0),
    "service": (cmd_service, 1),
    "preset": (cmd_preset, 1),
    "daemon-reload": (cmd_daemon_reload, 0),
    "update": (cmd_update, 0),
    "update-gui": (cmd_update_gui, 0),
}


def main():
    if os.geteuid() != 0:
        _fail("must run as root (via the scoped sudoers rule); refusing to act unprivileged", 2)
    args = sys.argv[1:]
    cmd = args[0] if args else None
    entry = COMMANDS.get(cmd)
    if entry is None:
        allowed = " | ".join(sorted(COMMANDS))
        _fail(f"unknown subcommand {cmd!r} — allowed: {allowed}", 1)
    handler, min_args = entry
    if len(args) - 1 < min_args:
        _fail(f"{cmd}: expected at least {min_args} argument(s)", 1)
    handler(args[1:])


if __name__ == "__main__":
    main()
