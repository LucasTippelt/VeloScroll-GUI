"""Edit buffer for the Config tab (chunk 2).

Holds the config.ini text in memory plus per-key edit state, and renders a new full
text line-preservingly over the baseline:

- an existing `KEY = ...` line inside [veloscroll] is replaced in place;
- a key present only as a commented-out line (`;KEY = ...`) is uncommented at that
  same position instead of being appended (keeps the file layout and its comment blocks);
- a missing key is appended at the section end, in DAEMON_KEYS order.

Validation mirrors the daemon exactly: per-key parsing goes through
veloscroll._parse_ini_value and cross-field rules through veloscroll.check_config —
the GUI blocks inline what priv.py's apply-config gate would reject anyway.

`raws[key]` is always the exact text that would be written for that key ("" = unset /
no line). Nullable keys use "none" when an existing value is cleared, matching the
daemon's syntax; empty raws only ever mean "key absent from the file".
"""
import configparser
import re

from PySide6.QtCore import QObject, Signal

from . import CONFIG_PATH, DAEMON_DIR
from .values import ALL_KEYS, GUI_KEYS, _daemon_module, daemon_module_error

# key -> widget kind (drives which editor the Config tab builds)
KIND_BOOL = "bool"        # INVERT LATENCY_LOG TOGGLE_SCROLL VERTICAL_H LEFT_CLICK_RESET STATE_FILE DISTANCE_FILE
KIND_TRI = "tri"          # TOGGLE_SCROLL_H / INVERT_H — boolean or none (falls back to vertical)
KIND_INT = "int"          # LOG_LEVEL (0..3), MAX_BACKUPS (>= -1)
KIND_FLOAT = "float"      # DPI DEADZONE_IN FULL_IN MAX_RATE MIN_RATE CURVE OUT_SMOOTH TICK
KIND_NFLOAT = "nfloat"    # the six *_H ramp floats — number or none (falls back to vertical)
KIND_BTN = "btn"          # SCROLL_BTN / DISABLED_BTN / SCROLL_BTN_H — evdev name or decimal, none disables
KIND_NAME = "name"        # NAME_MATCH — free text; empty falls back to the built-in default
KIND_DEVICE = "device"    # DEVICE_MATCH — vendor/product pair, stable path string, or none
KIND_TAB = "tab"          # STARTUP_TAB — GUI-owned: one of the tab labels (the daemon ignores this key)

KEY_KINDS = {
    "DPI": KIND_FLOAT, "DEADZONE_IN": KIND_FLOAT, "FULL_IN": KIND_FLOAT,
    "MAX_RATE": KIND_FLOAT, "MIN_RATE": KIND_FLOAT, "CURVE": KIND_FLOAT,
    "OUT_SMOOTH": KIND_FLOAT, "TICK": KIND_FLOAT,
    "DEADZONE_IN_H": KIND_NFLOAT, "FULL_IN_H": KIND_NFLOAT, "MIN_RATE_H": KIND_NFLOAT,
    "MAX_RATE_H": KIND_NFLOAT, "CURVE_H": KIND_NFLOAT, "OUT_SMOOTH_H": KIND_NFLOAT,
    "INVERT": KIND_BOOL, "INVERT_H": KIND_TRI, "TOGGLE_SCROLL": KIND_BOOL, "VERTICAL_H": KIND_BOOL,
    "LEFT_CLICK_RESET": KIND_BOOL, "TOGGLE_SCROLL_H": KIND_TRI, "LOG_LEVEL": KIND_INT, "LATENCY_LOG": KIND_BOOL,
    "MAX_BACKUPS": KIND_INT, "STATE_FILE": KIND_BOOL, "DISTANCE_FILE": KIND_BOOL,
    "NAME_MATCH": KIND_NAME, "DEVICE_MATCH": KIND_DEVICE,
    "SCROLL_BTN": KIND_BTN, "DISABLED_BTN": KIND_BTN, "SCROLL_BTN_H": KIND_BTN,
    "STARTUP_TAB": KIND_TAB,
}

# keys whose parsed value may be None (unset / feature off)
NULLABLE = {"TOGGLE_SCROLL_H", "INVERT_H", "DEADZONE_IN_H", "FULL_IN_H", "MIN_RATE_H",
            "MAX_RATE_H", "CURVE_H", "OUT_SMOOTH_H", "DISABLED_BTN",
            "SCROLL_BTN_H", "DEVICE_MATCH"}

# keys that shape a ramp (deadzone/full-rate distances, rates, curve, smoothing) — edited on the Ramp
# tab next to their live curves instead of in the Status & Config table
RAMP_KEYS = ("DEADZONE_IN", "FULL_IN", "MIN_RATE", "MAX_RATE", "CURVE", "OUT_SMOOTH",
             "DEADZONE_IN_H", "FULL_IN_H", "MIN_RATE_H", "MAX_RATE_H", "CURVE_H", "OUT_SMOOTH_H")

# the device, DPI & button fields — edited on the Device Picker tab next to the mouse selector and the
# chunk-4 [Capture…] buttons instead of in the Status & Config table (DPI is a property of the picked mouse).
# The three button keys come last, in their display order: SCROLL_BTN / SCROLL_BTN_H / DISABLED_BTN
DEVICE_KEYS = ("NAME_MATCH", "DEVICE_MATCH", "DPI", "SCROLL_BTN", "SCROLL_BTN_H", "DISABLED_BTN")

# the feel & scroll-mode fields — edited on the Scroll/Invert tab instead of in the Status & Config table
SCROLL_INVERT_KEYS = ("INVERT", "INVERT_H", "TICK",
                      "TOGGLE_SCROLL", "TOGGLE_SCROLL_H", "VERTICAL_H", "LEFT_CLICK_RESET")

# unset -> what the runtime uses instead (for the "(falls back to ...)" hints)
FALLBACK = {
    "DEADZONE_IN_H": ("vertical value", "DEADZONE_IN"), "FULL_IN_H": ("vertical value", "FULL_IN"),
    "MIN_RATE_H": ("vertical value", "MIN_RATE"), "MAX_RATE_H": ("vertical value", "MAX_RATE"),
    "CURVE_H": ("vertical value", "CURVE"), "OUT_SMOOTH_H": ("vertical value", "OUT_SMOOTH"),
    "INVERT_H": ("vertical value", "INVERT"),
    "TOGGLE_SCROLL_H": ("vertical value", "TOGGLE_SCROLL"),
    "SCROLL_BTN_H": ("horizontal scrolling disabled", None),
    "DISABLED_BTN": ("no button is swallowed", None),
    "DEVICE_MATCH": ("NAME_MATCH selector", "NAME_MATCH"),
}


def _parse_raws(text):
    """{key: raw} for the non-empty, uncommented [veloscroll] lines; {} when missing/malformed."""
    cp = configparser.ConfigParser(interpolation=None)
    cp.optionxform = str
    try:
        cp.read_string(text)
    except (configparser.Error, TypeError):
        return None
    if not cp.has_section("veloscroll"):
        return {}
    return {k: v.strip() for k, v in cp["veloscroll"].items()}


def _render(baseline, updates):
    """Line-preserving rewrite of `baseline` applying {key: raw_text}; see module docstring."""
    lines = baseline.split("\n") if baseline and baseline.strip() else []
    sec = next((i for i, line in enumerate(lines) if line.strip() == "[veloscroll]"), None)
    end = len(lines)
    if sec is not None:                              # section ends at the next [header] or EOF
        for j in range(sec + 1, len(lines)):
            s = lines[j].strip()
            if s.startswith("[") and s.endswith("]"):
                end = j
                break
    else:                                            # no [veloscroll] section (or empty file): create it at the end
        lines += ([""] if lines else []) + ["[veloscroll]"]
        sec, end = len(lines) - 1, len(lines)

    append_at = end
    while append_at > sec + 1 and lines[append_at - 1].strip() == "":   # skip the trailing blank line(s)
        append_at -= 1
    appends = []
    for key in (k for k in ALL_KEYS if k in updates):                    # deterministic order for appended keys
        val = updates[key]
        pat_active = re.compile(rf"^(\s*){re.escape(key)}\s*=.*$")
        idx = next((j for j in range(sec + 1, end) if pat_active.match(lines[j])), None)
        if idx is not None:
            lines[idx] = f"{pat_active.match(lines[idx]).group(1)}{key} = {val}"
            continue
        pat_cmt = re.compile(rf"^(\s*)[;#]\s*{re.escape(key)}\s*=.*$")   # commented-out line: uncomment in place
        idx2 = next((j for j in range(sec + 1, end) if pat_cmt.match(lines[j])), None)
        if idx2 is not None:
            lines[idx2] = f"{key} = {val}"
            continue
        appends.append(f"{key} = {val}")
    if appends:
        lines[append_at:append_at] = appends
    if lines and lines[-1] != "":
        lines.append("")                                 # keep a trailing newline
    return "\n".join(lines)


class ConfigModel(QObject):
    """In-memory edit buffer over all 30 daemon keys (see module docstring)."""

    changed = Signal()          # any raw/value change — the tab revalidates and recomputes dirty state

    def __init__(self, parent=None):
        super().__init__(parent)
        self._mod = _daemon_module()
        self._defaults = dict(self._mod._DEFAULTS) if self._mod is not None else {}
        self.baseline_text = ""       # last-known-good text (initial load; Cancel target, see ConfigTab)
        self.raws = {k: "" for k in ALL_KEYS}
        self.values = {}              # parsed effective values (defaults overlaid), per-key errors below
        self.field_errors = {}        # key -> message (parse/type failures of the current raws)

    @property
    def available(self):
        """False when /opt/veloscroll cannot be imported — validation is then impossible and Apply must stay blocked."""
        return self._mod is not None

    def load_text(self, text):
        """(Re)initialize the buffer from a full config.ini text (initial load or refresh)."""
        self.baseline_text = text if isinstance(text, str) else ""
        raws = _parse_raws(self.baseline_text)
        if raws is None:                                   # malformed INI syntax in the file itself
            self.field_errors = {"*": f"config.ini has a syntax error — fix it manually (see '{CONFIG_PATH}'); "
                                      "the GUI cannot parse this file"}
            return
        self.raws = {k: raws.get(k, "") for k in ALL_KEYS}
        self._recompute_values()

    def origin(self):
        """Raw values as they appear in the baseline text ("" when absent or commented out)."""
        return _parse_raws(self.baseline_text) or {}

    def set_raw(self, key, raw):
        if self.raws.get(key) == raw:
            return
        self.raws[key] = (raw or "").strip()
        self._recompute_one(key)
        self.changed.emit()

    def _recompute_one(self, key):
        if key in GUI_KEYS:      # GUI-owned (the daemon ignores it) — no daemon parser to run; the raw text is the value
            self.values[key] = self.raws.get(key) or None
            return
        mod = self._mod
        if not mod:
            return
        raw = self.raws.get(key, "")
        err = None
        try:
            # empty line -> unset (nullable keys) or built-in default, as the daemon sees it
            value = mod._parse_ini_value(key, raw) if raw else (None if key in NULLABLE else self._defaults.get(key))
        except Exception as exc:                           # ConfigError carrying the daemon's own message
            m = re.match(r"^[\w]+ in \S+: (.*)$", str(exc))
            err = m.group(1) if m else str(exc)
            value = None
        self.values[key] = value
        if err:
            self.field_errors[key] = err
        elif key in self.field_errors:
            del self.field_errors[key]

    def _recompute_values(self):
        mod = self._mod
        self.field_errors = {}
        if not mod:
            return
        for key in ALL_KEYS:
            self.values[key] = None
            self._recompute_one(key)

    # -- validation -------------------------------------------------------------

    def validate(self):
        """Returns (values|None, field_errors, form_errors).

        values is the full 30-key dict a daemon reload would see; form_errors are the
        cross-field check_config messages (only meaningful once every key parses)."""
        mod = self._mod
        if not mod:
            reason = f" ({daemon_module_error()})" if daemon_module_error() else ""
            hint = (" — the GUI's venv must see system site-packages (evdev); "
                    "check .venv/pyvenv.cfg has include-system-site-packages = true") \
                if "evdev" in reason else ""
            return None, {"*": f"daemon module unavailable — cannot validate{reason}{hint}"}, []
        if "*" in self.field_errors:                       # file-level syntax error from load_text
            return None, dict(self.field_errors), []
        values = dict(self._defaults)                      # an empty raw is skipped by the daemon, so the built-in default applies (incl. NAME_MATCH)
        for key in ALL_KEYS:
            if key in GUI_KEYS:                            # not a daemon key — never part of what a reload would see
                continue
            raw = self.raws.get(key, "")
            if raw:
                values[key] = self.values.get(key)
            elif key in NULLABLE:
                values[key] = None
        form_errors = []
        if not self.field_errors:
            try:
                form_errors = list(mod.check_config(values))
            except Exception as exc:                        # check_config must not crash the GUI on odd input
                form_errors = [f"validation failed internally: {exc}"]
        return values, dict(self.field_errors), form_errors

    # -- rendering / dirty ------------------------------------------------------

    def render_text(self):
        """Full text with every raw differing from its file origin applied over the baseline."""
        origin = _parse_raws(self.baseline_text) or {}
        updates = {k: self.raws[k] for k in ALL_KEYS if self.raws.get(k, "") != origin.get(k, "")}
        if not updates:
            return self.baseline_text
        return _render(self.baseline_text, updates)

    def is_dirty_against(self, disk_text):
        """True when the editor state differs from what is currently on disk."""
        return self.render_text() != (disk_text or "")
