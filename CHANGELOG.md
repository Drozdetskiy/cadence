# Changelog

## v0.19.2 - 2026-05-02

First public release. Available via `pip install cadence-runner` and `brew tap Drozdetskiy/cadence && brew install cadence`.

### New Features

- Three-mode CLI: `--plan`, `--task`, `--review`.
- `--impl` flag chains task execution after `--plan`.
- Multi-agent code review: 4 parallel agents (quality, implementation, testing, simplification) with review-loop until clean.
- Per-phase model configuration via `.cadence/config.yaml`.
- Configurable `commit_format` and `tasks_root` in `.cadence/config.yaml`.
- `--base` and `--config` CLI flags.
- Idle/session timeouts; break/resume via SIGINT/SIGQUIT.
