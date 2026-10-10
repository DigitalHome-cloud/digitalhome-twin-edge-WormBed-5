#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copy the cloud's vocabulary into this edge, so it validates and reads a
# habitat exactly as PermTek-5 does (docs/edge.md):
#
#   scripts/sync-ontology.sh [path to the PermTek-5 checkout]
#
# T-Boxes, concepts and shapes go to edge/ontology/; the crop library and the
# invented CityDemo habitat go to the package's data/ (the twin reads sowing
# depth, seeds and spacing from the library, and runs CityDemo unlinked).
set -euo pipefail
cd "$(dirname "$0")/.."
SRC="${1:-../digitalhome-cloud-PermTek-5}/packages/ontology"
[ -d "$SRC/tbox" ] || { echo "no ontology at $SRC" >&2; exit 1; }
PKG=permtek5_twin_edge
mkdir -p "$PKG/edge/ontology/tbox" "$PKG/edge/ontology/shapes" "$PKG/data"
rm -f "$PKG"/edge/ontology/tbox/*.ttl "$PKG"/edge/ontology/shapes/*.ttl
cp "$SRC"/tbox/*.ttl "$PKG/edge/ontology/tbox/"
cp "$SRC"/shapes/*.ttl "$PKG/edge/ontology/shapes/"
cp "$SRC/abox/library/crops.ttl" "$PKG/data/crops.ttl"
cp "$SRC/abox/examples/citydemo-habitat.ttl" "$PKG/data/citydemo-habitat.ttl"
echo "  ontology, crop library and CityDemo copied from $SRC"
