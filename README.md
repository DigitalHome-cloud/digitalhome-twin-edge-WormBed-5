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
| `assets/build.py` | optional looks made with Blender (`bpy`): plants, raked soil, the gantry's profile, trees, the worm box |
| `permtek5_twin_edge/edge/` | the D-LAB-5 edge layer: link to PermTek-5 (device flow), A-Box sync, chat with proposals |

**The simulation needs** ROS 2 (Kilted) with `webots_ros2_driver`, Webots
R2025a, and a source checkout (the ROS packages are built in `ros2/`):

```bash
(cd ros2 && source /opt/ros/kilted/setup.bash && colcon build --symlink-install)
./run.sh                                  # then: Prepare world, Start, Confirm a job
.venv/bin/python scripts/selftest-twin.py # the whole loop on CityDemo, end to end
```

**One edge runs one Perma5Guild** (PermTek-5 ADR 0020): an area's working
unit of bed, worm box, tank, sensors and robot. The first guild type is
**WormBed5**, worked by a **Gantry5-gen1**. For a second guild, start the same
software again with its own port; folder, gateway, Webots port and ROS domain
follow from it, so the two simulations cannot disturb each other:

```bash
./run.sh                                      # the habitat's first guild, on 8772
./run.sh --port 8782 --guild "Balcony guild"  # another guild, beside it
```

The bed is simulated **as a system** (PermTek-5 `docs/specs/roofbed-system.md`):
tank pipes under the soil keep it moist from below while they hold water, a
worm box takes the bed's far end, and the gantry feeds it from a hopper a
person fills (`feed_worms`). The page shows the tank, the worm box and the
hopper, and has the person's two buttons: *Hopper filled*, *Tank refilled*.

The page says what is missing. Without a linked habitat it runs **CityDemo**,
an invented roof bed and three fruit trees.

**Shapes come from a library, not from Blender at run time.** The plants, the
raked soil, the gantry's profile, the trees and the worm box are mesh files in
`permtek5_twin_edge/shapes/` (listed in `shapes/library.json`). They ship with
the app; Webots loads them, and nothing is built when the twin runs.

| Blender does | When | Who |
|---|---|---|
| the shape library | once, and again when a shape is changed or added | a developer |
| the film | when you ask for it on the Film tab | you |

Blender is needed only for those two. `bpy` on PyPI is built for one Python
version (3.13 today) and ROS 2 needs the system's 3.12, so it has an
environment of its own:

```bash
scripts/setup-bpy.sh                      # once: uv, Python 3.13, bpy (about 400 MB)
.venv-bpy/bin/python assets/build.py      # rewrites permtek5_twin_edge/shapes/ after a change to a shape
```

The **Film** tab renders the prepared bed as a short turntable with Blender
(`assets/demo.py`, Cycles on the CPU: about 12 s a frame on an 8-thread
laptop, so 72 frames take a quarter of an hour; `ffmpeg` makes the MP4):

```bash
.venv-bpy/bin/python assets/demo.py --still-only   # one still, half a minute
.venv-bpy/bin/python assets/demo.py                # poster.png and film.mp4 in mydocs/twin/demo/
```

**The worm box** is two bins: a working bin above the soil on a sump that
catches the liquid. Its sensors (two temperatures, moisture, weight, gas,
acidity, sump level, lid) read with noise and can fail. Around it:

| Part | What it does |
|---|---|
| `wormbox.py` | the box as a living model: eating, heat, air, acidity, health, the sump, a resting half for harvest |
| `feeder.py` | how much to feed, from the box's weight |
| `feeding_guide.py` | the WormBed5 feeding guide (PermTek-5's data), enforced: what, how much, where, when |
| `keeper.py` | the rules that hold jobs, lock the gantry out and tell the person what to do |
| jobs | `feed_worms`, `care_box` (dry bedding, water), `top_dress` (finished compost back to cells); the sump's tap and the resting half are switches |

The robot does the routine part (feeding, bedding, water, draining, compost
back to the bed); a person fills the hopper's two compartments, says what
went in, wraps the box against frost, and confirms every job.

A habitat whose bed has no worm box or tank on the map still gets them here: a
WormBed5 guild always has both, and the page says what was assumed.

**How it works** is a drawing behind the rail's link (`/how`), made with
Archify from `docs/how-it-works.architecture.json`:

```bash
node ~/.claude/skills/archify/bin/archify.mjs deliver architecture docs/how-it-works.architecture.json \
     permtek5_twin_edge/static/how.html --quality showcase --json
```

The page uses the D-LAB-5 app shell (as the Mediathek edge does) in the
colours of PermTek-5.

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
