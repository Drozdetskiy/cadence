# Проектные решения rlx

Дата: 2026-04-16

## Что такое rlx

Python-порт ralphex с упрощённым дизайном. CLI-инструмент для автономного выполнения задач через Claude Code: создание планов, выполнение задач по плану, code review.

---

## Принятые решения

### 1. Имя — `rlx` везде
- CLI entrypoint: `rlx`
- Config dir: `.rlx/`
- Сигналы: `<<<RLX:ALL_TASKS_DONE>>>`, `<<<RLX:TASK_FAILED>>>`, `<<<RLX:REVIEW_DONE>>>` и т.д.
- Env vars: `RLX_*`
- Обратная совместимость с ralphex НЕ нужна — это новый проект

### 2. Формат конфига — TOML
- Чтение: `tomllib` (stdlib)
- Файл: `.rlx/config.toml`

### 3. Python 3.14+
- Используем все современные фичи: type hints, match/case, etc.

### 4. Sync + threading (без asyncio)
- Основной цикл последовательный: запустить процесс -> читать stdout -> проанализировать -> следующий шаг
- `threading.Timer` для idle timeout
- `signal.signal()` для SIGINT/SIGQUIT в main thread

### 5. Без fzf
- Выбор из вариантов: нумерованный список (input() с валидацией)

### 6. Без git worktree
- rlx работает только в текущей ветке

### 7. Без внешнего ревью (codex + custom scripts)
- Единственный executor: ClaudeExecutor
- Review pipeline: только Claude (review_first + review_second)

### 8. Package manager — pdm

### 9. Загрузка промптов (2 уровня)
- **Системные** — вшиты в пакет rlx (`importlib.resources`)
- **Локальные** — `.rlx/prompts/<name>.txt`, опционально
- Локальный файл полностью заменяет системный (не merge)
- Та же схема для агентов: `.rlx/agents/<name>.txt`

### 10. Загрузка конфига (2 уровня + CLI)
- Приоритет: CLI флаги > `.rlx/config.toml` > дефолты в коде
- Локальный конфиг накладывается на дефолты (per-field merge)
- TOML: отсутствующий ключ = не указан, дефолт остаётся

### 11. Без notifications

### 12. Без web dashboard

### 13. Установка и зависимости
- Установка: `pip install -e .` (разработка), позже `pip install rlx` (PyPI)
- Entrypoint: `rlx` через `pyproject.toml [project.scripts]`
- Зависимости: **typer** (CLI) + **rich** (цвета, markdown)
- Всё остальное — stdlib

### 14. Минимальный CLI
```
rlx --plan <path>      # создать план из prompt-файла
rlx --task <path>      # выполнить план
rlx --review           # review текущей ветки (v0.3)
rlx --version          # версия
```

---

## Scope по версиям

### v0.1 — создание плана (ModePlan)

Компоненты:
- Config loading (defaults + `.rlx/config.toml`)
- CLI: `rlx --plan tasks/auth/prompt.md` (typer)
- ClaudeExecutor (stream-json parsing, signal detection, pattern matching, idle timeout)
- Processor: `run_plan_creation()`
- Signals: QUESTION, PLAN_DRAFT, PLAN_READY, TASK_FAILED
- InputCollector: ask_question (нумерованный список), ask_draft_review (accept/revise/reject)
- Progress logging (file + stdout)
- Signal handling (SIGINT graceful shutdown)
- Базовые git: проверка что мы в репо, default branch detection

Механика `--plan`:
- `rlx --plan <path-to-prompt.md>` — путь к файлу с описанием задачи
- rlx читает содержимое файла как описание
- Результат записывается рядом: `<name>-plan.md`
- Пример: `tasks/auth/prompt.md` -> `tasks/auth/prompt-plan.md`

### v0.2 — выполнение задач

Компоненты:
- CLI: `rlx --task tasks/auth/prompt-plan.md`
- Processor: task loop (`run_task_phase`)
- Plan parsing (markdown: `### Task N:` + checkboxes)
- Git: commit, diff
- Rate limit detection + wait-retry
- Session/idle timeout
- Manual break (SIGQUIT)

### v0.3 — review pipeline

Компоненты:
- CLI: `rlx --review`
- Review pipeline (review_first — 5 агентов, review_second — 2 агента)
- Agent system (loading, frontmatter, expansion)
- Finalize step

---

## Что убрано из ralphex

| Фича | Причина |
|---|---|
| Codex executor | Не нужен, только Claude |
| Custom executor / scripts | Не нужен |
| Web dashboard / SSE | Не нужен, tail -f достаточно |
| Notifications | Не нужен |
| Git worktree | Работаем в текущей ветке |
| fzf | Нумерованные списки достаточно |
| Docker wrapper | Не нужен |
| Global config (~/.config/) | Только локальный .rlx/ |
| --init / --reset / --dump-defaults | mkdir .rlx руками |
| 30+ CLI флагов | Только 4 опции, остальное в конфиге |
| INI формат | TOML |
| *Set tracking | Не нужен с TOML |
| 3-уровневый каскад конфигов | 2 уровня |
