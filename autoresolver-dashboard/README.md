# Hermes Base

A Clash-of-Clans-style dashboard for the Hermes issue autoresolver's pull requests on
`NousResearch/hermes-agent`.

Live page: https://claude.ai/artifact/Ub9jptDU1wDUmpyWtemrdf

## What's here

| File | Purpose |
|---|---|
| `index.html` | The published page. It reads `snapshot/latest` and the monthly `feed/*` history from the artifact's database. |
| `refresh_snapshot.py` | Builds that document from GitHub with the `gh` CLI (stdlib only). |
| `seed-snapshot.json` | The snapshot currently loaded: all 212 PRs as of Sep 27, 2026, with every closure classified. |
| `history/*.json` | The battle-log archive, one file per month from May 31, stored in the page's `feed` collection. |

## How the base maps to the data

- **Agents (buildings)** are the areas of the codebase, taken from each PR's conventional-commit
  scope and then its `comp/*` label: Core Sage (agent loop, providers), Gateway Herald
  (gateway and platforms), Desktop Artisan (desktop, TUI, dashboard), CLI Smith (CLI, config,
  install), Tool Tinkerer (tools, skills, MCP) and Cron Keeper (cron, kanban).
- **Purple pill:** open PRs. **Gold pill:** PRs that landed, either merged or salvaged
  (cherry-picked into a maintainer PR).
- **Troops marching to the town hall:** open PRs with active review threads.
- **Reviewers at the gate:** everyone who commented or reviewed in the battle-log window.
- Hover or tap a building to see what that agent has worked on, what it is addressing and
  what is in review. Click to pin the dossier.

## Refreshing the data

On a machine where `gh auth status` succeeds:

```bash
python3 autoresolver-dashboard/refresh_snapshot.py --out snapshot.json
```

Then ask Claude to upload it, or paste this step at the end of the autoresolver's prompt. It
fetches the script from this branch, so it works whichever branch the run has checked out:

```text
Final step, after the issue work is done: refresh the Hermes Base dashboard.
1. Run:
   git fetch -q origin claude/fervent-darwin-lxs0ds && git show origin/claude/fervent-darwin-lxs0ds:autoresolver-dashboard/refresh_snapshot.py > /tmp/hermes_refresh_snapshot.py && python3 /tmp/hermes_refresh_snapshot.py --out /tmp/hermes_snapshot.json
2. If that succeeds, load the ArtifactData tool (use ToolSearch if it isn't loaded yet) and call it with
   action "set", url "https://claude.ai/artifact/Ub9jptDU1wDUmpyWtemrdf", collection "snapshot",
   doc_id "latest", file_path "/tmp/hermes_snapshot.json".
3. If either step fails, report the error in one line and finish normally. Never let the dashboard
   refresh block or undo the issue work.
```

The refresh only rewrites `snapshot/latest`; the monthly `feed/<YYYY-MM>` documents keep the
older history. The page merges both, preferring the refresh's exact-time events over the
archive's day-level entries for the same person, PR and day. To restore the archive, `set`
each `history/<YYYY-MM>.json` file as `feed/<YYYY-MM>`.

Open pages update as soon as the document changes. The page itself never needs republishing
for new data.

The script classifies closures from each PR's comments, then falls back to the
`duplicate`, `sweeper:implemented-on-main` and `sweeper:not-planned` labels. The loaded snapshot
also caught silent cherry-picks by matching your commits (same author and timestamp) on upstream
`main`; the script doesn't do that git step, so a refresh can report a few of those as plain
"Closed".

Flags: `--days` (battle-log window, default 14), `--feed-prs` (threads to read, default 30),
`--classify` (closed PRs to classify from comments, default 200).
