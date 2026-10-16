---
name: permtek5-twin-edge
description: wormbed5.edge, a Webots twin of a gantry robot over a habitat's roof bed (PermTek-5, permaculture on DigitalHome.Cloud). Check a habitat's Turtle, write its Webots world, start and stop the simulation over ROS 2, watch the robot's camera views, and confirm jobs that sow, water, probe and photograph the bed's cells. Use when someone asks to simulate the roof bed or the gantry, to work with permtek5-twin-edge, or runs `permtek5-twin-edge`.
---

# wormbed5.edge

A local app (http://127.0.0.1:8772) that turns a PermTek-5 habitat into a
Webots world of one growing bed and its gantry robot, runs it over ROS 2 and
shows the robot's views. It is an edge of PermTek-5: it can link to the cloud
and sync the habitat's A-Box. Without a link it runs the invented CityDemo.

## What it works on

- A **habitat** is an A-Box in Turtle: zones, growing beds split into square
  cells, planting cells (crop, status), garden plants (PermTek-5 ADR 0016).
- A cell's place for the robot: bed coordinates in mm from the corner of
  row 1 / column 1, x along the length (columns), y across the width (rows),
  the cell's centre; depth below the soil; seeds per point; spacing.
- The world (`roofbed.wbt`) and `bed.json` are **outputs** in the workspace
  (`./mydocs/twin`, git-ignored). Never edit them; prepare again.

## Working through MCP

- `twin()`: prepared, running, what is missing, the gantry's position, the
  running job, the last jobs, and the **proposed jobs** with their ids.
- `check_habitat(ttl)`: what stops a habitat from being simulated.
- `twin_prepare()`, `twin_start(window)`, `twin_stop()`.
- `twin_confirm(proposal)`: send one proposed job (`plant-5_13`, `water`,
  `probe`, `photo`). In the chat panel this is a proposal the person confirms.
- `twin_estop(on)`: hold the gantry now. Always allowed.
- `twin_autopilot(on)`: in a rehearsal, the autopilot does a person's part and
  confirms the robot's jobs one at a time, so a run goes on unattended.
- `logbook(after, who, what, limit)`: who did what and when. To watch a run,
  use the `permtek5-twin-logbook` skill.

## Without MCP

```bash
permtek5-twin-edge check habitat.ttl
permtek5-twin-edge world habitat.ttl
permtek5-twin-edge jobs
```

Without a file, CityDemo is used. From a checkout, without installing:
`python3 -m permtek5_twin_edge.engine world`. The page is `./run.sh`, or
`permtek5-twin-edge-web` when installed. The simulation needs ROS 2, Webots
and the built `ros2/` workspace of a source checkout; the page says what is
missing. `scripts/selftest-twin.py` runs the whole loop end to end.

## Judgement

- The robot proposes, a person confirms. Never report a job as done before
  `twin()` shows it in the done list; a failed job says why (e.g. a cell
  outside the bed is refused by the soft limit).
- One job at a time. A second one is refused while one runs.
- Whether it is the right month to sow is PermTek-5's calendar; this tool
  only says what the gantry would do at a cell.
- A real habitat synced from the cloud is private: keep its data out of the
  repository and out of answers that leave this machine.
