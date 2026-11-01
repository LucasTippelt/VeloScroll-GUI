# VeloScroll GUI — Usage

Post-install documentation for the [VeloScroll GUI](README.md): how each tab works, feel-preset starting
points, the page's permissions checks and a reference of all daemon config keys. Setup itself lives in the
README ([One-time setup](README.md#one-time-setup), [Running](README.md#running)).

## Status & Config

The first page merges daemon status with config editing; top to bottom: the Daemon group, the values
table, this page's Apply/Cancel bar, and the [Permissions panel](#permissions-panel).

- **Daemon group** — service state (green = active, red = failed), the devices the daemon currently has
  grabbed, and config reload health (`reload OK` / `reload rejected: …` / `pending reload (~2 s)` when
  `config.ini` is newer than the last state-file update).
- **Values table** — one row per key with four columns: Key | Effective value (live from the state file;
  a red `—` means the daemon source could not be loaded) | Edit (typed widget) | Description. The feel &
  scroll-mode keys are edited on the [Scroll/Invert tab](#scrollinvert), the ramp keys on the
  [Ramp tab](#ramp) and the device & button keys on the
  [Device Picker tab](#device-picker); there is exactly one editor per key in the whole window, all of
  them writing to a single shared edit buffer — so a change made on any tab can be applied or discarded
  from any tab's bar. `STATE_FILE`/`DISTANCE_FILE` have no rows: they are [GUI-managed lifecycle keys](README.md#gui-managed-lifecycle-keys-state_file-distance_file).
- **Startup tab (`STARTUP_TAB`, GUI section)** — a GUI-only key (not one of the 30 daemon keys; the
  daemon ignores it) that picks which tab opens on startup. The combo lists every tab in display order;
  an unset or unknown value falls back to the first one (Status & Config). Its Effective value column
  shows what `config.ini` says, since no state-file/computed dataset ever carries it. Press Apply to make
  a change stick — it takes effect from the next start.
- **Apply / Cancel** (every tab page carries its own bar over the same buffer) — Apply validates inline
  against the daemon's own checks, writes `/etc/veloscroll/config.ini` through the scoped sudoers rule
  (`priv.py apply-config`), then confirms the reload from the state file (~2 s) or, without one, from the
  journal. Cancel discards unapplied edits; if a change was already applied earlier in this session it
  instead writes the previous config back through the same privileged path so the daemon reloads it.
  Unsaved changes are marked in every bar and in the window title.
- External edits to `config.ini` are picked up on the 2 s refresh cycle (your unapplied edits are kept,
  with a note that Cancel now reverts to your starting point rather than the new file).

## Scroll/Invert

The **Feel** (`INVERT`, `INVERT_H`, `TICK`) and **Scroll mode** (`TOGGLE_SCROLL`, `TOGGLE_SCROLL_H`,
`VERTICAL_H`, `LEFT_CLICK_RESET`) sections, moved here from the Status & Config table — same layout (Key |
Effective value | Edit | Description), same shared edit buffer, with this tab's own Apply/Cancel bar.
(The rate/curve/smoothing keys that used to sit in "Feel" live on the [Ramp tab](#ramp) next to their
live curves.)

## Ramp

The ramp options next to their live curves: the **Vertical ramp** panel (`DEADZONE_IN`, `FULL_IN`,
`MIN_RATE`, `MAX_RATE`, `CURVE`, `OUT_SMOOTH`) beside the vertical curve, and the **Horizontal overrides**
panel (the six `_H` keys) beside the horizontal one. Edits land in the shared edit buffer — nothing is
applied until you press Apply on this tab's bar. (`DPI` lives on the [Device Picker tab](#device-picker),
next to the mouse it belongs to.)

- The tables here have no Description column: instead, the last-clicked field's description is shown at
  the bottom left, in the same row as Cancel/Apply — clicking anywhere in a row (its Key / Effective value
  cells or its editor) selects it. It also follows Tab navigation, and shows a field's error text (in red)
  or its unset-fallback note when applicable.

- Each plot shows the daemon's exact target-rate formula (notches/s vs distance from the press point):
  gray dashed line = deadzone, green dashed line = full rate; the two plots keep independent axis scales
  so a very different horizontal ramp is not squashed to match the vertical one.
- Click or drag anywhere on a plot to read the rate at that distance — the orange marker follows your
  pointer and labels it (`1.23 in → 87 notches/s`); pan/zoom is disabled, the mouse scrubs instead.
- **Live position markers** — while a scroll session runs (button held or toggled on), that same orange
  marker tracks the daemon's live position from `/run/veloscroll/distance`, polled at the daemon's
  ticker rate (`TICK`, ~250 Hz by default), and shows `(live)`;
  on release it returns to where you last placed it by hand. The "live position markers (DISTANCE_FILE)"
  checkbox (checked by default) decides whether the GUI forces `DISTANCE_FILE = True` at startup —
  unchecking applies from the next start, and the key is always forced off again when the GUI closes
  ([GUI-managed lifecycle keys](README.md#gui-managed-lifecycle-keys-state_file-distance_file)).
- Plot titles follow the scroll-mode keys: "held" vs "toggled" per `TOGGLE_SCROLL(_H)`, and
  `VERTICAL_H = true` renames the second graph to "second vertical ramp". A note under the vertical plot
  lists every `_H` key that is currently falling back to its vertical value.

## Feel presets (starting points)

| Case | MAX_RATE | MIN_RATE | CURVE | OUT_SMOOTH | When to use it |
| --- | --- | --- | --- | --- | --- |
| Balanced (shipped default) | 130.0 | 4.0 | 0.5 | 0.8 | The anchor for judging any other preset; if something feels off, start with `MAX_RATE` before the ramp shape. |
| Fast | 220.0 | 6.0 | 0.35 | 0.9 | Long documents and big lists — steep ramp (low `CURVE`) reaches speed quickly; too fast → lower `MAX_RATE` first. |
| Precise | 45.0 | 2.0 | 1.6 | 0.5 | Spreadsheets, code, small UIs — low ceiling, gentle start, heavy smoothing damps jitter; still jittery → lower `OUT_SMOOTH`. |
| Cruising | 90.0 | 3.0 | 1.2 | 0.65 | Long reading sessions — the comfortable middle between Fast and Precise. |
| Snappy | 160.0 | 8.0 | 1.0 | 1.0 | Deliberate movements, minimal lag — `OUT_SMOOTH` 1.0 = instant tracking; jumpy → lower it to ~0.9 (the only knob that controls how fast the rate follows). |
| Firefox | 158.0 | 1.5 | 0.85 | 0.9 | Approximation of Firefox Autoscroll — also ships `DEADZONE_IN` 0.0075 / `FULL_IN` 1.1; tune those to your mouse/desk. |

**Horizontal** — the cases above tune the vertical ramp; to give horizontal scrolling a different feel,
  set one case's values as the corresponding `_H` keys (each falls back independently when unset). With
  `VERTICAL_H = true`, those `_H` values instead drive a second *vertical* scroll on `SCROLL_BTN_H`.

- **Two vertical modes** — the bundled dual-axis preset `two-vertical`: `SCROLL_BTN` at a slow-to-medium pace (`MAX_RATE` 90 /
  `MIN_RATE` 3.0 / `CURVE` 1.2 / `OUT_SMOOTH` 0.65)
  for precise reading, `SCROLL_BTN_H` with `VERTICAL_H = true` at a medium-to-fast pace (`MAX_RATE` 220 /
  `MIN_RATE` 90 / `CURVE` 0.35 / `OUT_SMOOTH` 0.9) for quick page jumps — e.g. main button vs thumb
  button. It sets both ramps plus `VERTICAL_H`, so
  load it via the **all** checkbox on the Presets tab (`loadall`), not feel. The distance keys are
  deliberately not part of it — set them once per machine.
- **DPI** — no per-DPI variants are needed: `DEADZONE_IN`/`FULL_IN` are in inches and converted by your
  [`DPI`](#device-picker) setting, so the physical ramp is identical at any DPI. Set `DPI` to your mouse's
  *current* DPI, not its maximum.
- These are starting points to tune by feel, not verified values — every key hot-reloads within ~2 s and a
  malformed edit is rejected with the previous values staying in effect (the Daemon group shows it). For
  the physical distance keys you can measure instead of guessing: use the **Live position markers** in the Ramp tab.

## Device Picker

Select the mouse the daemon should grab and assign its buttons. The form on the left holds the device,
DPI & button fields (`NAME_MATCH`, `DEVICE_MATCH`, `DPI`, `SCROLL_BTN`, `SCROLL_BTN_H`, `DISABLED_BTN`) —
same shared edit buffer as everywhere else; each button row carries a **[Capture…]** dialog (press the
physical button, click its code in the list).

- The dropdown on the right lists your input devices: enumeration runs in-process first (needs the
  `input` group) and falls back to root (`priv.py devices`) when any node is unreadable. One physical
  mouse usually exposes several event nodes — they collapse into a single entry annotated "(N nodes)".
  The daemon's own virtual output node, `veloscroll-mouse`, appears clearly labeled but cannot be
  selected; the device(s) currently grabbed are highlighted green on every refresh. **[Rescan]**
  re-enumerates (hot-plugged devices).
- Selecting a device writes `DEVICE_MATCH` into the edit buffer immediately — stable USB ID pair
  (`0x46d, 0xc084`) for real mice, node path when there is no USB id; the top entry sets it to `none`,
  falling back to `NAME_MATCH`. Press **Apply** to make it stick: the daemon re-grabs the new device
  within ~2–4 s.

## Presets

Named snapshots of the daemon keys in `/etc/veloscroll/presets/*.ini` — save what you have now, load key
groups from saved presets, delete ones you no longer need. The list shows name / modified time / key
count; selecting a row displays its raw file below it.

- **Save current as…** — snapshots every daemon key except `MAX_BACKUPS`, `STATE_FILE` and `DISTANCE_FILE`
  (per-machine operational state, never part of a preset) into `presets/<name>.ini`. Names may use letters,
  digits, `.`, `_`, `-`. The `*_H` keys are captured only when explicitly set — an unset axis keeps its
  fallback-to-vertical relation on any machine instead of baking in a stale number. Unknown keys in a
  preset file are warned about and ignored.
- **Load panel** — one checkbox per key group: feel (`loadfeel`: the eight rate/curve/smoothing keys +
  `_H`), distances (`loaddistances`: `DEADZONE_IN` / `FULL_IN` + `_H`), buttons-keys (`loadkeys`:
  `SCROLL_BTN` / `SCROLL_BTN_H` / `DISABLED_BTN`), device (`loaddevice`: `NAME_MATCH` / `DEVICE_MATCH` /
  `DPI`), scroll (`loadscroll`: the four scroll-mode flags), plus `all` (`loadall` — everything present
  except the button & device groups). The groups exist so a shared feel preset can't silently change
  another machine's physical distances or button bindings; keys outside a group are never applied by it.
  Checked rows run as a single command, top to bottom (each row may name its own preset); the exact
  command is shown for confirmation before anything runs. A load writes `config.ini` directly — the daemon
  hot-reloads within ~2 s and the edit buffer resyncs automatically on the next refresh.
- **Delete selected…** — removes the preset picked in the list after a confirm/cancel popup (Cancel is the
  default). Its `.bak-*` backups are kept and can be put back from the [Backups tab](#backups).
- The transcript pane keeps the output of every operation; **[Refresh list]** re-scans the directory
  (after a manual edit or another GUI instance).

## Backups

Backups made before every config overwrite, pruned per `MAX_BACKUPS` — this tab lists all well-formed
`.bak-<timestamp>` files under `/etc/veloscroll` (`config.ini`) and its `presets/` subdirectory, newest
first.

- Selecting a row previews the backup's keys against the current effective values; rows that differ are
  highlighted amber — restoring changes exactly those.
- **Restore selected…** — after confirmation, puts the backup back over its original file (an explicit
  relative path keeps two backups with the same basename unambiguous). Restoring `config.ini` makes the
  daemon hot-reload it within ~2 s. Every restore backs up the current state first, so undoing is itself
  reversible (while `MAX_BACKUPS != 0`).

## Permissions panel

The page's Permissions group (below the values table) checks, with a copy-paste fix for each gap (and [Re-check]):

1. the scoped sudoers rule (`sudo -n ... priv.py ping`),
2. current user in the `input` group,
3. `/etc/veloscroll/config.ini` exists,
4. unit file present at `/etc/systemd/system/veloscroll.service` and service enabled.

## Config keys (hot-reloaded)

All 30 daemon keys, grouped as in the GUI — the ramp keys are edited on the [Ramp tab](#ramp) next to
their live curves, Device & buttons (plus `DPI`) on the [Device Picker tab](#device-picker), the Feel &
Scroll mode sections (`INVERT`, `INVERT_H`, `TICK` and the four scroll-mode flags) on the
[Scroll/Invert tab](#scrollinvert), and Logging with Misc in the Status & Config table. Plus one GUI-only
key, `STARTUP_TAB` (which tab opens on startup; edited on the [Status & Config page](#status--config) —
it is not a daemon key, so it never hot-reloads: the daemon ignores it). A non-empty value in
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
| `TICK` | Ticker loop period in seconds; `0.004` s ≈ 250 Hz — how often the scroll rate is recomputed and hi-res events are emitted. | Not a feel setting (that's `OUT_SMOOTH`/`MAX_RATE`). Only lower it if scrolling still looks steppy, at the cost of more CPU. |

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
| `DISABLED_BTN` | Key code that is swallowed and does nothing, or `none` to disable this feature. | Set `none` if you don't want to disable a button. |

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
| `STATE_FILE` | `True`/`False`. When true, the daemon writes `/run/veloscroll/state`: a JSON snapshot of all effective values, the grabbed devices and scroll activity (plus reload health) for GUI frontends to read. Off by default — zero overhead when no GUI runs. | Per-machine operational state like `MAX_BACKUPS` — never part of presets; managed by the GUI itself (forced on while it is open, off at exit — no selector row). The shipped unit creates `/run/veloscroll` (`RuntimeDirectory=veloscroll`). See [GUI-managed lifecycle keys in the README](README.md#gui-managed-lifecycle-keys-state_file-distance_file). |
| `DISTANCE_FILE` | `True`/`False`. When true, the daemon writes a second file, `/run/veloscroll/distance`: the live per-axis distances (vertical + horizontal) and smoothed rate of the active scroll session, rewritten on every ticker tick while a button is held (~250 Hz). This is what drives the Ramp tab's live curve markers — with it off they only follow your mouse. Off by default — zero overhead without a consumer. | Per-machine operational state like `STATE_FILE` — never part of presets; managed by the GUI itself (forced on while open unless opted out via the Ramp tab checkbox, always off at exit — no selector row). The same unit creates `/run/veloscroll` for this file too. See [GUI-managed lifecycle keys in the README](README.md#gui-managed-lifecycle-keys-state_file-distance_file). |
