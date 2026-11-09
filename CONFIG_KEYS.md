# Config keys (hot-reloaded)

All 30 daemon keys, grouped as in the GUI. Plus the GUI-only
keys: `STARTUP_TAB`, `TRAY_LED` — see
  [Tray icon](#tray-icon). A non-empty value in
`/etc/veloscroll/config.ini`
overrides the built-in default; a missing or empty key falls back to it. Edits hot-reload within ~2 s — no
restart needed (the GUI applies them with one click). Keys with an `_H` suffix are horizontal variants; where
noted, unset or `none` falls back to the vertical value.

### Feel

| Key | Meaning / units | Tuning hint |
|-----|-----------------|-------------|
| `DPI` | Your mouse's current DPI (counts/inch). Converts inches to raw counts. | Set it to your mouse's current DPI setting, not its maximum. |
| `DEADZONE_IN` | Distance from the press point (inches) below which nothing scrolls. | Raise if scrolling starts before you've moved the mouse at all. |
| `FULL_IN` | Distance (inches) from the press point where `MAX_RATE` is reached. Must be > `DEADZONE_IN`. | The span over which speed ramps up. |
| `MAX_RATE` | Scroll rate in notches/second at `FULL_IN` and beyond. | Your top scrolling speed. |
| `MIN_RATE` | Scroll rate in notches/second just past the deadzone. | The floor of the ramp; keep above 0. |
| `CURVE` | Shape of the speed ramp: `>1` gentle near the start, `=1` linear, `<1` steep near the start. | Tune feel: `<1` reacts fast to small movements, `>1` is more forgiving. |
| `OUT_SMOOTH` | `0..1`: how quickly the rate follows the target. Lower = smoother, higher = snappier. | Lower it if scrolling feels steppy or jumpy. |
| `INVERT` | `True`/`False`. Flip scroll direction. | Set `true` if your DE has "natural scrolling" enabled. |
| `INVERT_H` | Horizontal variant of `INVERT` for `SCROLL_BTN_H`. Unset or `none` falls back to the vertical value. | Only if the H axis should be inverted differently than vertical (e.g. natural scrolling on one axis only). |

### Horizontal overrides

Used only while `SCROLL_BTN_H` is held; unset or `none` falls back to the vertical value.

| Key | Meaning / units | Tuning hint |
|-----|-----------------|-------------|
| `DEADZONE_IN_H` | Horizontal variant of `DEADZONE_IN` (inches). | Only if horizontal scrolling should start at a different distance than vertical. |
| `FULL_IN_H` | Horizontal variant of `FULL_IN`. Must be greater than the effective horizontal deadzone. | The span over which horizontal speed ramps up. |
| `MIN_RATE_H` | Horizontal variant of `MIN_RATE` (notches/second). | The floor of the horizontal ramp; keep > 0. |
| `MAX_RATE_H` | Horizontal variant of `MAX_RATE`. Must be >= the effective horizontal `MIN_RATE`. | Your top horizontal scrolling speed. |
| `CURVE_H` | Horizontal variant of `CURVE` (ramp shape). | Same semantics as `CURVE`. |
| `OUT_SMOOTH_H` | Horizontal variant of `OUT_SMOOTH`. | How quickly the horizontal rate follows its target. |

### Device & buttons

| Key | Meaning / units | Tuning hint |
|-----|-----------------|-------------|
| `NAME_MATCH` | Case-insensitive substring of your mouse's name that selects it. Used only when `DEVICE_MATCH` is unset (fallback). | Use a short unique fragment from the device list; if any other device shares it, prefer `DEVICE_MATCH`. Recommended for piping another virtual device into VeloScroll. |
| `DEVICE_MATCH` | Recommended precise selector for a physical device; overrides `NAME_MATCH`. An exact USB ID pair (`0x46d, 0xc084`) or a stable path string (e.g. `/dev/input/by-id/...`) for two identical mice. | Set it in the GUI's Device Picker tab (its USB ID pair): stable across reboots/ports, and lets per-device pointer rules apply automatically. |
| `SCROLL_BTN` | Key code of the button to hold for scrolling (evdev name like `BTN_SIDE`, or decimal code, e.g. `275`). | Use the GUI's [Capture…] dialog (or `ListButtonPresses.py` from the daemon repo). Names are `evdev.ecodes.BTN_*`. |
| `SCROLL_BTN_H` | Optional second button to hold for velocity scrolling. By default horizontal, driven by mouse X movement (emits `REL_HWHEEL`/`REL_HWHEEL_HI_RES`), using the vertical ramp unless overridden by the `_H` keys. With `VERTICAL_H = true` it emits vertical wheel events from up/down movement instead, at the same `_H`-key values. Evdev name or decimal code, or `none` to disable (default). | Must differ from `SCROLL_BTN` and `DISABLED_BTN`. `INVERT` flips both axes (`INVERT_H` overrides it for the H axis). |
| `DISABLED_BTN` | Scroll Bypass latch: key code of a button that toggles bypassing velocity scrolling entirely on/off at runtime (always starts off per daemon start); while ON the scroll buttons behave as normal clicks and movement is unmediated. Or `none` to disable this feature. | The tray icon turns red while latched (requires `STATE_FILE = true`). Set `none` if you don't want a bypass latch. |

### Scroll mode

| Key | Meaning / units | Tuning hint |
|-----|-----------------|-------------|
| `TOGGLE_SCROLL` | How `SCROLL_BTN` engages velocity scrolling. `False` (default) = hold-to-scroll, the button must stay pressed. `True` = tap to toggle on, tap again to toggle off (releasing does nothing while toggled). | While toggled on, movement is captured exactly as in hold mode (cursor stays put, same ramp from the tap point). Set `true` if you prefer a latched scroll mode over holding. |
| `TOGGLE_SCROLL_H` | Horizontal variant of `TOGGLE_SCROLL` for `SCROLL_BTN_H`. Unset or `none` falls back to the vertical value. | Only if horizontal scrolling should use a different engagement mode than vertical (e.g. hold vertically, toggle horizontally). |
| `VERTICAL_H` | `True`/`False`. With `true`, holding `SCROLL_BTN_H` becomes a second vertical velocity scroll instead of horizontal: mouse up/down movement drives `REL_WHEEL`/`REL_WHEEL_HI_RES` using the horizontal ramp values (`*_H` keys), and left/right movement does nothing. `false` (default) = horizontal behavior. | Only takes effect when `SCROLL_BTN_H` is set. Flipped by `INVERT_H` when set, else by `INVERT`, like every other axis. Flipping it live mid-hold ends any in-progress session. |
| `LEFT_CLICK_RESET` | `True`/`False`. What releasing the left mouse button does while a scroll session is active: `true` (default) = fresh origin — the next movement scrolls from zero again; `false` = keep the current distance, so scrolling continues at its old speed. | Set `false` if you prefer to click mid-session without losing your scroll position/speed. Velocity output is paused while left is down either way. |

### Logging

| Key | Meaning / units | Tuning hint |
|-----|-----------------|-------------|
| `LOG_LEVEL` | What the daemon writes to its log (`journalctl -u veloscroll`). Integer `0..3`, higher = more verbose: `0` errors only, `1` + events (`grabbed …`), `2` + config detail (default: unknown keys ignored, reload rejected, config reloaded old→new), `3` + debug. | Set `0` on a healthy system for silence; raise to `3` temporarily when debugging device selection. Lowering below `2` also hides the `config reloaded:` confirmation line itself. |
| `LATENCY_LOG` | `True`/`False`. Per-second measured latency summary lines plus a computed model line at startup/config change. Independent of `LOG_LEVEL`. | Debugging only: set `true`, watch `journalctl -u veloscroll`, then leave it `false`. |

### Misc

| Key | Meaning / units | Tuning hint |
|-----|-----------------|-------------|
| `MAX_BACKUPS` | How many `.bak-<timestamp>` backups the preset helper and seed script keep per file family. `-1` = unlimited, `N ≥ 1` = keep only the newest N after each run that writes a backup, `0` = backups disabled entirely (existing ones stay untouched). | Hot-reloaded like every other key — each helper run reads the current value. Set `0` if you don't want any `.bak-*` files at all. |
| `TICK` | Ticker loop period in seconds; `0.004` s ≈ 250 Hz — how often the scroll rate is recomputed and hi-res events are emitted. Changing it requires an admin authentication (the helper raises such a write to the polkit `tick` action); everything else applies silently. | Not a feel setting (that's `OUT_SMOOTH`/`MAX_RATE`). Only lower it if scrolling still looks steppy, at the cost of more CPU. |
| `STATE_FILE` | `True`/`False`. When true, the daemon writes `/run/veloscroll/state`: a JSON snapshot of all effective values, the grabbed devices and scroll activity (plus reload health) for GUI frontends to read. Off by default — zero overhead when no GUI runs. | Per-machine operational state like `MAX_BACKUPS` — never part of presets; managed by the GUI itself (forced on while it is open, off at exit — no selector row). The shipped unit creates `/run/veloscroll` (`RuntimeDirectory=veloscroll`). See [GUI-managed lifecycle keys in the README](README.md#gui-managed-lifecycle-keys-state_file-distance_file). |
| `DISTANCE_FILE` | `True`/`False`. When true, the daemon writes a second file, `/run/veloscroll/distance`: the live per-axis distances (vertical + horizontal) and smoothed rate of the active scroll session, rewritten on every ticker tick while a button is held (~250 Hz). This is what drives the Ramp tab's live curve markers — with it off they only follow your mouse. Off by default — zero overhead without a consumer. | Per-machine operational state like `STATE_FILE` — never part of presets; managed by the GUI itself (forced on while open when the Ramp tab checkbox is checked, always off at exit — no selector row). The same unit creates `/run/veloscroll` for this file too. See [GUI-managed lifecycle keys in the README](README.md#gui-managed-lifecycle-keys-state_file-distance_file). |
