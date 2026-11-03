# Versioning

The GUI and daemon share one version number, `vX.Y.Z`:

- **X — major release / parity break.** A change to the shared contract below. No compatibility is promised across different X values; upgrade both components as a set.
- **Y — new feature or behavior change** in either the GUI or the daemon.
- **Z — bugfixes and documentation updates.**

v2.0.0 is the first release under this scheme, re-baselined from v1.4.0 (which used an older convention where Y marked parity with the daemon). The re-baseline itself does not change the shared contract.

## Shared contract

The artifacts both components depend on — changing any of them is a parity break (X bump):

- config keys read by the daemon (`/etc/veloscroll/config.ini`)
- `/run/veloscroll/state` feed (~4 Hz) and `/run/veloscroll/distance` feed (~250 Hz)
- helper/D-Bus interface: bus name `org.veloscroll.gui`, polkit policy `org.veloscroll.gui.policy`, deployed helper at `/usr/lib/veloscroll-gui/helper_service.py`

## Bump procedure

1. Update `VERSION` in `velogui/__init__.py` (single source of truth; shown in the window title via `main.py`).
2. Add a `CHANGELOG.md` section for the new version.
3. When X or Y moves, bump the daemon repo in the same release so both components ship matching versions.