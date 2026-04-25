# rlx --review: флаг `--base` для указания таргет-ветки

## Что такое rlx

Python CLI для автономного выполнения задач через Claude Code. Режим `rlx --review` запускает review-only текущей ветки относительно базовой (default) ветки: `review_first` -> `review_loop` -> `finalize`. См. `CLAUDE.md` и `tasks/0004-v03/0004-v03-implementation-prompt.md`.

## Проблема

Сейчас базовая ветка для diff в `--review` определяется в `src/rlx/cli.py` (`run_review_mode()`):

```python
default_branch = cfg.default_branch or git_svc.get_default_branch()
```

Порядок: `default_branch` из `.rlx/config.toml` -> автоопределение через git. CLI-флага для разового переопределения нет, что неудобно: ревью feature-ветки относительно произвольной базы (например, `develop`, релизной ветки, или родительской feature-ветки) требует правки конфига.

## Скоуп

Добавить CLI-флаг `--base <branch>` для режима `rlx --review`. Флаг переопределяет базовую ветку **только на текущий запуск**, не трогая конфиг.

### Что входит

1. **CLI** (`src/rlx/cli.py`):
   - Добавить опцию `_BASE_OPT: str | None = typer.Option(None, "--base", help="Base branch for review diff (overrides config default_branch)")`
   - Пробросить значение в `run_review_mode(base: str | None)`
   - Внутри `run_review_mode()` изменить резолюцию:
     ```python
     default_branch = base or cfg.default_branch or git_svc.get_default_branch()
     ```
   - Валидация: `--base` имеет смысл только с `--review`. Если передан с `--plan` или `--task` -- падать с понятной ошибкой (`error: --base is only valid with --review`). Это согласуется с текущей валидацией `--impl`/`--review` в cli.py.
   - Опционально: проверить что указанная ветка существует через `git_svc` (если есть подходящий метод; иначе -- пусть git сам упадёт при `diff_stats`). Решить по месту: если простая проверка существует -- добавить с осмысленным сообщением, иначе не усложнять.

2. **Логирование**:
   - В `run_review_mode()` после резолюции базовой ветки добавить `log.print("base: %s", default_branch)` рядом с существующим `log.print("branch: %s", branch)`. Полезно видеть в логе, относительно чего идёт ревью.

3. **Тесты** (`tests/`):
   - `determine_mode` / CLI parsing: `--review --base develop` корректно парсится.
   - `--base` без `--review` -> SystemExit с ожидаемым сообщением.
   - `--base` имеет приоритет над `cfg.default_branch` и автоопределением.
   - Если `--base` не передан -- поведение не меняется (regression-тест существующего пути).
   - Моки: `GitChecker` / `Service`, не запускать реальный git (через `tmp_path` где нужно).

### Что НЕ входит

- Поддержка `--base` для `--task` и `--plan`. Эти режимы создают/работают со своей веткой, отдельная база сейчас не нужна.
- Изменение конфигурации (`config.toml`). Существующее поле `default_branch` остаётся как есть.
- Авто-fetch / pull базовой ветки перед diff.
- Поддержка remote-веток с префиксом (`origin/develop`). Если git примет имя -- работает, специальной обработки не добавлять.

## Ключевые контракты

### Приоритет резолюции базовой ветки в `--review`

```
1. --base <branch>           (CLI flag, разовое переопределение)
2. cfg.default_branch        (.rlx/config.toml)
3. git_svc.get_default_branch()  (автоопределение)
```

### Сообщения об ошибках

- `--base` с `--plan` или `--task`: `error: --base is only valid with --review` -> exit 1
- (Опционально) ветка не существует: `error: base branch '<name>' not found` -> exit 1

## Требования к валидации

- `pdm run pytest` -- все тесты проходят
- `pdm run ruff check src/ tests/` -- нет ошибок линтера
- `pdm run mypy src/` -- strict type checking проходит
- `rlx --version` -- выводит версию
- `rlx --review --help` -- показывает новый флаг `--base`

## Важно: не переустанавливать rlx и не запускать ручные проверки

`rlx` уже установлен и **запущен** в текущей сессии (из этой же копии исходников выполняется ревью-агент, читающий этот промпт). Любая переустановка -- особенно в editable/dev режим (`pip install -e .`, `pipx install --editable`) -- может изменить поведение запущенного процесса непредсказуемым образом или сломать активную сессию.

Конкретно:
- **НЕ** запускать `pip install`, `pip install -e .`, `pdm install`, `pipx install`, `uv pip install` и т.п.
- **НЕ** запускать `rlx --review`, `rlx --task`, `rlx --plan` для ручной проверки изменений -- это запустит вложенный инстанс поверх уже работающего.
- Валидация только через `pytest`, `ruff check`, `mypy` -- они не требуют установки пакета и не запускают CLI.
- Корректность CLI-парсинга проверять unit-тестами через typer's `CliRunner`, а не реальным запуском `rlx`.