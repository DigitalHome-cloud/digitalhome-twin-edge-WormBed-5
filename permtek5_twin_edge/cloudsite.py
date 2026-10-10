# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 D-LAB-5
"""
cloudsite.py — find a PermTek-5 site's edge API from the site's own address.

Linking needs the edge API's address, an AWS URL nobody remembers. A PermTek-5
site publishes it at /edge.json (with its minimum edge version), so a person
picks "Stage" or "Production", or types the site's address, and the edge
looks the rest up. Standard library only; one small GET, nothing is sent.
"""

from __future__ import annotations

import json
import re
import urllib.error
import urllib.request
from typing import Any, Dict

SITES: Dict[str, str] = {
    "stage": "https://stage-permtek5.digitalhome.cloud",
    "prod": "https://permtek5.digitalhome.cloud",
}
_HOST = re.compile(r"^https://[a-z0-9.-]+(:\d+)?$")
_API = re.compile(r"^https://[a-z0-9.-]+\.amazonaws\.com(/[\w/-]*)?$")


class NotFound(Exception):
    """The site could not say where its edge API is; the message says what to do."""


def site_url(site: str) -> str:
    """A preset's name or an https address, as the site's base URL."""
    url = SITES.get(site.strip().lower(), site.strip().rstrip("/"))
    if not _HOST.fullmatch(url):
        raise NotFound("say 'stage', 'prod' or the site's https address, e.g. https://stage-permtek5.digitalhome.cloud")
    return url


def discover(site: str, opener=urllib.request.urlopen) -> Dict[str, Any]:
    """{site, edgeApi, minEdgeVersion, environment} of a PermTek-5 site."""
    base = site_url(site)
    req = urllib.request.Request(f"{base}/edge.json", headers={"Accept": "application/json"})
    by_hand = "paste the edge API address from its About page instead"
    try:
        with opener(req, timeout=8) as r:
            data = json.loads(r.read(20_000))
    except (urllib.error.URLError, OSError) as exc:
        raise NotFound(f"{base} does not answer ({getattr(exc, 'reason', exc)}); {by_hand}") from None
    except ValueError:
        # a site built before /edge.json existed answers with its page
        raise NotFound(f"{base} does not publish its edge address yet (no /edge.json); {by_hand}") from None
    api = str(data.get("edgeApi") or "") if isinstance(data, dict) else ""
    if not _API.fullmatch(api):
        raise NotFound(f"{base}/edge.json names no edge API; {by_hand}")
    return {"site": base, "edgeApi": api, "minEdgeVersion": data.get("minEdgeVersion"),
            "environment": data.get("environment"), "name": data.get("name") or "PermTek-5"}
