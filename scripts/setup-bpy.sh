#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Set up Blender-as-a-module for the mesh builder (assets/build.py).
#
#   scripts/setup-bpy.sh
#
# bpy on PyPI is built for ONE Python version (3.13 today), and ROS 2 needs the
# system's 3.12, so bpy gets an interpreter and a venv of its own: .venv-bpy.
# It is used only to build mesh files; the app and the simulation never import
# it. uv (from PyPI, in .venv-uv) fetches that Python; nothing is installed
# system-wide. The bpy wheel is about 400 MB.
set -euo pipefail
cd "$(dirname "$0")/.."
PY="${BPY_PYTHON:-3.13}"
if [ ! -x .venv-uv/bin/uv ]; then
  python3 -m venv .venv-uv
  .venv-uv/bin/python -m pip install --quiet uv
fi
if [ ! -x .venv-bpy/bin/python ]; then
  .venv-uv/bin/uv venv --quiet --python "$PY" .venv-bpy
fi
.venv-uv/bin/uv pip install --quiet --python .venv-bpy/bin/python bpy
.venv-bpy/bin/python -c 'import bpy; print("  bpy", bpy.app.version_string, "ready in .venv-bpy")'
