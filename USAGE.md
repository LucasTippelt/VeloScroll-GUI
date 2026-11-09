# VeloScroll GUI — Usage

Post-install documentation for the [VeloScroll GUI](README.md): how each tab works, feel-preset starting
points, the page's permissions checks and a reference of all daemon config keys. Setup itself lives in the
README ([One-time setup](README.md#one-time-setup), [Running](README.md#running)).

## Status & Config

The first page merges daemon status with config editing; top to bottom: the Daemon group, the values
table, this page's Apply/Cancel bar, and the [Permissions panel](#permissions-panel).

- **Daemon group** — service state (green = active, red = failed), the devices the daemon currently has
  grabbed, config reload health (`reload OK` / `reload rejected: …` / `pending reload (~2 s)` when
  `config.ini` is newer than the last state-file update), and **[Update daemon…]**: a one-click update of
  the daemon only (per [docs/UPDATING.md](https://github.com/LucasTippelt/VeloScroll/blob/main/docs/UPDATING.md)) —
    after confirmation it runs the helper service's `Update` (native polkit auth prompt): `git pull --ff-only` in `/opt/veloscroll`, run **as your user** (the checkout owner), then as root re-deploys any changed files to `/usr/lib/veloscroll`
  a service-file refresh + daemon-reload when the release changed `veloscroll.service`, and a restart only if `veloscroll.py` or the unit changed, plus a journal tail. The
  dialog shows a transcript of every step; on a clean repo it ends in an "already up to date" no-op (nothing is
  restarted), and after a real update it offers the previous commit as a one-line rollback command. Directly
   below it, **[Update GUI…]** updates the GUI only — after confirmation it runs the helper service's `UpdateGui` (native polkit auth prompt):
   `git pull --ff-only` in `/opt/veloscroll-gui`, run **as your user** (the checkout owner); when a release changes the helper it redeploys the runtime copy
to `/usr/lib/veloscroll-gui` and restarts its service (the self-restart is scheduled after the transcript is
delivered). The GUI is not a service, so there is nothing to restart via
  systemd; instead, after a successful pull that changes code, the running instance closes & reopens itself
  automatically (an "already up to date" no-op leaves it untouched). It shows the same transcript and offers
  the previous commit as a one-line rollback command. Your `config.ini` lives outside both repos and is
  untouched either way. 
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
- **Tray LED (`TRAY_LED`, GUI section)** — one more GUI-only key (the daemon ignores it) that shows/hides
  the status-LED tray icon. Unlike `STARTUP_TAB` it applies **live** — flipping the checkbox takes effect
  immediately without Apply (it is a chrome switch with no daemon round-trip), and Cancel reverts it like
  any other unapplied edit. Press Apply to make a change stick across restarts; an unset key means on. See
  [Tray icon](#tray-icon).
- **Apply / Cancel** (every tab page carries its own bar over the same buffer) — Apply validates inline
  against the daemon's own checks, writes `/etc/veloscroll/config.ini` through the polkit-gated helper service
  (`ApplyConfig`), then confirms the reload from the state file (~2 s) or, without one, from the
  journal. Cancel discards unapplied edits; if a change was already applied earlier in this session it
  instead writes the previous config back through the same privileged path so the daemon reloads it.
  Unsaved changes are marked in every bar and in the window title.
- External edits to `config.ini` are picked up on the 2 s refresh cycle (your unapplied edits are kept,
  with a note that Cancel now reverts to your starting point rather than the new file.
  #### Permissions panel

The Status & Config page's Permissions group (below the values table) checks, with a copy-paste fix for each gap (and [Re-check]):

1. the helper service reachable on the system bus (a D-Bus `Ping`) — it performs every privileged operation, gated per call by polkit: read-only ops and config/preset/service management pass without a prompt, while button capture and both updates raise the native auth prompt on every start (no `input` group needed),
2. `/etc/veloscroll/config.ini` exists,
3. unit file present at `/etc/systemd/system/veloscroll.service` and service enabled,
4. the deployed helper copy `/usr/lib/veloscroll-gui/helper_service.py` **and its directory** root-owned and not user/group-writable — the unit executes *that* copy as root, so a user-writable deployed helper (or its directory, via rename-swap) would be an escalation vector; the repo's `helpers/helper_service.py` is plain source, user-writable by design. [Re-check] surfaces drift of the deployed copy with the redeploy copy-paste fix.


## Scroll/Invert

The **Feel** (`INVERT`, `INVERT_H`) and **Scroll mode** (`TOGGLE_SCROLL`, `TOGGLE_SCROLL_H`,
`VERTICAL_H`, `LEFT_CLICK_RESET`) sections (Key |
Effective value | Edit | Description), same shared edit buffer, with this tab's own Apply/Cancel bar.


## Ramp

The ramp options next to their live curves: the **Vertical ramp** panel (`DEADZONE_IN`, `FULL_IN`,
`MIN_RATE`, `MAX_RATE`, `CURVE`, `OUT_SMOOTH`) beside the vertical curve, and the **Horizontal overrides**
panel (the six `_H` keys) beside the horizontal one. Edits land in the shared edit buffer — nothing is
applied until you press Apply on this tab's bar.

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
    checkbox (checked by default) decides whether the GUI forces `DISTANCE_FILE = True` at startup — unchecking
    applies from the next start, and the key is always forced off again when the GUI closes
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
 physical button, click its code in the list). Listening runs through the helper service's `StartCapture` — a
  polkit auth prompt appears on every start — which opens input nodes **read-only** by design: it can see your
  presses but cannot inject events. Cancelling or denying that prompt closes the dialog again.

- The dropdown on the right lists your input devices: enumeration runs in-process first (only applies when
  you have direct `/dev/input` access) and falls back to the helper service's `Devices` (root) otherwise. One physical
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
count; selecting a row previews its keys against the current effective values on the right — changed rows
highlighted amber (loading changes exactly those), re-diffed when the state file changes.

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
   command is shown for confirmation before anything runs. Below [Load selected…] that same command is also
   shown live in a read-only field (`sudo /usr/lib/veloscroll/veloscroll_preset.py …`) with a [Copy] button,
   so it can be copy-pasted into a terminal or used for keyboard-shortcut automation. A load writes `config.ini`
   directly — the daemon hot-reloads within ~2 s and the edit buffer resyncs automatically on the next refresh.
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

## Tray icon

- **Tray icon** (`TRAY_LED`, on by default) — a small dot in your system tray: red while scroll bypass is
  latched (the daemon's `bypass` flag from the state file, added in daemon v2.1.0), green while a scroll session
  is active (the daemon's `active` flag, re-read at 10 Hz), gray when idle. A single left
  click toggles the main window (shows/raises it, or hides it). Turn it off with the `TRAY_LED` checkbox on
  the Status & Config page (GUI section) — it applies live and sticks after Apply. If the session has no
  system tray the icon simply isn't created — nothing else depends on it.

