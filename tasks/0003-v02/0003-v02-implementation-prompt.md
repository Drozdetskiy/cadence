# rlx v0.2 -- Task Execution Mode (`--task`)

## Что такое rlx

Python CLI-инструмент для автономного выполнения задач через Claude Code. v0.1 реализует создание плана (`rlx --plan <file>`). v0.2 добавляет выполнение задач по плану (`rlx --task <file>`).

## Технические решения

Те же что в v0.1:
- Python 3.14+, pdm, typer, rich, tomllib
- Sync + threading (без asyncio)
- Protocol-based interfaces
- Embedded ресурсы через importlib.resources
- strict mypy

## Справочные документы

Детальная спецификация каждого модуля лежит в `docs/reference/`. Перед созданием плана ОБЯЗАТЕЛЬНО прочитай все документы (описание документов в `tasks/0002-v01/0002-v01-implementation-prompt.md`).

Эти документы -- ИСЧЕРПЫВАЮЩАЯ спецификация. Код должен строго соответствовать спецификации.

## Scope v0.2

Команда `rlx --task <file>` -- выполнение задач по план-файлу. Читает markdown-план, создаёт git-ветку, итеративно выполняет задачи через Claude, отслеживает прогресс через checkboxes.

### Что входит в v0.2

1. **Plan module** (`src/rlx/plan/`): парсинг markdown-планов (Task/Checkbox/Plan типы), выбор плана (numbered picker), извлечение имени ветки
2. **Git module расширение** (`src/rlx/git/`): полный git Service с Backend -- ветки, коммиты, diff stats, перемещение плана в completed/
3. **Runner расширение**: `run_task_phase()` loop, `run_with_session_timeout()`, break/pause (Ctrl+\), `has_uncompleted_tasks()`, `next_plan_task_position()`, `sleep_with_cancel()`
4. **Task prompt**: встроенный `task.txt` + `build_task_prompt()` в prompts.py
5. **CLI wiring**: `run_task_mode()` -- plan selection, branch creation, task execution, post-success stats
6. **Тесты**: pytest для каждого нового/изменённого модуля

### Что НЕ входит в v0.2

- `--review` (review pipeline) -- это v0.3
- Агенты ревью (5+2) -- это v0.3
- review_first.txt, review_second.txt, finalize.txt промпты -- это v0.3
- `run_claude_review()`, `run_claude_review_loop()`, `run_finalize()` -- это v0.3
- Agent system (frontmatter, loading, expand_agent_references) -- это v0.3
- Stalemate detection (review_patience) -- это v0.3
- `replace_prompt_variables()` с агентами -- в v0.2 достаточно `replace_base_variables()` + commit trailer
