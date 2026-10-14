#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 D-LAB-5
"""
i18n-check.py — every English text the edge shows, and what the catalogs lack.

    .venv/bin/python scripts/i18n-check.py              # lists what de.json and fr.json lack, and broken placeholders
    .venv/bin/python scripts/i18n-check.py --missing-json out.json   # the missing texts, to translate

The English text is the key (permtek5_twin_edge/i18n.py). It is collected from:
  - Python: every _("...") call (ast, so texts split over lines are joined), and
    the tables whose texts are translated where they are served (keeper.TEXT,
    operator_view's GROUPS, RULES and SENSORS, disturbances.CATALOGUE, film's
    SHOTS and QUALITY, scenarios.available's reasons)
  - the page: index.html's text and its title, aria-label, alt and placeholder
    attributes; app.js's tr("...") calls (every string inside the call) and its
    CARE_WHY table; and the words the robot uses for jobs and their states
  - the scenarios: title, about, and each step's title, why, button label and
    expectations ("say")
  - the template's core.js and edge.js: their fixed texts, which the page translates as they appear
Exit status 1 when a catalog lacks a text or a translation lost a placeholder.
"""
import argparse
import ast
import html.parser
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PKG = ROOT / "permtek5_twin_edge"
sys.path.insert(0, str(ROOT))

# words the robot and the jobs use that reach the page as they are (app.js: job_(), tr(x.state) ...)
WORDS = ["feed", "water", "plant", "probe", "photo", "scan", "stamp", "care", "tap", "rest", "topdress", "home", "move",
         "running", "done", "failed", "cancelled", "aborted", "queued", "refused",
         "ok", "watch", "alarm", "note", "planned", "sown", "growing", "robot", "you"]


def from_python():
    out = set()
    for f in sorted(PKG.glob("*.py")):
        for node in ast.walk(ast.parse(f.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "_" and node.args \
                    and isinstance(node.args[0], ast.Constant) and isinstance(node.args[0].value, str):
                out.add(node.args[0].value)
    # the Operator tiles: _tile(group, key, LABEL, value, UNIT, ..., hint=HINT) translates its label, unit,
    # a value that is a word ("open", "wrapped") and its hint
    tree = ast.parse((PKG / "operator_view.py").read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "_tile":
            parts = [a for i, a in enumerate(node.args) if i in (2, 3, 4)] + [k.value for k in node.keywords if k.arg == "hint"]
            for part in parts:
                words = [part.body, part.orelse] if isinstance(part, ast.IfExp) else [part]   # "open" if ... else "closed"
                out |= {n.value for n in words if isinstance(n, ast.Constant) and isinstance(n.value, str)
                        and re.search(r"[a-z]{2}", n.value) and n.value not in ("kg", "mm/h", "m/s")}
    from permtek5_twin_edge import disturbances, film, habitat, keeper, operator_view, scenarios
    out.add(habitat.WATER_ASSUMED)                    # said in the Setup screen's "assumed" line, translated by the page
    out |= set(keeper.TEXT.values())
    out |= {t for _, t in operator_view.GROUPS} | set(operator_view.RULES.values())
    for info in operator_view.SENSORS.values():
        for k, v in info.items():
            if k != "same":
                out |= set(v) if isinstance(v, list) else {v}
    for d in disturbances.CATALOGUE.values():
        out |= {d["title"], d["about"]}
    out |= set(film.SHOTS.values()) | set(film.QUALITY.values())
    for f in (PKG / "scenarios.py",):        # the reasons a scenario cannot run: plain returns in available()
        tree = ast.parse(f.read_text(encoding="utf-8"))
        for fn in (n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == "available"):
            out |= {n.value.value for n in ast.walk(fn) if isinstance(n, ast.Return) and isinstance(n.value, ast.Constant)
                    and isinstance(n.value.value, str)}
    return out


class _Text(html.parser.HTMLParser):
    ATTRS = ("title", "aria-label", "alt", "placeholder")

    def __init__(self):
        super().__init__()
        self.found, self.skip = set(), 0

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style"):
            self.skip += 1
        for k, v in attrs:
            if k in self.ATTRS and v and "{" not in v:
                self.found.add(" ".join(v.split()))

    def handle_endtag(self, tag):
        if tag in ("script", "style"):
            self.skip -= 1

    def handle_data(self, data):
        text = " ".join(data.split())
        if not self.skip and text and re.search(r"[A-Za-z]{2}", text) and "{" not in text and "%}" not in text:
            self.found.add(text)


def from_page():
    src = (PKG / "templates" / "index.html").read_text(encoding="utf-8")
    src = re.sub(r"\{%.*?%\}|\{\{.*?\}\}|\{#.*?#\}", " ", src, flags=re.S)      # Jinja out
    p = _Text()
    p.feed(src)
    out = {t for t in p.found if not re.fullmatch(r"[A-Z]{2}", t)}                # the EN/DE/FR buttons stay
    js = (PKG / "static" / "app.js").read_text(encoding="utf-8")
    for m in re.finditer(r"(?<![\w.$])tr\(", js):
        depth, i = 1, m.end()
        while depth and i < len(js):
            depth += {"(": 1, ")": -1}.get(js[i], 0)
            i += 1
        call = js[m.end():i - 1]
        call = re.sub(r"\{[^{}]*\}\s*$", "", call)                               # the values object
        out |= {s.encode().decode("unicode_escape").encode("latin-1").decode("utf-8") for s in re.findall(r'"((?:[^"\\]|\\.)*)"', call) if re.search(r"[A-Za-z]", s)}
    care = re.search(r"const CARE_WHY = \{(.*?)\};", js, re.S)
    if care:
        out |= set(re.findall(r':\s*"([^"]+)"', care.group(1)))
    return out | set(WORDS)


# the template's own scripts (core.js, edge.js) are not edited: the page translates their fixed texts as they
# appear (app.js, translateDom), so they are listed here; texts with a value in them stay English
TEMPLATE_SKIP = {"D-LAB-5", "DOMContentLoaded", "Stop/Restart the tool?", "Ask an admin of your tenant to approve code ",
                 "An agent starts its own copy of the MCP server, so there is nothing to "}


def from_template():
    out = set()
    for f in (PKG / "core" / "static" / "core.js", PKG / "edge" / "static" / "edge.js"):
        js = f.read_text(encoding="utf-8")
        found = re.findall(r'"([A-Z][^"<>]{3,})"', js) + re.findall(r">([A-Z][^<>`$]{3,})<", js)
        out |= {t for t in found if t not in TEMPLATE_SKIP and not re.search(r"GET|POST|Content-Type|application/|\\$\\{", t)}
    return out


def from_scenarios():
    out = set()
    for f in sorted((PKG / "scenarios").glob("*.json")):
        s = json.loads(f.read_text(encoding="utf-8"))
        out |= {s["title"], s["about"]}
        for step in s["steps"]:
            out |= {step["title"], step["why"]}
            if step.get("do", {}).get("label"):
                out.add(step["do"]["label"])
            out |= {e["say"] for e in step.get("expect") or []}
    return out


def placeholders(text):
    return sorted(re.findall(r"\{(\w+)(?:[:!][^}]*)?\}", text))


def main():
    ap = argparse.ArgumentParser(description=__doc__.strip().splitlines()[0])
    ap.add_argument("--missing-json", help="write the texts a catalog lacks to this file")
    args = ap.parse_args()
    texts = {t for t in from_python() | from_page() | from_scenarios() | from_template() if t and t.strip()}
    bad, missing = [], {}
    for lang in ("de", "fr"):
        cat = json.loads((PKG / "i18n" / f"{lang}.json").read_text(encoding="utf-8"))
        missing[lang] = sorted(t for t in texts if t not in cat)
        bad += [f"{lang}: placeholders differ in {k!r}" for k, v in cat.items() if placeholders(k) != placeholders(v)]
        unused = sorted(k for k in cat if k not in texts)
        if unused:
            print(f"{lang}: {len(unused)} unused (no longer in code or page): " + "; ".join(unused[:5]) + (" …" if len(unused) > 5 else ""))
    print(f"{len(texts)} texts; missing: " + ", ".join(f"{l} {len(m)}" for l, m in missing.items()))
    for b in bad:
        print("  ! " + b)
    if args.missing_json:
        Path(args.missing_json).write_text(json.dumps(sorted(set(missing["de"]) | set(missing["fr"])), ensure_ascii=False, indent=1), encoding="utf-8")
    return 1 if bad or any(missing.values()) else 0


if __name__ == "__main__":
    sys.exit(main())
