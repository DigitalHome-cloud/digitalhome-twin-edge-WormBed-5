# SPDX-License-Identifier: GPL-3.0-or-later
"""Finding a site's edge API from its address, without a network."""

from __future__ import annotations

import io
import json
import urllib.error

import pytest

from permtek5_twin_edge import cloudsite


def answering(body: bytes):
    class R(io.BytesIO):
        def __enter__(self): return self
        def __exit__(self, *a): return False
    seen = []
    def opener(req, timeout=0):
        seen.append(req.full_url)
        return R(body)
    return opener, seen


def test_presets_and_addresses():
    assert cloudsite.site_url("Stage") == "https://stage-permtek5.digitalhome.cloud"
    assert cloudsite.site_url("https://example.org/") == "https://example.org"
    for bad in ("", "http://insecure.example", "https://x.example/path", "javascript:alert(1)"):
        with pytest.raises(cloudsite.NotFound):
            cloudsite.site_url(bad)


def test_the_edge_api_comes_from_edge_json():
    opener, seen = answering(json.dumps({"name": "PermTek-5", "edgeApi": "https://abc123.execute-api.eu-central-1.amazonaws.com",
                                         "minEdgeVersion": "0.1.0", "environment": "stage"}).encode())
    found = cloudsite.discover("stage", opener)
    assert seen == ["https://stage-permtek5.digitalhome.cloud/edge.json"]
    assert found["edgeApi"].endswith("amazonaws.com") and found["minEdgeVersion"] == "0.1.0" and found["site"].endswith("digitalhome.cloud")


def test_a_site_without_edge_json_says_what_to_do():
    opener, _ = answering(b"<!doctype html><html>the page</html>")
    with pytest.raises(cloudsite.NotFound, match="About page"):
        cloudsite.discover("stage", opener)


def test_an_address_that_is_not_an_aws_api_is_refused():
    opener, _ = answering(json.dumps({"edgeApi": "https://evil.example/collect"}).encode())
    with pytest.raises(cloudsite.NotFound):
        cloudsite.discover("stage", opener)


def test_no_answer_is_reported_not_raised_raw():
    def opener(req, timeout=0):
        raise urllib.error.URLError("no route")
    with pytest.raises(cloudsite.NotFound, match="does not answer"):
        cloudsite.discover("prod", opener)
