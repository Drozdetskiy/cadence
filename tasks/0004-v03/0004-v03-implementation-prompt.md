# rlx v0.3 -- Review Pipeline Mode (`--review`, полный pipeline для `--task`)

## Что такое rlx

Python CLI-инструмент для автономного выполнения задач через Claude Code. v0.1 реализует создание плана (`rlx --plan <file>`), v0.2 -- выполнение задач (`rlx --task <file>`). v0.3 добавляет ревью-пайплайн: `rlx --review` (только ревью текущей ветки) и полный pipeline `--task` (tasks -> review_first -> review_loop -> finalize).

## Технические решения

Те же что в v0.1/v0.2:
- Python 3.14+, pdm, typer, rich, tomllib
- Sync + threading (без asyncio)
- Protocol-based interfaces
- Embedded ресурсы через importlib.resources
- strict mypy

## Справочные документы

Детальная спецификация каждого модуля лежит в `docs/reference/`. Перед созданием плана ОБЯЗАТЕЛЬНО прочитай все документы (описание документов в `tasks/0002-v01/0002-v01-implementation-prompt.md`).

Особое внимание:
- `docs/reference/04-processor.md` -- секции про run_full(), run_review_only(), run_claude_review(), run_claude_review_loop(), run_finalize(), last_session_timed_out, diff_fingerprint
- `docs/reference/08-prompts.md` -- полные тексты review_first.txt, review_second.txt, finalize.txt
- `docs/reference/09-agents.md` -- полные тексты всех 5 агентов, frontmatter, expand_agent_references(), format_agent_expansion()

Эти документы -- ИСЧЕРПЫВАЮЩАЯ спецификация. Код должен строго соответствовать спецификации.

## Scope v0.3

Режим `rlx --review` (review-only текущей ветки) и расширение `rlx --task <file>` до полного pipeline (tasks -> review_first -> review_loop -> finalize). Добавляется система агентов и три новых промпта.

### Что входит в v0.3

1. **Agents module** (`src/rlx/agents/` или `src/rlx/processor/agents.py`):
   - Загрузка агентов: local `.rlx/agents/<name>.txt` -> embedded `defaults/agents/<name>.txt`
   - Парсинг YAML frontmatter: `model` (haiku/sonnet/opus + нормализация длинных ID), `agent` (тип субагента Task tool, default `general-purpose`)
   - Отделение frontmatter от тела агента
   - Невалидные model значения -- отбрасываются с warning, используются defaults

2. **Prompts module расширение** (`src/rlx/processor/prompts.py`):
   - `expand_agent_references(prompt, app_cfg, local_dir)` -- regex `\{\{agent:([a-zA-Z0-9_-]+)\}\}`, для каждого match: загрузить агента, раскрыть base-переменные внутри тела (без рекурсии), форматировать через `format_agent_expansion()`
   - `format_agent_expansion(prompt, opts)` -- "Use the Task tool[ with model=X] to launch a <subagent-type> agent with this prompt: \"<text>\"\n\nReport findings only - no positive observations."
   - `replace_prompt_variables(prompt, ...)` -- полная замена: `replace_base_variables()` + `expand_agent_references()` + `append_commit_trailer_instruction()` (trailer добавляется ОДИН РАЗ на финальном собранном промпте)
   - `build_review_first_prompt()`, `build_review_second_prompt()`, `build_finalize_prompt()` -- build-функции для review/finalize промптов

3. **Runner расширение** (`src/rlx/processor/runner.py`):
   - `run_full()` -- переопределить: tasks -> review_first -> review_loop -> finalize (сейчас только task phase)
   - `run_review_only()` -- review_first -> review_loop -> finalize (без task phase)
   - `run_claude_review(prompt)` -- одиночный pass, обработка FAILED/REVIEW_DONE/no-signal warning
   - `run_claude_review_loop()` -- итеративный loop с ReviewSecond, no-commit detection через head_hash, skip HEAD-check при last_session_timed_out, max_iterations = max(3, max_iterations // 10)
   - `run_finalize()` -- best-effort семантика: единственное исключение propagate -- cancellation (KeyboardInterrupt), всё остальное логируется без raise; гейт через `finalize_enabled`
   - Отдельный `review_claude` executor: если `review_model` != `claude_model`, создать второй ClaudeExecutor; иначе использовать один и тот же
   - `Executors` dataclass или альтернативный способ группировки

4. **Git module расширение** (`src/rlx/git/`):
   - `GitChecker.diff_fingerprint()` -- хеш рабочего дерева diff (используется для stalemate/no-change detection если потребуется; head_hash уже есть в v0.2)
   - Убедиться что Service реализует оба метода GitChecker протокола

5. **Status module расширение** (`src/rlx/status.py`):
   - `SignalReviewDone = "<<<RLX:REVIEW_DONE>>>"` (если ещё нет)
   - `is_review_done(signal)` helper в `processor/signals.py`
   - `new_claude_review_section(n, suffix)` -- уже есть, использовать как есть

6. **Новые промпты** (`src/rlx/defaults/prompts/`):
   - `review_first.txt` -- 5 агентов (quality, implementation, testing, simplification, documentation), сигналы REVIEW_DONE/TASK_FAILED
   - `review_second.txt` -- 2 агента (quality, implementation), critical/major only
   - `finalize.txt` -- best-effort rebase + squash + verify, без сигналов

   Тексты ВЗЯТЬ as-is из `docs/reference/08-prompts.md` (полные тексты в каждой секции).

7. **Новые агенты** (`src/rlx/defaults/agents/`):
   - `quality.txt` -- correctness, security, simplicity
   - `implementation.txt` -- requirement coverage, correctness of approach, wiring
   - `testing.txt` -- coverage, test quality, fake test detection
   - `simplification.txt` -- over-engineering detection
   - `documentation.txt` -- README/CLAUDE.md/plan files updates

   Тексты ВЗЯТЬ as-is из `docs/reference/09-agents.md` (полные тексты в каждой секции). Frontmatter по умолчанию не требуется (model=default, agent=general-purpose).

8. **CLI расширение** (`src/rlx/cli.py`):
   - `run_review_mode()` -- без plan_file, без branch creation, только review phases + finalize; использует текущую ветку и default_branch для diff
   - `run_task_mode()` -- переключить Mode.FULL на полный pipeline (сейчас делает только task phase); post-success: diff_stats + move_plan_to_completed + display_stats остаются
   - Удалить заглушку "error: --review mode not implemented in v0.1"
   - Валидация: `--review` несовместим с `--impl`

9. **Тесты**:
   - pytest для agents loading (local/embedded fallback, frontmatter parsing, невалидные model значения)
   - pytest для expand_agent_references (match/miss/recursion protection)
   - pytest для run_claude_review (FAILED/REVIEW_DONE/no-signal paths)
   - pytest для run_claude_review_loop (iteration limits, no-commit detection, last_session_timed_out skip)
   - pytest для run_finalize (best-effort: errors swallowed except KeyboardInterrupt, disabled by default)
   - pytest для build_review_first_prompt / build_review_second_prompt / build_finalize_prompt (агенты раскрыты, trailer ОДИН РАЗ)
   - pytest для determine_mode + CLI в Mode.REVIEW (не требует plan_file)
   - Моки: `Executor`, `GitChecker` protocols; не запускать реальный claude и не требовать реальных репо (через tmp_path)

### Что НЕ входит в v0.3

Ничего сверх того, что описано в справочных документах. v0.3 -- финальная версия согласно скоупу проекта.

Явные non-goals (не реализовывать даже если встретится в reference-документах за рамками rlx):
- Web dashboard / SSE
- Notifications
- Global config (`~/.config/`)
- Codex / custom executors (только ClaudeExecutor)
- Git worktree
- Запуск реального `claude` в тестах

## Ключевые контракты

### Сигналы

```
<<<RLX:REVIEW_DONE>>>         -- ревью завершено, проблем не найдено
<<<RLX:TASK_FAILED>>>         -- проблемы не удалось исправить
(без сигнала)                 -- проблемы исправлены, нужна ещё итерация
```

### Agent expansion

- Regex: `\{\{agent:([a-zA-Z0-9_-]+)\}\}`
- Для каждого match: загрузка агента -> `replace_base_variables()` на теле -> `format_agent_expansion()`
- Рекурсия запрещена: `{{agent:X}}` внутри тела агента НЕ раскрывается
- Если агент не найден: warning в лог, reference оставляется as-is

### Frontmatter

```yaml
---
model: sonnet           # haiku | sonnet | opus; длинные ID нормализуются
agent: code-reviewer    # тип субагента Task tool; default "general-purpose"
---
```

Невалидные `model` -- warning + игнорировать поле.

### Review loop semantics

- REVIEW_DONE = "ZERO issues in this iteration" (не "I finished fixing")
- Нет сигнала + есть коммиты = "issues fixed, run another iteration"
- Нет сигнала + нет коммитов (HEAD unchanged) = "no changes detected, stop" (кроме last_session_timed_out)
- last_session_timed_out: skip HEAD-check, всегда продолжать

### Finalize semantics

- Гейт: `finalize_enabled` в конфиге (default false)
- Best-effort: все ошибки логируются, не propagate. Единственное исключение -- KeyboardInterrupt (user abort).
- Prompt: finalize.txt, без сигналов

### Commit trailer

- Добавляется ОДИН РАЗ на финальном собранном prompt (не внутри agent expansion)
- Если `commit_trailer` пустой: prompt unchanged

## Поток выполнения `rlx --review`

1. typer парсит --review
2. load_config + check_claude_dep + _ensure_git_repo
3. Service.open + set_commit_trailer + default_branch resolution
4. НЕТ: branch creation, plan_file resolution, move_plan_to_completed
5. Build logger (mode=review, без plan_file)
6. Build ClaudeExecutor (+ review_claude при разных моделях)
7. Runner.run() -> run_review_only():
   - PhaseReview: run_claude_review(ReviewFirstPrompt)
   - PhaseReview: run_claude_review_loop()
   - run_finalize() (если enabled)
8. Post-success: diff_stats против default_branch, display_stats (без move_plan)

## Поток выполнения `rlx --task` (full mode, обновлённый)

1. Всё как в v0.2 до runner.run()
2. Runner.run() -> run_full():
   - PhaseTask: run_task_phase() (как в v0.2)
   - PhaseReview: run_claude_review(ReviewFirstPrompt)
   - PhaseReview: run_claude_review_loop()
   - run_finalize() (если enabled)
3. Post-success: как в v0.2 (diff_stats + move_plan_to_completed + display_stats)

## Требования к валидации

- `pdm run pytest` -- все тесты проходят
- `pdm run ruff check src/ tests/` -- нет ошибок линтера
- `pdm run mypy src/` -- strict type checking проходит
- `rlx --version` -- выводит версию
- `rlx --review` -- запускается в git-репо, проходит review_first без падений (требует реальный claude CLI)
- `rlx --task <plan>` -- полный pipeline отрабатывает tasks -> review -> (finalize если включён)