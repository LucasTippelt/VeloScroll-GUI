"""Effective-value resolution for the Status table (read-only).

Source priority per plan: state-file `values` (the live daemon's snapshot) first,
else an in-process import of the daemon module (`load_config()` over `_DEFAULTS`).
A missing/unreadable config or a failed import degrades to "daemon source
unavailable" — never a crash. Importing veloscroll is side-effect-free for a user
process (threads only start under __main__; no state writes without main()).

Each row carries two source dimensions: where the value came from (config.ini vs
built-in default) and which dataset produced it (state file vs computed).
"""
import configparser
import os
import sys

from . import CONFIG_PATH, DAEMON_DIR

# Section order mirrors config.ini.example; together these cover all 30 DAEMON_KEYS.
SECTIONS = (
    ("feel", ("DPI", "DEADZONE_IN", "FULL_IN", "MAX_RATE", "MIN_RATE",
              "CURVE", "OUT_SMOOTH", "INVERT", "INVERT_H", "TICK")),
    ("horizontal overrides", ("DEADZONE_IN_H", "FULL_IN_H", "MIN_RATE_H",
                              "MAX_RATE_H", "CURVE_H", "OUT_SMOOTH_H")),
    ("device & buttons", ("NAME_MATCH", "DEVICE_MATCH", "SCROLL_BTN",
                          "DISABLED_BTN", "SCROLL_BTN_H")),
    ("scroll mode", ("TOGGLE_SCROLL", "TOGGLE_SCROLL_H", "VERTICAL_H", "LEFT_CLICK_RESET")),
    ("logging", ("LOG_LEVEL", "LATENCY_LOG")),
    ("misc", ("MAX_BACKUPS", "STATE_FILE", "DISTANCE_FILE")),
)

# GUI-owned keys: the GUI persists them in [veloscroll] of config.ini, but they are not daemon keys —
# the daemon logs an unknown-key warning and ignores them (priv.py's apply-config does too). Their
# effective value is whatever config.ini says; no state-file/computed dataset ever carries them.
GUI_KEYS = ("STARTUP_TAB",)

ALL_KEYS = tuple(k for _, keys in SECTIONS for k in keys) + GUI_KEYS

DS_STATE = "state file"
DS_COMPUTED = "computed (in-process)"
DS_UNAVAILABLE = "daemon source unavailable"

_mod_cache = None
_mod_error = ""      # why the import failed (e.g. "ModuleNotFoundError: No module named 'evdev'") — surfaced in the GUI


def _daemon_module():
    """Import the daemon module once; None when /opt/veloscroll is unusable."""
    global _mod_cache, _mod_error
    if _mod_cache is not None:
        return _mod_cache
    try:
        if DAEMON_DIR not in sys.path:
            sys.path.insert(0, DAEMON_DIR)
        import veloscroll  # noqa: PLC0415 — deliberate runtime import of the daemon source
        _mod_cache = veloscroll
        _mod_error = ""
    except Exception as exc:
        _mod_error = f"{exc.__class__.__name__}: {exc}"   # e.g. a venv rebuilt without --system-site-packages hides evdev
        return None
    return _mod_cache


def daemon_module_error():
    """The last import failure of the daemon module ("" when it imports fine)."""
    return _mod_error


def config_file_keys():
    """Keys present with a non-empty value in [veloscroll] of CONFIG_PATH.

    Mirrors the daemon's own rule (non-empty wins, empty falls back to default).
    Returns None when the file is unreadable/malformed so callers can degrade."""
    try:
        cp = configparser.ConfigParser(interpolation=None)
        cp.optionxform = str          # keys are exact Python names
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            cp.read_string(f.read())
    except (OSError, configparser.Error):
        return None
    if not cp.has_section("veloscroll"):
        return set()
    return {k for k, raw in cp["veloscroll"].items() if raw.strip()}


def gui_key_values():
    """{key: raw} for the GUI-owned keys in [veloscroll] of CONFIG_PATH; None when unreadable/malformed.

    The daemon never resolves these (unknown to it), so their effective value is read straight from
    the file instead of going through resolve()."""
    try:
        cp = configparser.ConfigParser(interpolation=None)
        cp.optionxform = str          # keys are exact Python names
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            cp.read_string(f.read())
    except (OSError, configparser.Error):
        return None
    if not cp.has_section("veloscroll"):
        return {}
    return {k: v.strip() for k, v in cp["veloscroll"].items() if k in GUI_KEYS}


def resolve(state):
    """Resolve effective values.

    state: parsed state-file dict or None. Returns (values|None, dataset_label, errors)."""
    errors = []
    if isinstance(state, dict) and isinstance(state.get("values"), dict) and state["values"]:
        return dict(state["values"]), DS_STATE, errors
    if isinstance(state, dict):
        errors.append("state file present but its 'values' section is missing or empty")
    mod = _daemon_module()
    if mod is not None:
        try:
            values = mod.load_config()   # _DEFAULTS overlaid with non-empty config.ini values; missing file -> pure defaults
            return values, DS_COMPUTED, errors
        except Exception as exc:         # ConfigError (bad syntax) or OSError (unreadable)
            errors.append(f"config load failed: {exc}")
    if not errors:
        reason = f" ({_mod_error})" if _mod_error else ""
        errors.append(f"could not import the daemon module from {DAEMON_DIR}{reason}")
    return None, DS_UNAVAILABLE, errors


def format_value(key, v):
    """Human-readable cell text for a parsed Python value."""
    if v is None:
        return "none"
    if isinstance(v, (list, tuple)) and len(v) == 2 and all(isinstance(x, int) for x in v):
        a, b = v   # DEVICE_MATCH pair: state file stores [vendor, product], computed returns (vendor, product)
        return f"0x{a:x}, 0x{b:x}"
    if isinstance(v, bool):
        return "True" if v else "False"
    return str(v)


def build_rows(state):
    """Rows for the Status table.

    Returns (rows, dataset_label, errors). rows is a list of dicts: section rows
    {"section": name} and key rows {"key", "value_text", "source_text"}."""
    values, dataset, errors = resolve(state)
    file_keys = config_file_keys()
    defaults = None
    mod = _daemon_module()
    if mod is not None:
        try:
            defaults = dict(mod._DEFAULTS)   # built-in code defaults for the origin fallback comparison
        except Exception:
            defaults = None

    rows = []
    for name, keys in SECTIONS:
        rows.append({"section": name})
        for key in keys:
            if values is None:
                value_text, source_text = "—", DS_UNAVAILABLE
            else:
                v = values.get(key)
                if file_keys is not None and key in file_keys:
                    origin = "config.ini"
                elif defaults is not None and v != defaults.get(key):
                    origin = "config.ini"     # effective differs from built-in default -> came from the file (fallback when config unreadable)
                else:
                    origin = "default"
                value_text = format_value(key, v)
                source_text = f"{origin} · {dataset}"
            rows.append({"key": key, "value_text": value_text, "source_text": source_text})

    # GUI-owned keys resolve from the file itself — an absent key means the built-in default applies
    gui_raws = gui_key_values()
    for key in GUI_KEYS:
        if gui_raws is None:
            rows.append({"key": key, "value_text": "—", "source_text": DS_UNAVAILABLE})
        else:
            raw = gui_raws.get(key, "")
            rows.append({"key": key, "value_text": raw or "(default)", "source_text": "config.ini · gui"})
    return rows, dataset, errors
