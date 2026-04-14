# Review Findings

## Findings

1. High: `.claude/settings.json:7` wires the `Stop` hook to `codex exec "Review changes since last commit and write results in a file named planning/REVIEW.md"`. That spawned Codex run will hit the same `Stop` hook again when it exits, which creates an unbounded review-on-stop loop. At best this repeatedly rewrites `planning/REVIEW.md`; at worst it keeps spawning nested Codex processes until the session is interrupted.

2. Medium: `README.md:65`, `README.md:86`, and `README.md:122` now document a runnable full-stack app with `frontend/`, `scripts/`, `test/`, `Dockerfile`, `.env.example`, and multiple API endpoints, but those paths and entrypoints do not exist in the repository today. The current tree only contains the market-data backend under `backend/app/market` plus tests. As written, the README gives users commands they cannot run and a project structure they cannot find.

3. Medium: `planning/PLAN.md:87`, `planning/PLAN.md:93`, `planning/PLAN.md:116`, and `planning/PLAN.md:155` no longer match the code contract in `backend/app/market/interface.py`. The plan renames the package to `backend/app/market_data`, introduces `backend/schema` and `backend/app/db.py`, and says sources re-read the watchlist from the DB directly instead of using `add_ticker()` / `remove_ticker()`. The checked-in interface still lives at `backend/app/market/interface.py` and explicitly requires `add_ticker`, `remove_ticker`, and `get_tickers`. That mismatch is likely to send the next implementation pass down the wrong architecture.

## Open Questions

- Should the stop hook run a different command path that does not itself trigger the same hook, or should review generation be moved out of `Stop` entirely?
- Is `README.md` meant to describe the current repo state or the intended end-state? Right now it reads as current-state documentation.
