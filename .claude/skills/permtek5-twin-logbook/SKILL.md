---
name: permtek5-twin-logbook
description: Monitor a wormbed5.edge twin run through its logbook. Reads who did what and when (you, the autopilot, the robot, the plants, the keeper), reports what is new since the last look, and flags what needs a person: lost plants, failed or aborted jobs, keeper notices, holds and lockouts, an autopilot that switched itself off. Use when someone asks to watch, monitor, check on or summarise a running twin, a rehearsal or an unattended autopilot run of permtek5-twin-edge, or to read its logbook.
---

# Monitoring a twin run by its logbook

wormbed5.edge (http://127.0.0.1:8772) writes everything that happens in the
guild to a logbook, one numbered entry each, oldest first:

| who | what it writes |
|---|---|
| `person` | what a person did through the page or an agent's confirmed proposal: hopper, tank, taps, the worm box, care tasks, confirmed jobs, prepare/start/stop, scenarios, chaos; and a care task done in the cloud's site ("done (from the site): …", what `care.done`) |
| `autopilot` | the same acts, done by the autopilot in an unattended rehearsal; `off` when it stops and why; `error` when a look failed |
| `robot` | how each gantry job ended: `job.done`, `job.failed`, `job.aborted`, `job.cancelled`, `job.refused` |
| `plants` | `plant.up` (came up), `plant.ready` (ready to harvest), `plant.lost` (frost, dry, cold, did not come up), `plant.harvested` |
| `keeper` | `notice`: the keeper's notices as they appear |
| `edge` | the run moved on to another growing period |

Each entry: `{"n", "t" (wall clock), "sim" (the simulation's date and clock), "who", "what", "text"}`.

## Reading it

With the MCP server (`permtek5-twin-edge`):

- `logbook(after=N, who="", what="", limit=100)` → `{"entries", "last"}`. Pass the
  `last` you saw before as `after` to get only what is new.
- `twin()` for the state now: running, the period, the proposals, holds and lockout,
  the autopilot (`autopilot.on`, `acts`, `last`, `why`), the cells by status.

Without MCP: `curl -s "http://127.0.0.1:8772/api/logbook?after=N&limit=200"` and
`curl -s http://127.0.0.1:8772/api/twin`.

## A look

1. Read the new entries (`after` = the `last` of the previous look; 0 the first time) and `twin()`.
2. Report briefly, in the person's language:
   - **since when**: the simulation dates of the first and last new entry;
   - **counts**: robot jobs by outcome, plants come up / ready / lost / harvested, the autopilot's acts;
   - **needs a person**, first and each on its own line: every `plant.lost` (crop, where, why),
     `job.failed`/`job.aborted`, a `keeper` notice, `twin().keeper.holds` or a lockout,
     `autopilot` `off` with a reason other than "switched off", any `autopilot` `error`, the
     simulation not running;
   - **progress towards the target**: from `twin().byStatus` and `ready` (planned, sown, growing,
     ready, harvested, lost), and the period (`runPeriod`).
3. Say the new `last`, so the next look starts there.

Nothing new and nothing wrong is a one-line answer.

## Watching over time

For an unattended run, look every so often with `/loop` (e.g. `/loop 10m use the
permtek5-twin-logbook skill to look at the twin`) and carry `last` from one look to the
next. Stop watching when the run stopped and the last look reported it.

## Rules

- **Read only.** A look never confirms a job, never switches the autopilot, never
  stops or starts the twin. If something should be done, say what and let the person
  decide (in the chat panel such a change is a proposal they confirm).
- A rehearsal writes nothing into the habitat (PermTek-5 ADR 0029): a lost plant or a
  failed job in the logbook is the twin's, not the real bed's.
- Times: `t` is this PC's clock; `sim` is the simulation's own date, which runs faster.
  Report rates and counts against `sim`.
