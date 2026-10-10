# SPDX-License-Identifier: GPL-3.0-or-later
"""
settings.py — what makes this edge yours. THE TOOL OWNS THIS FILE: the edge
layer writes it once and never again (`copier update` leaves it alone).

Everything else in edge/ belongs to the layer and is replaced on update, so
put product decisions here, not there.
"""

from __future__ import annotations

from pathlib import Path
from typing import Callable, Dict, List, Tuple

# -- who this edge is ----------------------------------------------------------

TOOL = "permtek5-twin-edge"
PACKAGE = "permtek5_twin_edge"
TITLE = "PermTek-5 Twin Edge"
KIND = "robot"                 # human | iot | robot
CLOUD_NAME = "PermTek-5"          # "the Mediathek", "DigitalHome" …
TENANT_NOUN = "site"        # what people call a tenant here
SPACE_NOUN = "area"          # … and a space
ENV = "PERMTEK5_TWIN_EDGE"                 # PERMTEK5_TWIN_EDGE_EDGE_STATE, PERMTEK5_TWIN_EDGE_CHAT_CONFIRM …

# The client id and scope this edge asks for when it pairs (DH-SPEC-100).
CLIENT_ID = TOOL
SCOPE = "edge.link edge.telemetry graphs.write"

# -- knowledge (the A-Boxes, ADR-0007 in the cloud) ---------------------------

# Copy the cloud's packages/ontology here (tbox/*.ttl, shapes/*.ttl): this
# edge validates a merged A-Box with the same shapes the cloud does.
ONTOLOGY = Path(__file__).resolve().parent / "ontology"

# Facts a thing has at most one of. Two different values from two sides are
# a CONFLICT the person resolves, instead of both being kept.
SINGLE_VALUED: List[str] = [
    "https://schema.org/name",
    # a planting cell has one status, one sowing date, one crop, one place
    "https://permaculture.digitalhome.cloud/ontology#cellStatus",
    "https://permaculture.digitalhome.cloud/ontology#sownOn",
    "https://permaculture.digitalhome.cloud/ontology#grows",
    "https://permaculture.digitalhome.cloud/ontology#row",
    "https://permaculture.digitalhome.cloud/ontology#col",
    "https://permaculture.digitalhome.cloud/ontology#inBed",
]
# Facts that are single per LANGUAGE (one label per language).
PER_LANGUAGE: List[str] = [
    "http://www.w3.org/2004/02/skos/core#prefLabel",
]

# -- the chat (a local Claude Code with only this edge's MCP tools) -----------

# What a proposal from the chat may run on this edge's own API: (method, path
# regex). Nothing else can be proposed. Add the tool's own routes here.
CHAT_ALLOWED: List[Tuple[str, str]] = [
    ("POST", r"/api/jobs/[\w-]{1,40}/cancel"),
    ("POST", r"/api/cloud/link"),
    ("POST", r"/api/cloud/use"),
    ("POST", r"/api/knowledge/sync"),
    ("POST", r"/api/ros2/publish"),
    ("POST", r"/api/twin/(prepare|start|stop|job|cancel|film|hopper|tank|disturb|calm|time|chaos)"),
    ("POST", r"/api/twin/scenario(/do|/next|/stop)?"),
]

CHAT_SYSTEM = """You are the chat of {title}, an edge of {cloud}: a local
tool on this computer. Use the {tool} tools to answer; do not guess numbers.
Be brief and friendly, and answer in the language the person writes in (the
page is in {{lang}}).

Changes are proposals: a tool that changes something answers "proposed". The
person sees a Confirm button for it. Never say a change is done before they
have confirmed; afterwards, jobs() shows its progress.

This edge runs a Webots twin of a gantry robot over the site's roof bed. twin()
shows it; sowing, watering, probing and photographing cells are jobs the
person confirms, one at a time. Cells are row, column counted from 1.

Privacy: what this edge keeps private never reaches you. Names you see are
the {tenant}'s; use them only to answer.""".format(
    title=TITLE, cloud=CLOUD_NAME, tool=TOOL, tenant=TENANT_NOUN)

# -- first start ---------------------------------------------------------------

# Steps a new edge runs once (at start, and again after linking) until each
# has worked: fetch models, pull what the cloud already knows … Each is
# (name, needed() -> bool, run(progress, cancelled) -> dict). The layer adds
# "site knowledge" (every space's A-Box) itself.
FIRST_START: List[Tuple[str, Callable[[], bool], Callable[..., Dict]]] = []
