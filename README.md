# permtek5-twin-edge

**v0.1.0** · GPL-3.0 · [releases](https://github.com/DigitalHome-cloud/permtek5-twin-edge/releases)

A local app that turns a [PermTek-5](https://github.com/DigitalHome-cloud/digitalhome-cloud-PermTek-5)
habitat into a **Webots twin** of its roof bed and gantry robot, runs it over
**ROS 2**, and shows the **robot's views** while it works. It is an edge of
PermTek-5 (permaculture on DigitalHome.Cloud): the habitat's map (beds, cells,
crops) is the plan, the robot proposes jobs, and nothing moves until a person
confirms. The same ROS 2 actions are meant to drive the real gantry later
(PermTek-5 `docs/specs/roofbed-robot.md`).

| Part | What |
|---|---|
| `permtek5_twin_edge/habitat.py` | reads a habitat's A-Box (Turtle) and the crop library; a cell as a seeder's job: x/y in mm from the bed's corner, depth, seeds, spacing (the cloud's rule) |
| `permtek5_twin_edge/world.py` | writes a self-contained Webots world of one bed and its gantry, and `bed.json` for the ROS side |
| `ros2/roofbed_interfaces` | actions `PlantCell`, `WaterCells`, `ProbeCells`, `PhotoPass` |
| `ros2/roofbed_twin` | the gantry's Webots driver (axes, two cameras, the bed's state), `bed_actions` (the jobs), a gateway on 127.0.0.1 for this app, the launch |
| `permtek5_twin_edge/sim.py`, `app.py` | start and stop the simulation; the page: robot views, the bed, proposed jobs with **Confirm** |
| `assets/build.py` | optional looks made with Blender (`bpy`): plants, raked soil, the gantry's profile, trees |
| `permtek5_twin_edge/edge/` | the D-LAB-5 edge layer: link to PermTek-5 (device flow), A-Box sync, chat with proposals |

**The simulation needs** ROS 2 (Kilted) with `webots_ros2_driver`, Webots
R2025a, and a source checkout (the ROS packages are built in `ros2/`):

```bash
(cd ros2 && source /opt/ros/kilted/setup.bash && colcon build --symlink-install)
./run.sh                                  # then: Prepare world, Start, Confirm a job
.venv/bin/python scripts/selftest-twin.py # the whole loop on CityDemo, end to end
```

The page says what is missing. Without a linked habitat it runs **CityDemo**,
an invented roof bed and three fruit trees.

**Blender meshes are optional.** `bpy` on PyPI is built for one Python version
(3.13 today) and ROS 2 needs the system's 3.12, so it has an environment of
its own and is used only to build mesh files:

```bash
scripts/setup-bpy.sh                      # once: uv, Python 3.13, bpy (about 400 MB)
.venv-bpy/bin/python assets/build.py      # writes mydocs/twin/assets/*.obj
```

Prepare the world again and it uses them for looks. Without them it draws
boxes and spheres; motion and jobs are the same either way.

**Private stays private.** A real habitat synced from the cloud, the worlds
generated from it, pictures and logs live in `mydocs/` (git-ignored), never in
this repository. `scripts/check-public.sh` runs before every commit.

## Use it

```bash
./run.sh                 # http://127.0.0.1:8772 (run.cmd or run.ps1 on Windows)
./run.sh --port 9000     # somewhere else
./run.sh --stop          # shut it down
```

The first run creates `.venv` and installs the requirements. Any other option
goes straight to the app, e.g. `./run.sh --no-update-check`.

The header has **About**, which shows the installed version, checks github.com
for a newer one and links to the repository. On a pip or pipx install it can
**update itself**: it runs pip against the latest tag, shows what pip said, and
restarts into the new version only if that worked. From a source checkout it
tells you to `git pull` instead. The check is one request for a text file. It
sends nothing about you, is cached for six hours, and is skipped silently when
there is no network. `--no-update-check` turns it off. **Restart** and **Stop**
are only accepted from this machine.

## Installing without a clone

**A release tarball**: needs neither git nor pip.

```bash
curl -L https://github.com/DigitalHome-cloud/permtek5-twin-edge/archive/refs/tags/v0.1.0.tar.gz | tar xz
cd permtek5-twin-edge-0.1.0
./run.sh
```

**Or install it**, with no source tree to keep:

```bash
pipx install git+https://github.com/DigitalHome-cloud/permtek5-twin-edge@v0.1.0
permtek5-twin-edge-web                 # the page
permtek5-twin-edge world              # the command line
permtek5-twin-edge-mcp                 # the MCP server, for agents
permtek5-twin-edge-skill --install     # the agent skill, into ~/.claude/skills
```

Every command has a `python3 -m` twin that needs nothing on PATH:

| command | the same thing as a module |
|---|---|
| `permtek5-twin-edge` | `python3 -m permtek5_twin_edge.engine` |
| `permtek5-twin-edge-web` | `python3 -m permtek5_twin_edge.app` |
| `permtek5-twin-edge-mcp` | `python3 -m permtek5_twin_edge.mcp_server` |
| `permtek5-twin-edge-skill` | `python3 -m permtek5_twin_edge.core.skill_install` |

## Updating an install

**From the page:** *About → Update and restart*.

**From the command line**, name the new tag. `--upgrade` alone will not move a
pinned tag:

```bash
pip install --upgrade git+https://github.com/DigitalHome-cloud/permtek5-twin-edge@v0.1.0
pipx install --force git+https://github.com/DigitalHome-cloud/permtek5-twin-edge@v0.1.0
```

If you copied the skill with `--install`, refresh it afterwards:
`permtek5-twin-edge-skill --install --force`.

## For agents

**As a Claude Code plugin** you get the skill and the MCP server in one step:

```
/plugin marketplace add ERP-LAB-5/python-tool-template
/plugin install permtek5-twin-edge@erp-lab-5
```

On first use the plugin installs the version it pins into its own venv under
`~/.local/share/dlab5-tools/permtek5-twin-edge/` and runs the MCP server from there.
It needs `python3` on PATH.

**From a checkout**, `.mcp.json` registers the MCP server for this repository.

**For any other agent, press Agent** in the header. It writes the JSON for
Claude Code, VS Code (GitHub Copilot), Cursor or Claude Desktop with the real
paths already filled in — each client keeps the same server under a slightly
different key, in a different file — and either copies it or writes the file
for you, keeping any other servers already registered there. The panel also
shows whether an agent is currently working on this server.

MCP itself is the standard, so the server is written once and every client
speaks to it unchanged; only that small config file differs between them. What
no page can do is *start* one: a stdio server is spawned by its client, lives on
that client's pipes and dies with it, so there is nothing to start or stop from
the browser.

The MCP server starts the web server if it is not running, and both work on the
same files, so an agent's save shows up for the person at the browser.

[**The skill**](permtek5_twin_edge/skill/SKILL.md) ships inside the package.
`permtek5-twin-edge-skill --install` copies it to `~/.claude/skills` for agents
that don't use the plugin, and `--install --for vscode` writes it as
`.github/copilot-instructions.md` instead (`--for cursor` for Cursor's rules
folder). A checkout has identical copies in
`.claude/skills/permtek5-twin-edge/` and `plugin/skills/permtek5-twin-edge/`. They are
real files rather than symlinks, because symlinks don't survive a clone on
Windows. `permtek5-twin-edge-skill --check` confirms they are in step, and
`--sync` rewrites them from the packaged copy.

## Development

```bash
./test.sh                # the suite, with ROS 2's PYTHONPATH kept out of it
```

This repository was generated from
[ERP-LAB-5/python-tool-template](https://github.com/ERP-LAB-5/python-tool-template).
`permtek5_twin_edge/core/`, the launchers, `test.sh`, `tests/test_core.py`,
`scripts/release.py` and `plugin/bin/launch_mcp.py` belong to the template. Take its fixes with
`copier update` rather than editing them here. Everything else belongs to this tool.

Releasing:

```bash
python3 scripts/release.py 0.2.0 -F notes.txt --push
```

It bumps `VERSION` everywhere it appears, updates this README's pins, syncs the
skill copies, commits, tags, pushes, and creates the GitHub release.

## Licence

GPL-3.0-or-later. © 2026 D-LAB-5 — *Twin. Experiment. Automate.*
See [LICENSE](LICENSE).
