#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-3.0-or-later
# The run replay's 3D (static/replay.js) uses three.js. The edge runs offline, so the part of three.js it needs is
# vendored here as one minified ES module, made by this script and committed (like static/how.html):
#
#   scripts/vendor-three.sh            # needs node and npm once; writes permtek5_twin_edge/static/vendor/three-replay.js
#
# three.js is MIT-licensed; its licence comment stays at the top of the bundle.
set -euo pipefail
cd "$(dirname "$0")/.."
THREE=0.186.1
ESBUILD=0.25.12
work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT
( cd "$work" && npm init -y >/dev/null && npm install --no-audit --no-fund --silent "three@$THREE" "esbuild@$ESBUILD" )
cat > "$work/entry.js" <<'JS'
export { AmbientLight, BoxGeometry, Color, DirectionalLight, HemisphereLight, Mesh, MeshStandardMaterial, PerspectiveCamera,
  Scene, SphereGeometry, WebGLRenderer } from "three";
export { OrbitControls } from "three/examples/jsm/controls/OrbitControls.js";
JS
mkdir -p permtek5_twin_edge/static/vendor
( cd "$work" && npx esbuild entry.js --bundle --format=esm --minify --legal-comments=inline --outfile=three-replay.js )
cp "$work/three-replay.js" permtek5_twin_edge/static/vendor/three-replay.js
echo "  three.js $THREE -> permtek5_twin_edge/static/vendor/three-replay.js ($(du -h permtek5_twin_edge/static/vendor/three-replay.js | cut -f1))"
