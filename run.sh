#!/bin/sh
# Project venv: PySide6 + pyqtgraph (+ numpy) are pip-installed into .venv (see requirements.txt).
exec "$(dirname "$0")/.venv/bin/python" "$(dirname "$0")/main.py"
