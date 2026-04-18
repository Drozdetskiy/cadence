# rlx v0.1 -- ModePlan (создание плана через Claude)

## Что такое rlx

Python CLI-инструмент для автономного выполнения задач через Claude Code. v0.1 реализует только одну команду: `rlx --plan <file>` -- интерактивное создание плана реализации через диалог с Claude.

## Технические решения

- Python 3.14+ (все современные фичи: type hints, match/case)
- Package manager: pdm
- CLI: typer (обертка над click, type hints для CLI)
- Цветной вывод: rich (RGB, markdown rendering)
- Конфиг: TOML через tomllib (stdlib)
- Sync + threading (без asyncio): subprocess.Popen для процессов, threading.Timer для idle timeout
- Embedded ресурсы: importlib.resources
- Установка: `pip install -e .` (разработка), позже PyPI
- Entrypoint: `rlx` через `pyproject.toml [project.scripts]`
- Зависимости: typer + rich. Все остальное -- stdlib

## Справочные документы

Детальная спецификация каждого модуля лежит в `docs/reference/`. Перед созданием плана ОБЯЗАТЕЛЬНО прочитай все документы:

- `docs/reference/README.md` -- обзор, маппинг Go->Python
- `docs/reference/01-architecture.md` -- архитектура, граф зависимостей, потоки выполнения, модель сигналов
- `docs/reference/02-config.md` -- система конфигурации, каскад загрузки, TOML формат, все поля с типами и дефолтами
- `docs/reference/03-cli.md` -- CLI через typer, опции, определение режима, основной поток
- `docs/reference/04-processor.md` -- Runner, run_plan_creation(), циклы, промпты, сигналы
- `docs/reference/05-executors.md` -- ClaudeExecutor, stream-json parsing, idle timeout, pattern matching, process groups
- `docs/reference/06-git-and-plans.md` -- git Service, Backend, plan parsing
- `docs/reference/07-web-and-io.md` -- Logger, Colors, file locking, TerminalCollector, ask_question, ask_draft_review
- `docs/reference/08-prompts.md` -- тексты промптов, шаблонные переменные, сигналы
- `docs/reference/09-agents.md` -- система агентов, frontmatter, загрузка
- `docs/reference/10-features.md` -- каталог фич, кроссплатформенные различия

Эти документы -- ИСЧЕРПЫВАЮЩАЯ спецификация. В них описаны все типы, интерфейсы, алгоритмы и поведение. Код должен строго соответствовать спецификации.

## Scope v0.1

Только `rlx --plan <file>`. Команда читает файл с описанием задачи и создает план реализации через интерактивный диалог с Claude.

### Что входит в v0.1

1. **Каркас проекта**: pyproject.toml (pdm, entrypoint, зависимости), структура пакета `src/rlx/`
2. **status.py** -- Phase, Signal, Section типы (leaf module)
3. **config.py** -- Config dataclass, загрузка defaults + TOML merge, ColorConfig, duration parsing
4. **input.py** -- TerminalCollector: ask_question (numbered picker с "Other"), ask_draft_review (accept/revise/interactive/reject), ask_yes_no
5. **progress/** -- Logger (timestamp output, section headers, file logging, rich colors), Colors (RGB, phase mapping), file locking (fcntl)
6. **executor/** -- ClaudeExecutor (subprocess, stream-json parsing, detect_signal, match_pattern, idle timeout), process group management (Unix: start_new_session, SIGTERM/SIGKILL)
7. **git.py** -- минимум для v0.1: проверка что мы в репо, default branch detection, head_hash
8. **processor/** -- Runner с run_plan_creation() (Q&A loop, draft review, QUESTION/PLAN_DRAFT/PLAN_READY сигналы), prompts.py (загрузка + подстановка переменных), signals.py (parse_question_payload, parse_plan_draft_payload)
9. **cli.py** -- typer app: --plan, --version, signal handling (SIGINT graceful shutdown)
10. **defaults/** -- встроенный промпт make_plan.txt
11. **Тесты**: pytest для каждого модуля

### Что НЕ входит в v0.1

- `--task` (task execution) -- это v0.2
- `--review` (review pipeline) -- это v0.3
- Агенты ревью (5+2) -- это v0.3
- task.txt, review промпты -- это v0.2/v0.3
- git commit/diff/branch operations -- это v0.2
- Rate limit retry (run_with_limit_retry) -- это v0.2
- Session timeout -- это v0.2
- Stalemate detection -- это v0.3
- Plan parsing (parse_plan, Task/Checkbox) -- это v0.2
- Plan selection (numbered list из plans_dir) -- это v0.2
- Finalize step -- это v0.3

## Структура пакета

```
src/rlx/
  __init__.py          # version
  cli.py               # typer app, entrypoint
  status.py            # Phase, Signal, Section types
  config.py            # Config, ColorConfig, load, merge
  input.py             # TerminalCollector
  git.py               # minimal git operations
  progress/
    __init__.py
    logger.py           # Logger class
    colors.py           # Colors class, RGB parsing
    flock.py            # file locking (fcntl/no-op)
  executor/
    __init__.py
    claude_executor.py  # ClaudeExecutor, Result, detect_signal, match_pattern, error types
    process_group.py    # Unix process group management
  processor/
    __init__.py
    runner.py           # Runner class, run_plan_creation()
    prompts.py          # prompt loading, variable substitution
    signals.py          # parse_question_payload, parse_plan_draft_payload
  defaults/
    __init__.py
    prompts/
      make_plan.txt     # plan creation prompt
tests/
  __init__.py
  test_status.py
  test_config.py
  test_input.py
  test_executor.py
  test_processor.py
  test_progress.py
  test_signals.py
  test_cli.py
```

## Поток выполнения `rlx --plan <file>`

1. typer парсит --plan <file>
2. Загрузка конфига: defaults + .rlx/config.toml (если есть)
3. Валидация: файл существует, claude в PATH, мы в git repo
4. Определение режима: ModePlan
5. Создание Logger (progress file)
6. Создание ClaudeExecutor
7. Создание Runner
8. Runner.run() -> run_plan_creation():
   - Цикл до max_plan_iterations (max(5, max_iterations/5))
   - Каждая итерация: собрать промпт (make_plan.txt + переменные + Q&A history из progress file) -> запустить ClaudeExecutor -> прочитать Result
   - Обработка сигналов:
     - QUESTION: распарсить JSON, показать пользователю через ask_question, записать ответ в progress
     - PLAN_DRAFT: показать план через ask_draft_review (rich markdown rendering)
       - Accept: следующая итерация запишет файл, получим PLAN_READY
       - Revise: записать feedback в progress, следующая итерация доработает
       - Interactive: открыть $EDITOR, вычислить diff, записать в progress
       - Reject: TASK_FAILED, выход
     - PLAN_READY: план записан как `<file>-plan.md`, спросить "Continue with implementation?" (v0.1: всегда No, т.к. --task не реализован)
9. SIGINT (Ctrl+C): graceful shutdown -- убить claude процесс, cleanup, выход

## Ключевые контракты

### Сигналы

```
<<<RLX:QUESTION>>>
{"question": "...", "options": ["...", "..."]}
<<<RLX:END>>>

<<<RLX:PLAN_DRAFT>>>
# Plan content...
<<<RLX:END>>>

<<<RLX:PLAN_READY>>>

<<<RLX:TASK_FAILED>>>
```

### ClaudeExecutor

- Запуск: `claude --dangerously-skip-permissions --output-format stream-json --verbose --print`
- Prompt через stdin (не через -p)
- Парсинг JSON stream построчно из stdout
- Сигналы детектируются в тексте через substring search
- Idle timeout: threading.Timer с reset на каждой строке
- Process group: start_new_session=True, kill через os.killpg

### Config merge

- Defaults в коде (dataclass с default values)
- .rlx/config.toml: tomllib.load(), per-field merge
- CLI flags через typer: применяются последними
- Промпты: per-file fallback (local .rlx/prompts/ -> embedded defaults/)

## Требования к тестам

- pytest
- Каждый модуль покрыт unit-тестами
- ClaudeExecutor: mock через CommandRunner protocol (не запускать реальный claude)
- Runner: mock Executor, Logger, InputCollector, GitChecker через protocols
- Config: тест загрузки defaults, merge с TOML, валидация
- Input: тест ask_question, ask_draft_review с mock stdin/stdout
- Тесты запускать через: `pdm run pytest`
- Линтер: `pdm run ruff check src/ tests/`

## Требования к валидации

- `pdm run pytest` -- все тесты проходят
- `pdm run ruff check src/ tests/` -- нет ошибок линтера
- `pdm run mypy src/` -- type checking проходит (strict mode)
- `rlx --version` -- выводит версию
- `rlx --plan <test-file>` -- запускается, показывает вопросы Claude (требует настроенный claude CLI)
