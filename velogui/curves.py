"""Ramp curve previews (chunk 3): vertical and horizontal notches/s vs distance.

Plots the daemon's exact target-rate formula from veloscroll.py's ticker:

    frac   = min(1, (dist - dz) / (full - dz))
    target = MIN_RATE + (MAX_RATE - MIN_RATE) * frac ** CURVE        # 0 inside the deadzone; CURVE=1 is linear

with per-axis effective values — each unset/`none` horizontal key falls back to its vertical
counterpart, exactly like the runtime (`MIN_RATE_H if MIN_RATE_H is not None else MIN_RATE`, ...).
OUT_SMOOTH / OUT_SMOOTH_H are deliberately not part of the curve: they shape how fast the rate
*follows* this target (dynamics), not the target itself.

The two plots keep INDEPENDENT axis scales: each one fits its own ramp (the horizontal overrides
often differ from the vertical by a lot), so neither curve is squashed to match the other.

Clicking or dragging anywhere on a plot moves the measurement marker to that distance and labels
it with the exact rate there ("1.23 in → 87 notches/s"), like the deadzone / full-rate markers;
the mouse scrubs (pan/zoom is disabled) so you can drag it along the curve. While a scroll session
is held, that same marker takes over: it tracks the daemon's live position from the separate
distance file /run/veloscroll/distance (only written when the daemon runs with DISTANCE_FILE = True;
hidden otherwise), which the watcher polls at ~33 Hz — the daemon rewrites it on every ticker tick
while held, so the marker moves smoothly. The line is clamped to the visible x-range: the daemon's
distance keeps growing past FULL_IN while you keep scrolling, so an unclamped marker would fly off
the right edge of the plot (the label still shows the true distance). Mouse scrubbing is suspended
for the duration of the hold; on release the marker returns to where it last was placed by hand
(or hides if there was none). The preview reflects the Config tab's edit buffer (what Apply would
write): edits replot after a ~100 ms debounce, so scrubbing a spin box gives live feedback without
per-keystroke redraw cost.
"""
import os
import time

# PyQt6 is installed system-wide: force pyqtgraph onto PySide6 before it binds to whatever Qt it finds first.
os.environ.setdefault("QT_API", "pyside")   # noqa: E402 — must precede the pyqtgraph import below

from PySide6.QtCore import Qt, QTimer                    # noqa: E402
from PySide6.QtWidgets import QCheckBox, QLabel, QWidget, QVBoxLayout  # noqa: E402

import numpy as np                                      # noqa: E402
import pyqtgraph as pg                                  # noqa: E402

# (deadzone, full-rate distance, min rate, max rate, curve) per axis — the five values the formula uses.
V_KEYS = ("DEADZONE_IN", "FULL_IN", "MIN_RATE", "MAX_RATE", "CURVE")
H_KEYS = ("DEADZONE_IN_H", "FULL_IN_H", "MIN_RATE_H", "MAX_RATE_H", "CURVE_H")

PEN_V = "#2563eb"      # vertical curve: blue
PEN_H = "#9333ea"      # horizontal curve: purple
DZ_COLOR = "#6b7280"   # deadzone marker: gray
FULL_COLOR = "#16a34a"  # full-rate marker: green
MEAS_COLOR = "#ea580c"  # measurement marker: orange — also the live-position indicator while a session is held

DEBOUNCE_MS = 100
N_POINTS = 400


def ramp_rate(dist, dz, full, min_r, max_r, curve):
    """The daemon's exact per-axis target rate (notches/s) at a given distance in inches."""
    if dist <= dz or not (full > dz) or min_r <= 0:
        return 0.0
    frac = min(1.0, (dist - dz) / (full - dz))
    # CURVE=1 is linear between MIN_RATE and MAX_RATE; <1 steep start, >1 gentle start — veloscroll.py's ticker form
    return min_r + (max_r - min_r) * frac ** curve


def axis_params(values):
    """Effective ramp parameters for both axes from a parsed values dict (defaults overlaid).

    Returns ((v_dz, v_full, v_min, v_max, v_curve),
             (h_dz, h_full, h_min, h_max, h_curve),
             {H_KEY: V_KEY} for every horizontal key that fell back to vertical)."""
    v = tuple(float(values[k]) for k in V_KEYS)         # the five vertical keys are non-nullable
    fallbacks = {}
    h = []
    for hk, vk in zip(H_KEYS, V_KEYS):
        hv = values.get(hk)
        if hv is None:                                  # unset/none -> runtime uses the vertical value
            h.append(v[V_KEYS.index(vk)])
            fallbacks[hk] = vk
        else:
            h.append(float(hv))
    return v, tuple(h), fallbacks


def curve_y(x, params):
    """Vectorized ramp over an x array (inches) for one axis's parameter tuple."""
    dz, full, min_r, max_r, curve = params
    y = np.zeros_like(x)
    m = (x > dz) & (full > dz) & (min_r > 0)
    frac = np.clip((x[m] - dz) / (full - dz), 0.0, 1.0)
    # CURVE=1 is linear between MIN_RATE and MAX_RATE; <1 steep start, >1 gentle start — veloscroll.py's ticker form
    y[m] = min_r + (max_r - min_r) * frac ** curve
    return y


def axis_titles(values):
    """Live plot titles from the scroll-mode keys of the edit buffer.

    TOGGLE_SCROLL true -> "toggled" instead of "held"; unset TOGGLE_SCROLL_H falls back to the
    vertical value exactly like the runtime; VERTICAL_H true renames the second graph — it then
    emits vertical wheel events, not horizontal ones."""
    tog_v = bool(values.get("TOGGLE_SCROLL", False))
    tsh = values.get("TOGGLE_SCROLL_H")
    tog_h = bool(tsh if tsh is not None else values.get("TOGGLE_SCROLL", False))
    h_base = "second vertical ramp" if bool(values.get("VERTICAL_H", False)) else "horizontal ramp"
    return ("vertical ramp — SCROLL_BTN " + ("toggled" if tog_v else "held"),
            f"{h_base} — SCROLL_BTN_H " + ("toggled" if tog_h else "held"),
            h_base)


class _AxisPlot(pg.PlotWidget):
    """One axis's plot: the ramp curve plus deadzone / full-rate marker lines and a drag-to-scrub
    measurement marker (press and drag anywhere on the plot). While a session is held, the marker
    tracks the daemon's live position instead (distance file, key-gated) — see set_live."""

    def __init__(self, title, pen_color):
        super().__init__()
        self.setTitle(title)
        self.showGrid(x=True, y=True, alpha=0.3)
        self.setLabel("bottom", "distance (in)")
        self.setLabel("left", "notches/s")
        self.setMouseEnabled(x=False, y=False)      # no pan/zoom: the mouse scrubs the measurement marker instead
        self.curve = self.plot(pen=pg.mkPen(pen_color, width=2))
        # label="" creates the InfLineLabel; text is set per refresh (values change with edits)
        opts = dict(labelOpts=dict(position=0.85), movable=False)
        self.dz_line = pg.InfiniteLine(pen=pg.mkPen(DZ_COLOR, style=Qt.DashLine), label="", **opts)
        self.full_line = pg.InfiniteLine(pen=pg.mkPen(FULL_COLOR, style=Qt.DashLine), label="", **opts)
        # the measurement marker sits low on the line so its label does not fight the other two; it is
        # hidden until a first scrub (or a live session takes over) — no stray marker at x=0 on startup
        meas_opts = dict(labelOpts=dict(position=0.15), movable=False)
        self.meas_line = pg.InfiniteLine(pen=pg.mkPen(MEAS_COLOR, width=1), label="", **meas_opts)
        for line in (self.dz_line, self.full_line, self.meas_line):
            self.plotItem.addItem(line, ignoreBounds=True)   # ViewBox: positions are data coordinates
        self.meas_line.hide()
        self._params = None                                  # set by update_curve; scrub handler needs it
        self._xmax = None                                    # visible x-range end (live marker clamps to it)
        self._meas_user_x = None                             # last hand-placed marker position (restored after a live hold)
        self._live_active = False                            # True while the distance feed drives the marker
        self._scrubbing = False

    def _set_meas_from_event(self, ev):
        if self._params is None or self._live_active:        # no valid curve / live session owns the marker
            return
        vb = self.plotItem.getViewBox()          # PlotWidget exposes the ViewBox via plotItem, not .vb
        data = vb.mapSceneToView(self.mapToScene(ev.pos()))
        x = float(data.x())
        if x < 0:
            return                                           # left of the origin: not a distance
        rate = ramp_rate(x, *self._params)
        self.meas_line.setValue(x)
        self.meas_line.label.setText(f"{x:.3g} in → {rate:g} notches/s")
        self.meas_line.show()
        self._meas_user_x = x                                # remembered so clear_live can restore it

    def mousePressEvent(self, ev):
        super().mousePressEvent(ev)                          # keep the ViewBox's default handling first
        if ev.button() != Qt.LeftButton or self._live_active:
            return                                           # during a hold the marker belongs to the daemon
        self._scrubbing = True
        self._set_meas_from_event(ev)

    def mouseMoveEvent(self, ev):
        super().mouseMoveEvent(ev)
        if not (ev.buttons() & Qt.LeftButton):
            self._scrubbing = False                          # released outside the widget: stop scrubbing
        elif self._scrubbing:
            self._set_meas_from_event(ev)

    def mouseReleaseEvent(self, ev):
        super().mouseReleaseEvent(ev)
        if ev.button() == Qt.LeftButton:
            self._scrubbing = False

    def set_live(self, dist_in, rate_nps):
        """Take over the measurement marker with the daemon's live position; CurvePlots routes the
        distance file here. The label shows the UNCLAMPED distance (it keeps growing past FULL_IN
        while you keep scrolling) but the line is clamped to the visible range so it never leaves
        the plot."""
        self._live_active = True
        if self._params is None or self._xmax is None:       # no valid curve yet — nothing to place on
            return
        x = min(max(dist_in, 0.0), self._xmax)
        self.meas_line.setValue(x)
        self.meas_line.label.setText(f"{dist_in:.3g} in → {abs(rate_nps):g} notches/s (live)")
        self.meas_line.show()

    def clear_live(self):
        """Session ended: hand the marker back to click/drag — restore the last hand-placed position,
        or hide it if there was none. Idempotent (distance documents keep arriving at ~33 Hz)."""
        if not self._live_active:
            return
        self._live_active = False
        if self._meas_user_x is None or self._params is None:
            self.meas_line.hide()
            return
        x = min(self._meas_user_x, self._xmax)
        rate = ramp_rate(x, *self._params)
        self.meas_line.setValue(x)
        self.meas_line.label.setText(f"{x:.3g} in → {rate:g} notches/s")

    def update_curve(self, params):
        dz, full, min_r, max_r, curve = params
        if not (full > dz and min_r > 0):               # invalid combo: show nothing rather than a bogus ramp
            self.curve.setData([], [])
            self._params = None
            self._xmax = None
            self.meas_line.hide()
            return None
        self._params = params
        xmax = full + 0.1                 # just past the full-rate marker: a little headroom for the label, no dead space after it
        self._xmax = xmax
        x = np.linspace(0.0, xmax, N_POINTS)
        self.curve.setData(x, curve_y(x, params))
        self.dz_line.setValue(dz)
        self.dz_line.label.setText(f"deadzone {dz:g} in")
        self.full_line.setValue(full)
        self.full_line.label.setText(f"full rate at {max_r:g}/s ({full:g} in)")
        # y-top just past the full-rate point: the curve uses the whole y-axis, with a hair of headroom so
        # the 2 px pen is not clipped at the top border (a larger pad would leave dead space above the line)
        return (0.0, xmax, 0.0, max_r * 1.02)


class CurvePlots(QWidget):
    """Both axis plots + the horizontal fallback annotation; re-renders from the edit buffer and,
    when given a StateWatcher, tracks the daemon's live position with the measurement marker (distance file).

    Built as two child widgets instead of one flat column: `top` (vertical plot) and `bottom`
    (horizontal plot). Each fills its body-grid cell exactly like the table beside it, so both graphs are
    the same height as their tables and the gap between the graphs is just the grid spacing — the same as
    the gap between the two tables. RampTab places them into the first two rows of its body grid so the
    "Horizontal ramp" table starts exactly at the horizontal graph's top edge — a side-by-side layout can't
    guarantee that, because where plot_h begins is stretch-driven by plot_v above it. The fallback note
    (lbl_fallback) and the DISTANCE_FILE checkbox (chk_distance) are created here but placed by RampTab:
    the note gets its own grid row directly under the horizontal graph (hidden while empty)."""

    # the daemon rewrites the distance doc every tick (~4 ms) while held — a doc older than this is stale
    # (e.g. the daemon died mid-hold): treat it as session ended instead of pinning the marker forever
    STALE_DISTANCE_S = 0.5

    def __init__(self, model, watcher=None, parent=None):
        super().__init__(parent)
        self.model = model
        # `top` is just the vertical plot — zero margins so it fills its body-grid cell exactly like the
        # "Vertical ramp" table beside it (see class docstring).
        self.top = QWidget()
        lay = QVBoxLayout(self.top)
        lay.setContentsMargins(0, 0, 0, 0)
        # DISTANCE_FILE has no selector row in the table — this checkbox is its only GUI control. Checked by
        # default: while checked the GUI forces it on at startup (the daemon then writes /run/veloscroll/distance,
        # which drives the live markers); it is always forced off again when the GUI closes. Unchecking applies
        # from the next start — an opted-out key is left as configured in the meantime. Not laid out here:
        # RampTab places it below the horizontal-ramp row (with a separator above it).
        self.chk_distance = QCheckBox("live position markers (DISTANCE_FILE)")
        self.chk_distance.setChecked(True)
        self.chk_distance.setToolTip(
            "While checked, the GUI turns DISTANCE_FILE on at startup so these markers track the daemon's live "
            "position while you scroll. It is always turned off again when the GUI closes; unchecking applies "
            "from the next start.")
        t0, t1, _base = axis_titles(self.model.values)   # refresh() keeps them in sync with every edit
        self.plot_v = _AxisPlot(t0, PEN_V)
        self.plot_h = _AxisPlot(t1, PEN_H)
        self._title_v, self._title_h = t0, t1            # last titles pushed (avoids a setTitle per 2 s refresh)
        lay.addWidget(self.plot_v, 1)

        # `bottom` is just the horizontal plot — zero margins so it fills its body-grid cell exactly like
        # the "Horizontal ramp" table beside it (same height, and the gap between graphs = grid spacing).
        self.bottom = QWidget()
        blay = QVBoxLayout(self.bottom)
        blay.setContentsMargins(0, 0, 0, 0)
        blay.addWidget(self.plot_h, 1)

        # fallback note — created here (it knows the values), placed by RampTab in its own grid row directly
        # under plot_h so it never steals height from either graph; hidden while empty. No stylesheet:
        # palette default text color (theme-safe).
        self.lbl_fallback = QLabel("")
        self.lbl_fallback.setTextInteractionFlags(Qt.TextSelectableByMouse)

        # edits in the Config tab replot after a short debounce (spin-box scrubbing fires many changes)
        self._debounce = QTimer(self)
        self._debounce.setSingleShot(True)
        self._debounce.setInterval(DEBOUNCE_MS)
        self._debounce.timeout.connect(self.refresh)
        model.changed.connect(lambda: self._debounce.start())

        if watcher is not None:                            # live marker: distance file while a session is held (key-gated upstream)
            watcher.distance_changed.connect(self._on_distance)

    def _clear_both(self):
        self.plot_v.clear_live()
        self.plot_h.clear_live()

    def _on_distance(self, data):
        """Route one distance document to the active axis's marker; anything else hands both markers back."""
        if not isinstance(data, dict) or not data.get("active") or data.get("mode") not in ("v", "h"):
            self._clear_both()                             # idle doc / flag off -> marker back to click/drag control
            return
        ts = data.get("ts")
        if isinstance(ts, (int, float)) and time.time() - ts > self.STALE_DISTANCE_S:
            self._clear_both()                             # frozen file: the session is gone even though the doc says active
            return
        target = self.plot_h if data["mode"] == "h" else self.plot_v
        other = self.plot_v if target is self.plot_h else self.plot_h
        key = "distance_in_h" if data["mode"] == "h" else "distance_in_v"
        try:
            target.set_live(float(data[key]), float(data.get("rate_nps", 0.0)))
        except (TypeError, ValueError):
            pass                                           # a malformed distance value must never take the plots down
        other.clear_live()

    def refresh(self):
        values = self.model.values
        # titles follow the scroll-mode keys live (TOGGLE_SCROLL(_H) held/toggled, VERTICAL_H renames graph 2)
        t0, t1, h_base = axis_titles(values)
        if self._title_v != t0:
            self.plot_v.setTitle(t0)
            self._title_v = t0
        if self._title_h != t1:
            self.plot_h.setTitle(t1)
            self._title_h = t1
        if not self.model.available or any(values.get(k) is None for k in V_KEYS):
            msg = "daemon module unavailable — cannot compute the curves"
            self.plot_v.update_curve((0.0, 0.0, 0.0, 0.0, 1.0))
            self.plot_h.update_curve((0.0, 0.0, 0.0, 0.0, 1.0))
            self._set_note(msg)
            return
        v, h, fallbacks = axis_params(values)
        # independent scales: each plot fits its own ramp (the overrides often differ a lot from the vertical).
        # padding=0: pyqtgraph otherwise auto-pads the requested range by ~4-5 % per side (size-dependent), which
        # would shrink the curve away from the top edge — with zero padding the full-rate point sits at the
        # y-top exactly as update_curve computed it.
        rng_v = self.plot_v.update_curve(v) or (0, 2, 0, 1)
        self.plot_v.setRange(xRange=(rng_v[0], rng_v[1]), yRange=(0.0, rng_v[3]), padding=0)
        rng_h = self.plot_h.update_curve(h) or (0, 2, 0, 1)
        self.plot_h.setRange(xRange=(rng_h[0], rng_h[1]), yRange=(0.0, rng_h[3]), padding=0)
        if fallbacks:
            parts = [f"{hk} unset — falls back to vertical ({values[vk]:g})" for hk, vk in fallbacks.items()]
            self._set_note(f"{h_base}: " + "; ".join(parts))
        else:
            self._set_note("")                      # nothing to annotate when every override is set

    def _set_note(self, text):
        """The note under the horizontal graph — hidden while empty so its grid row collapses and the
        layout stays tight (RampTab gives it a dedicated row; see class docstring)."""
        self.lbl_fallback.setText(text)
        self.lbl_fallback.setVisible(bool(text))
