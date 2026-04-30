# CLI и точка входа

Справочный документ по CLI-слою rlx (Python-порт ralphex).

## Парсинг аргументов

Используется `typer` (обёртка над `click`). Четыре опции:

| Опция | Тип | Default | Описание |
|-------|-----|---------|----------|
| `--plan` | `Path` | None | Путь к файлу с описанием -- создать план |
| `--task` | `Path` | None | Путь к файлу плана -- выполнить задачи |
| `--review` | `bool` | `false` | Только ревью текущей ветки |
| `--version` | `bool` | `false` | Вывести версию и выйти |

Нет позиционных аргументов. Нет env var для опций (кроме `RLX_CONFIG_DIR` для конфигурации).

### Поведение опций

- `--plan <file>` -- читает содержимое файла, создаёт план. Результат записывается рядом с исходным файлом с суффиксом `-plan.md` (например, `feature.md` -> `feature-plan.md`)
- `--task <file>` -- принимает путь к файлу плана. Если файл существует, запускает выполнение задач по плану (ModeTasksOnly или ModeFull в зависимости от конфигурации)
- `--review` -- запуск только review фазы для текущей ветки, без выполнения задач
- `--version` -- вывод версии через `importlib.metadata.version("rlx")`, затем выход

## Определение режима (Mode)

Функция `determine_mode(plan, task, review)`:

```
if plan is not None  -> ModePlan
if task is not None  -> ModeFull (tasks + review)
if review            -> ModeReview
```

Enum `Mode` (строковые константы):
- `"full"` -- задачи + ревью + finalize
- `"review"` -- только ревью + finalize
- `"tasks-only"` -- только задачи (без ревью)
- `"plan"` -- создание плана

Порядок приоритета: `--plan` > `--task` > `--review`.

Взаимоисключающие опции: typer обеспечивает валидацию (нельзя указать `--plan` и `--task` одновременно).

## Основной поток выполнения

```
main()
  |-- typer.run() -- парсинг аргументов
  |-- signal handling (SIGINT)
  |
  v
run(plan, task, review, version)
  |-- if version -> print version, sys.exit(0)
  |-- config.load() -- загрузка конфигурации (.rlx/config.toml + defaults)
  |-- check_claude_dep(cfg) -- claude в PATH
  |-- os.path.isdir(".git") -- проверка корня репо
  |-- open_git_service(cfg.vcs_command)
  |-- git_svc.set_commit_trailer(cfg.commit_trailer)
  |-- ensure_repo_has_commits()
  |
  |-- resolve_default_branch(cfg)
  |-- determine_mode(plan, task, review)
  |
  |-- if ModePlan -> run_plan_mode(plan_file)
  |-- if ModeFull -> run_full_mode(task_file)
  |-- if ModeReview -> run_review_mode()
```

## Режим создания плана (run_plan_mode)

```
run_plan_mode(plan_file)
  |-- content = read_file(plan_file)
  |-- ensure_local_gitignore()
  |-- get_current_branch()
  |-- progress.new_logger()
  |-- print_startup_info()
  |-- processor.new(mode="plan", plan_description=content)
  |-- runner.run()
  |
  |-- Результат: файл плана создаётся рядом с исходным файлом
  |     с суффиксом -plan.md
```

## Выполнение плана (run_full_mode)

```
run_full_mode(task_file)
  |-- plan_file = resolve_path(task_file)
  |-- ensure_local_gitignore()
  |-- create_branch_for_plan(plan_file)
  |-- execute_plan(plan_file, mode="full")
```

### Извлечение имени ветки

`extract_branch_name(plan_file)`:
- убирает .md расширение
- убирает date prefix (regexp `^[\d-]+`) -- "2024-01-15-feature" -> "feature"
- если после удаления пусто (только даты), возвращает оригинальное имя

## execute_plan -- основное выполнение

```
execute_plan(plan_file, mode)
  |-- get_current_branch()
  |-- progress.new_logger()
  |-- print_startup_info() -- вывод режима, ветки, пути прогресса
  |-- create_runner(plan_file, mode)
  |-- runner.run()
  |
  |-- при успехе:
  |     |-- git_svc.diff_stats(base_ref)
  |     |-- mark_plan_completed()  -- in-place rename, no commit
  |     |-- display_stats()
  |
  |-- при ошибке:
  |     |-- UserAbortedError -> "aborted by user", return (без ошибки)
  |     |-- иначе -> raise / sys.exit(1)
```

## Обработка сигналов

### SIGINT (Ctrl+C)

```python
signal.signal(signal.SIGINT, handler)
```

- Первый SIGINT: graceful shutdown -- отменяет текущую операцию
- Повторный SIGINT (в течение 5 секунд): force exit через `sys.exit(1)`
- Threading: используется `threading.Event` для координации shutdown

Подавление ^C echo через модуль `termios` (стандартная библиотека).

## Проверка зависимостей

`check_claude_dep(cfg)`:
- Берёт `cfg.claude_command` (или "claude" по умолчанию)
- `shutil.which()` -- проверка в PATH
- Ошибка если не найден

Проверка корня репо:
- `os.path.isdir(".git")` -- для стандартного git
- Пропускается если `cfg.vcs_command` не пустой и не "git"

## Разрешение конфигурационных значений

### DefaultBranch

`resolve_default_branch(config_branch, auto_detected)`:
- config (`default_branch`) > auto-detected (`git_svc.get_default_branch()`)
- CLI не влияет (нет `--base-ref` флага)

### applyCLIOverrides

В rlx CLI overrides минимальны -- модели, timeouts и прочее настраиваются только через config. CLI определяет только режим работы (`--plan`, `--task`, `--review`).

## create_runner

Создаёт `processor.Runner` с конфигурацией:

Разрешение параметров (config > default):
- `plan_model`: config > `"claude-opus-4-7"`
- `task_model`: config > `"claude-opus-4-7"`
- `review_model`: config > `"claude-opus-4-7"`
- `max_iterations`: config > 50
- `task_retry_count`: config > 1

## Версия

`resolve_version()`:
- `importlib.metadata.version("rlx")` -- версия из package metadata
- Fallback: "unknown"

Выводится при запуске: `print(f"rlx {resolve_version()}")`.
`--version`: после вывода -- `sys.exit(0)`.

## Вспомогательные функции

- `to_rel_path(p)` -- абсолютный путь в относительный от CWD
- `display_meta(plan_file, branch, progress_path)` -- печать plan/branch/progress
- `display_stats(stats, elapsed, branch)` -- итоговая статистика (файлы, +/-)
- `file_exists(path)` -- `os.path.exists()` check
