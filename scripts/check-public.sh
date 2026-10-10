#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Fail if anything that could locate a real site is about to be published.
#
#   scripts/check-public.sh [DIR]      # checks the files git would publish under DIR (default: this repository)
#
# Real site names are NOT written here (that would publish them). They live in
# a private denylist, one case-insensitive regex per line:
#   $PERMA_DENYLIST, default ~/.config/permaculture/denylist
# Without it only the generic checks run (and a warning says so).
set -u
cd "${1:-$(dirname "$0")/..}"
fail=0
say() { echo "check-public: $*" >&2; fail=1; }

if git rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  mapfile -t files < <(git ls-files --cached --others --exclude-standard)
else
  mapfile -t files < <(find . -type f -not -path './.git/*' -not -path './worlds/*' -not -path './data/*' \
                        -not -name site_config.json -not -path '*/__pycache__/*' | sed 's#^\./##')
fi

# 1. no plans, generated worlds or run data
for f in "${files[@]}"; do
  case "$f" in
    tools/plan/*|twin/tools/plan/*|worlds/*|twin/worlds/*|data/*|twin/data/*|site_config.json|twin/site_config.json)
      say "generated or private file tracked: $f" ;;
    *.jpg|*.jpeg|*.tif|*.tiff|*.geojson|*.kml|*.gpx|*.dxf) say "map-like file: $f (only demo images under docs/ are allowed)" ;;
  esac
done

# 2. no geographic coordinates or real elevations in site files
for f in "${files[@]}"; do
  [ -f "$f" ] || continue
  if grep -n -i -E '"(lat|lon|lng|latitude|longitude|epsg|crs|georef)"[[:space:]]*:' "$f" >/dev/null; then
    say "geographic key in $f"
  fi
done
for f in "${files[@]}"; do
  case "$f" in sites/*.json|sites/*/*.json|twin/sites/*.json|twin/sites/*/*.json)
    python3 - "$f" <<'PY' || fail=1
import json, sys
s = json.load(open(sys.argv[1]))
bad = []
if s.get("z_offset", 0) != 0:
    bad.append("z_offset must be 0 in a public site (no real elevation)")
if "ground_image" in s:
    bad.append("ground_image (a site plan) is not allowed in a public site")
for b in bad:
    print(f"check-public: {sys.argv[1]}: {b}", file=sys.stderr)
sys.exit(1 if bad else 0)
PY
  esac
done

# 3. the private denylist: names of real places, people, parcels
deny="${PERMA_DENYLIST:-$HOME/.config/permaculture/denylist}"
if [ -f "$deny" ]; then
  while IFS= read -r pat; do
    [ -z "$pat" ] && continue
    case "$pat" in \#*) continue ;; esac
    for f in "${files[@]}"; do
      [ -f "$f" ] || continue
      if grep -q -i -E -- "$pat" "$f" 2>/dev/null; then say "denylisted term in $f"; fi
      if echo "$f" | grep -q -i -E -- "$pat"; then say "denylisted term in file name $f"; fi
    done
  done < "$deny"
else
  echo "check-public: warning: no denylist at $deny, only generic checks ran" >&2
fi

# 4. secrets
if command -v gitleaks >/dev/null 2>&1 && git rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  gitleaks detect --no-banner --redact -q || say "gitleaks found something"
fi

[ "$fail" = 0 ] && echo "check-public: ok (${#files[@]} files)"
exit "$fail"
