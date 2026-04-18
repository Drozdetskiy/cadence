# Справочник проекта rlx

Эта директория содержит описание архитектуры rlx -- Python-порта ralphex с упрощенным дизайном. Каждый документ описывает конкретный слой системы с детализацией, достаточной для реализации.

## Содержание

1. [Архитектура](01-architecture.md) - высокоуровневая схема системы, граф зависимостей модулей, потоки выполнения для каждого режима, жизненный цикл фаз, модель сигналов, управление процессами
2. [Система конфигурации](02-config.md) - все поля конфига с типами и дефолтами, каскад загрузки (defaults in code -> `.rlx/config.toml` -> CLI flags), merge-стратегия, шаблонные переменные, frontmatter агентов
3. [CLI и точка входа](03-cli.md) - все флаги с типами и дефолтами (`--plan`, `--task`, `--review`, `--version`), определение режима из комбинации флагов, валидация конфликтов, основной поток выполнения, обработка сигналов
4. [Processor / оркестрация](04-processor.md) - Runner и его зависимости, методы каждого режима, циклы задач/ревью, timeout-ы сессий, retry при rate limit, построение промптов
5. [Executor layer](05-executors.md) - интерфейс Executor, ClaudeExecutor (streaming JSON, idle timeout, pattern matching), платформенные различия
6. [Git и планы](06-git-and-plans.md) - Service/backend, все операции (branch, commit, diff), определение default branch, VCS-абстракция, парсинг планов, выбор через numbered list
7. [Промпты](08-prompts.md) - полный текст каждого промпт-файла, используемые переменные, эмитируемые сигналы, привязка к фазам
8. [Агенты](09-agents.md) - полный текст каждого агента ревью, frontmatter-опции, привязка к промптам
9. [Каталог фич](10-features.md) - все пользовательские фичи, кроссплатформенные заметки

## Как пользоваться справочником

- Начните с [архитектуры](01-architecture.md) для понимания общей структуры
- [Processor](04-processor.md) и [executors](05-executors.md) - ядро системы, их стоит изучить вместе
- [Промпты](08-prompts.md) и [агенты](09-agents.md) - текстовые ресурсы, которые переносятся as-is
- [Каталог фич](10-features.md) - чеклист для проверки полноты

## Маппинг Go -> Python

Ниже собраны ключевые Go-паттерны, используемые в ralphex, и их Python-эквиваленты для rlx.

### Конкурентность: goroutines -> sync + threading

Go использует goroutines для:
- Фоновый мониторинг отмены контекста при управлении процессами (`pkg/executor/procgroup_unix.go`)
- Неблокирующее чтение строк из pipe с контекстной отменой (`pkg/executor/linereader.go`)

Python-эквиваленты:
- `threading.Thread` для блокирующих вызовов (subprocess, pipe reading)
- `concurrent.futures.ThreadPoolExecutor` для параллельного запуска нескольких подпроцессов
- `threading.Event` для сигнализации завершения

### Каналы: channels -> threading primitives

Go использует каналы для:
- Сигнализация завершения (`done chan struct{}`)
- Буферизованные каналы для результатов чтения pipe

Python-эквиваленты:
- `threading.Event` для сигнализации (аналог `chan struct{}`)
- `queue.Queue` для межпоточной коммуникации

### Контекст: context.Context -> cancellation tokens

Go использует context для:
- Отмена подпроцессов при таймауте сессии

Python-эквиваленты:
- `subprocess.Popen` + `threading.Timer` для таймаутов
- `threading.Event` для cancellation signaling

### Управление процессами: process groups -> subprocess

Go использует `syscall.SysProcAttr{Setsid: true}` для изоляции процессов в сессию, затем SIGTERM/SIGKILL по negative PID для завершения всей группы.

Python-эквиваленты:
- `subprocess.Popen(start_new_session=True)` для создания новой сессии (Unix)
- `os.killpg(os.getpgid(proc.pid), signal.SIGTERM)` для завершения группы
- На Windows: `subprocess.CREATE_NEW_PROCESS_GROUP` + `proc.terminate()`
- Graceful shutdown: SIGTERM -> wait -> SIGKILL аналогично Go-реализации

### Встроенные файлы: go:embed -> importlib.resources

Go встраивает файлы через `//go:embed`:
- `pkg/config/config.go`: `defaults/config`, `defaults/prompts/*`, `defaults/agents/*`

Python-эквиваленты:
- `importlib.resources` (Python 3.9+) для доступа к файлам пакета
- Структура пакета: defaults как sub-package с `__init__.py`

### Конфигурация: INI (gopkg.in/ini.v1) -> tomllib

Go использует `ini.LoadSources` с `IgnoreInlineComment: true`.

Python-эквивалент:
- `tomllib` (Python 3.11+, stdlib) для чтения TOML-конфига
- Каскад: defaults в коде -> `.rlx/config.toml` -> CLI flags

### Потоковый JSON: line-by-line json.Unmarshal -> json.loads

Go парсит JSON построчно через `json.Unmarshal` каждой строки.

Python-эквивалент:
- `for line in process.stdout: event = json.loads(line)` - прямой аналог

### Цветной вывод: fatih/color -> rich

Go использует `github.com/fatih/color` с объектами цвета для каждой фазы.

Python-эквивалент:
- `rich` - мощная библиотека с поддержкой RGB, стилей, прогресс-баров
- Поддерживает RGB цвета (как в Go-конфиге), Windows-совместимость из коробки

### CLI-парсинг: go-flags -> typer

Go использует `github.com/jessevdk/go-flags` со struct tags.

Python-эквивалент:
- `typer` - type hints для CLI, минимальный API
- Минимальный набор флагов: `--plan <file>`, `--task <file>`, `--review`, `--version`

### Тестирование: testify -> pytest

Go использует table-driven tests с `github.com/stretchr/testify`.

Python-эквиваленты:
- `pytest` с `@pytest.mark.parametrize` для table-driven тестов
- `unittest.mock` / `pytest-mock` вместо moq
- `tmp_path` fixture вместо `t.TempDir()`
- `pytest-cov` для покрытия

### Синхронизация: sync.Mutex / sync.RWMutex -> threading.Lock

Go использует `sync.RWMutex` для защиты map-ов и состояния, `sync.Once` для однократного выполнения.

Python-эквиваленты:
- `threading.RLock()` для reentrant lock
- `functools.cache` или ручной флаг для аналога `sync.Once`

### Платформенные различия: build tags -> runtime checks

Go использует `//go:build !windows` и отдельные файлы `_unix.go` / `_windows.go`.

Python-эквиваленты:
- `sys.platform` / `platform.system()` для runtime-проверок
- Отдельные модули (`_unix.py`, `_windows.py`) с фабрикой для выбора
- `importlib.import_module()` для условного импорта
- Протоколы (typing.Protocol) для определения интерфейса платформенных операций

## Рекомендуемый стек для rlx

| Компонент | Go-библиотека | Python-реализация |
|-----------|--------------|---------------------|
| CLI | jessevdk/go-flags | typer |
| Config | gopkg.in/ini.v1 | tomllib (stdlib) |
| Color output | fatih/color | rich |
| Testing | stretchr/testify | pytest |
| Subprocess | os/exec | subprocess (stdlib) |
| JSON streaming | encoding/json | json (stdlib) |
| Embedded files | go:embed | importlib.resources |

## Внешние зависимости (остаются как есть)

Эти инструменты вызываются как CLI и не требуют портирования:
- `claude` - Claude Code CLI (обязательно)
- `git` - для всех git-операций
- `$EDITOR` - для интерактивного ревью планов
