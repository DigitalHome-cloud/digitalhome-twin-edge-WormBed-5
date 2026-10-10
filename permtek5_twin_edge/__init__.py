# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 D-LAB-5
"""permtek5-twin-edge — PermTek-5 Twin Edge

    engine      the engine and its command line, standard library only
    app         the browser front end (Flask), on top of core.server
    mcp_server  MCP tools over stdio, talking to the same web server
    core/       shared with every D-LAB-5 tool; `copier update` maintains it

Keeping everything in a package rather than loose at the repository root is
what lets `pip install` carry the templates, static files, VERSION and the
agent skill along.
"""

from .core.identity import REPO_URL  # noqa: F401
from .core.version import __version__  # noqa: F401
