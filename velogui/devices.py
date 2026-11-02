"""Device picker: choose the daemon's target mouse, write DEVICE_MATCH into the edit buffer.

Enumeration is in-process first (`evdev.list_devices()` + open each node — works for a user in the
input group); any unreadable node (permission denied / vanished) triggers the root fallback
`priv.py devices`, whose JSON list then stands in for it. The virtual `veloscroll-mouse` output
node appears in the list, clearly labeled, but cannot be selected — selecting a real device writes
`DEVICE_MATCH = 0x46d, 0x4093` (hex pair) into the Config tab's edit buffer; Apply makes it stick.
Devices without a USB ID (vendor/product both zero) are pinned by node path instead of a pair.
One physical mouse usually exposes several event nodes that share its USB pair — they collapse into
a single dropdown entry annotated "(N nodes)", since the daemon grabs them all by that pair anyway.

The grabbed device from the state file is highlighted in green on every refresh; NAME_MATCH stays
free text in the config table, per plan.
"""
import json

from PySide6.QtCore import Qt
from PySide6.QtGui import QBrush, QColor
from PySide6.QtWidgets import (QComboBox, QGroupBox, QHBoxLayout, QLabel, QPushButton,
                               QVBoxLayout)

from .proc import PrivError, PrivJob
from .values import _daemon_module

UNSET_TEXT = "(fall back to NAME_MATCH — none)"
GRABBED_COLOR = QColor("#16a34a")


def virtual_name():
    """The daemon's virtual output node name (veloscroll.VIRTUAL_NAME, "veloscroll-mouse")."""
    mod = _daemon_module()
    return getattr(mod, "VIRTUAL_NAME", None) or "veloscroll-mouse"


def enumerate_local():
    """Open every /dev/input/event* in-process. Returns (devices, complete).

    devices: [{"path","name","vendor","product"}] with int vendor/product;
    complete: False when evdev is missing or any node could not be opened — the caller then runs
    the root fallback for a full picture."""
    try:
        import evdev
    except ImportError:
        return [], False
    found, complete = [], True
    for path in evdev.list_devices():
        try:
            d = evdev.InputDevice(path)
        except OSError:
            complete = False                 # permission denied (not in input group) or node vanished mid-scan
            continue
        info = d.info
        found.append({"path": path, "name": d.name, "vendor": info.vendor, "product": info.product})
    return found, complete


def group_devices(devices):
    """Collapse the several /dev/input/event* nodes of one physical device into a single entry.

    Nodes sharing a non-zero USB pair are one device (a mouse usually exposes pointer + keyboard
    parts); no-USB-id nodes stay separate — their path is all we have to pin them by. The virtual
    output node keeps its own entry even though it clones its mouse's id: it must stay visible,
    labeled and unselectable. Each entry gains "paths" (list) and "node_count"."""
    vname = virtual_name()
    groups, order = {}, []
    for dev in devices:
        if dev["name"] == vname or not (dev["vendor"] or dev["product"]):
            key = ("path", dev["path"])
        else:
            key = ("pair", dev["vendor"], dev["product"])
        g = groups.get(key)
        if g is None:
            g = dict(dev)
            g["paths"] = []
            groups[key] = g
            order.append(g)
        g["paths"].append(dev["path"])
    for g in order:
        g["node_count"] = len(g["paths"])
    return sorted(order, key=lambda d: (d["name"].lower(), min(d["paths"])))


def match_raw(dev):
    """The DEVICE_MATCH raw text for a device: the hex pair when it has one, else its node path."""
    if dev["vendor"] or dev["product"]:
        return f"0x{dev['vendor']:x}, 0x{dev['product']:x}"
    return (dev.get("paths") or [dev["path"]])[0]


def display_text(dev):
    usb = (f"0x{dev['vendor']:x}, 0x{dev['product']:x}" if (dev["vendor"] or dev["product"])
           else "no USB id — matched by path")
    tag = "   [virtual veloscroll output]" if dev["name"] == virtual_name() else ""
    nodes = f" ({dev.get('node_count', 1)} nodes)" if dev.get("node_count", 1) > 1 else ""
    return f"{dev['name']} ({usb}){nodes}{tag}"


class DevicePicker(QGroupBox):
    """Dropdown over the input devices; writes DEVICE_MATCH into the shared edit buffer.

    `model` is read for the current raw (refresh sync); `on_set(key, raw)` is ConfigTab.set_key_raw —
    it applies the value to the buffer AND re-runs validation/dirty-state on that page."""

    def __init__(self, model, watcher, on_set, parent=None):
        super().__init__("Device picker", parent)
        self.model = model
        self.watcher = watcher
        self.on_set = on_set
        self._scanning = False
        self._partial_local = []                    # in-process results kept for the fallback-failure path
        self._last_valid_index = 0

        lay = QVBoxLayout(self)
        top = QHBoxLayout()
        # no forced color (theme-safe): only the size is set — palette default text color applies
        hint = QLabel("Select the mouse veloscroll should grab; writes DEVICE_MATCH into the edit buffer")
        hint.setWordWrap(True)
        hint.setStyleSheet("font-size: small;")
        self.btn_rescan = QPushButton("Rescan")
        self.btn_rescan.setToolTip("Re-enumerate /dev/input (hot-plugged devices, new virtual node)")
        self.btn_rescan.clicked.connect(self.rescan)
        top.addWidget(hint, 1)
        top.addWidget(self.btn_rescan)
        lay.addLayout(top)

        self.combo = QComboBox()
        self.combo.activated.connect(self._on_selected)      # user clicks only — programmatic sync uses setCurrentIndex
        lay.addWidget(self.combo)

        self.lbl_status = QLabel("not scanned yet")
        self.lbl_status.setWordWrap(True)
        self.lbl_status.setStyleSheet("font-size: small;")    # no forced color (theme-safe)
        self.lbl_status.setTextInteractionFlags(Qt.TextSelectableByMouse)
        lay.addWidget(self.lbl_status)

    # -- scanning -------------------------------------------------------------------

    def rescan(self):
        if self._scanning:
            return
        local, complete = enumerate_local()
        if complete and local:
            self._populate(local, f"{len(local)} device(s) listed in-process")
            return
        # partial reads OR an empty list (list_devices() permission-filters when the user is not in the
        # input group — indistinguishable from "no devices"): ask the privileged helper for the full list
        self._scanning = True
        self._partial_local = list(local)
        why = ("some nodes were unreadable" if local else "no nodes were readable in-process")
        self.lbl_status.setText(f"in-process scan saw {len(local)} device(s) — {why}; "
                                f"enumerating as root (priv.py devices)…")
        job = PrivJob(["devices"], self)
        job.finished_ok.connect(self._on_devices_json)
        job.failed.connect(self._on_devices_fail)
        job.start()

    def _on_devices_fail(self, kind, detail):
        partial = getattr(self, "_partial_local", [])
        self._scanning = False                      # a failed fallback must never wedge [Rescan]
        hint = " — install the scoped sudoers rule (see the Permissions panel)" \
            if kind == PrivError.KIND_RULE_MISSING else ""
        self._populate(partial, f"{len(partial)} device(s) listed in-process; root fallback failed ({detail}){hint}")

    def _on_devices_json(self, out):
        self._scanning = False
        try:
            raw = json.loads(out or "[]")
            devices = [{"path": d["path"], "name": d["name"],
                        "vendor": int(d["vendor"], 16), "product": int(d["product"], 16)} for d in raw]
        except (ValueError, KeyError) as exc:
            self._populate([], f"root fallback returned unparseable JSON ({exc})")
            return
        if not devices:
            self._populate([], "no readable input devices found even as root")
            return
        self._populate(devices, f"{len(devices)} device(s) listed (in-process + root fallback)")

    def _sync_index(self):
        """Point the combo at whatever DEVICE_MATCH currently holds in the edit buffer.

        Several nodes can share one USB pair (two identical mice, or the virtual node cloning its
        mouse's id) — when the current item already writes this value, keep it: picking a specific
        twin must not be snapped to the alphabetically-first twin on the next refresh."""
        vname = virtual_name()
        raw = (self.model.raws.get("DEVICE_MATCH") or "").strip()
        cur = self.combo.currentIndex()
        if raw and raw.lower() != "none":
            dcur = self.combo.itemData(cur) if 0 < cur < self.combo.count() else None
            if isinstance(dcur, dict) and dcur["match_raw"] == raw:
                return cur
            for i in range(1, self.combo.count()):
                d = self.combo.itemData(i)
                # the virtual node clones its mouse's id — never let it stand in for a real device;
                # if only it matches (mouse unplugged), report "not in list" instead of selecting it
                if isinstance(d, dict) and d["match_raw"] == raw and d["name"] != vname:
                    return i
        return 0

    def _populate(self, devices, status):
        vname = virtual_name()
        self.combo.blockSignals(True)
        self.combo.clear()
        self.combo.addItem(UNSET_TEXT, None)             # index 0: DEVICE_MATCH = none -> NAME_MATCH fallback
        for dev in group_devices(devices):               # multi-node devices collapse to one entry ("N nodes")
            data = dict(dev)
            data["match_raw"] = match_raw(dev)
            self.combo.addItem(display_text(dev), data)
        # restore by the value written into the buffer, so rescan reordering never loses the selection
        target = self._sync_index()
        if target > 0 and vname == (self.combo.itemData(target) or {}).get("name"):
            target = 0                                   # the buffer's device is virtual: select nothing instead
        self._last_valid_index = target
        self.combo.setCurrentIndex(self._last_valid_index)
        self.combo.blockSignals(False)
        self.lbl_status.setText(status + " — the daemon re-grabs a new DEVICE_MATCH within ~2–4 s of Apply")

    def _is_virtual(self, index):
        d = self.combo.itemData(index)
        return isinstance(d, dict) and d["name"] == virtual_name()

    # -- selection --------------------------------------------------------------------

    def _on_selected(self, index):
        if index == 0:
            self.on_set("DEVICE_MATCH", "none")          # explicit fallback to NAME_MATCH (daemon syntax)
            self.lbl_status.setText("DEVICE_MATCH set to none — the daemon will fall back to NAME_MATCH after Apply")
            return
        data = self.combo.itemData(index)
        if not isinstance(data, dict):
            return
        if data["name"] == virtual_name():               # labeled but never selectable: it is veloscroll's own output
            self.lbl_status.setText(f"'{data['name']}' is veloscroll's own virtual output node — it cannot be its target")
            self.combo.blockSignals(True)
            self.combo.setCurrentIndex(self._last_valid_index)
            self.combo.blockSignals(False)
            return
        raw = data["match_raw"]
        self.on_set("DEVICE_MATCH", raw)
        self._last_valid_index = index
        note = "" if (data["vendor"] or data["product"]) else " (no USB id — pinned by node path)"
        self.lbl_status.setText(f"DEVICE_MATCH set to {raw}{note} — Apply to make it stick")

    # -- live sync ----------------------------------------------------------------------

    def refresh(self):
        """Called on the main window's 2 s cycle: re-sync selection from the edit buffer and mark
        the grabbed device(s) from the state file. Never fights an in-progress rescan."""
        if self._scanning or self.combo.count() <= 1:
            return
        # The combo always mirrors the buffer: a pick updates the buffer synchronously (so a live pick
        # never fights this), while Apply/Cancel/disk-reload changes to the buffer snap the combo back.
        target = self._sync_index()
        if target != self.combo.currentIndex():
            self.combo.blockSignals(True)
            self.combo.setCurrentIndex(target)
            self._last_valid_index = target
            raw = (self.model.raws.get("DEVICE_MATCH") or "").strip()
            if target == 0 and raw and raw.lower() != "none":
                self.lbl_status.setText(f"current DEVICE_MATCH ({raw}) is not in the device list — rescan or edit it manually")
            self.combo.blockSignals(False)

        state = getattr(self.watcher, "payload", None)
        grabbed = []
        if isinstance(state, dict):
            for d in (state.get("devices_grabbed") or []):
                grabbed.append((d.get("vendor"), d.get("product"), d.get("path")))
        for i in range(1, self.combo.count()):
            d = self.combo.itemData(i)
            if not isinstance(d, dict):
                continue
            if d["vendor"] == 0 and d["product"] == 0:
                # every no-USB-id device shares the "pair" 0x0/0x0 — only its path can disambiguate
                hit = any(gv in (None, "0x0") and gp in (None, "0x0") and p in d.get("paths", [d["path"]])
                          for gv, gp, p in grabbed)
            else:
                # the daemon grabs by USB pair, so every node sharing it is a match (incl. its virtual clone)
                hit = any(gv == f"0x{d['vendor']:x}" and gp == f"0x{d['product']:x}" for gv, gp, _ in grabbed)
            self.combo.setItemData(i, QBrush(GRABBED_COLOR) if hit else None, Qt.ForegroundRole)
