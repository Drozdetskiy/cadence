# Git-операции и работа с планами

Справочный документ по модулям `git` и `plan` для Python-порта rlx.

## Обзор

Два модуля обеспечивают взаимодействие с VCS и управление планами:

- `git` -- единый API для git-операций (ветки, коммиты, диффы) через subprocess
- `plan` -- парсинг markdown-планов, нумерованный выбор, извлечение имени ветки

Ключевые модули:
- `rlx/git/service.py` -- Service class, все публичные методы
- `rlx/git/backend.py` -- ExternalBackend: реализация через CLI (git)
- `rlx/plan/plan.py` -- Selector, Select(), ExtractBranchName()
- `rlx/plan/parse.py` -- ParsePlan(), ParsePlanFile(), типы Task/Checkbox/Plan

## Logger интерфейс

Совместим с progress-логгером и стандартным `logging.Logger`. Методы Service логируют через переданный logger.

## Backend интерфейс

Абстрагирует низкоуровневые git-операции:

```python
class Backend(Protocol):
    def root(self) -> str: ...
    def head_hash(self) -> str: ...
    def has_commits(self) -> bool: ...
    def current_branch(self) -> str: ...
    def get_default_branch(self) -> str: ...
    def branch_exists(self, name: str) -> bool: ...
    def create_branch(self, name: str) -> None: ...
    def checkout_branch(self, name: str) -> None: ...
    def diff_fingerprint(self) -> str: ...
    def is_dirty(self) -> bool: ...
    def file_has_changes(self, path: str) -> bool: ...
    def has_changes_other_than(self, path: str) -> list[str]: ...
    def add(self, path: str) -> None: ...
    def move_file(self, src: str, dst: str) -> None: ...
    def commit(self, msg: str) -> None: ...
    def commit_files(self, msg: str, *paths: str) -> None: ...
    def create_initial_commit(self, msg: str) -> None: ...
    def diff_stats(self, base_branch: str) -> DiffStats: ...
```

Единственная реализация -- `ExternalBackend` (вызывает git CLI через subprocess).

## DiffStats

```python
@dataclass
class DiffStats:
    files: int = 0      # number of files changed
    additions: int = 0   # lines added
    deletions: int = 0   # lines deleted
```

Возвращается из `Service.diff_stats()` и `backend.diff_stats()`. Бинарные файлы (`-` в numstat) считаются как 1 файл без additions/deletions.

## Service class

```python
class Service:
    def __init__(self, path: str, log: Logger):
        self._repo: ExternalBackend
        self._log: Logger
        self._trailer: str = ""  # optional trailer line appended to all commits
```

Единственный публичный API модуля `git`. Все операции -- методы на `Service`.

### Конструктор

```python
def __init__(self, path: str, log: Logger) -> None
```

- `path` -- путь к репозиторию (`.` для текущей директории)
- Создаёт `ExternalBackend`, который валидирует путь через `rev-parse --show-toplevel`
- Резолвит symlinks для консистентного сравнения путей (macOS `/var` -> `/private/var`)

### Commit Trailer

```python
def set_commit_trailer(self, trailer: str) -> None
def _append_trailer(self, msg: str) -> str  # private
```

Если `trailer` не пуст, `_append_trailer()` добавляет `"\n\n" + trailer` к сообщению коммита. Применяется ко всем коммитам через Service:
- `create_branch_for_plan()` -- "add plan: <branch>"
- `commit_plan_file()` -- "add plan: <branch>"
- `move_plan_to_completed()` -- "move completed plan: <filename>"
- `ensure_has_commits()` -- "initial commit"

### Методы состояния репозитория

| Метод | Описание |
|---|---|
| `root() -> str` | Абсолютный путь к корню репозитория |
| `head_hash() -> str` | SHA текущего HEAD коммита |
| `diff_fingerprint() -> str` | SHA256-хэш состояния working tree для stalemate detection |
| `current_branch() -> str` | Имя текущей ветки, пустая строка для detached HEAD |
| `is_default_branch(default_branch: str) -> bool` | Проверяет совпадение текущей ветки с default |
| `get_default_branch() -> str` | Определяет default branch (алгоритм ниже) |
| `has_commits() -> bool` | Есть ли хотя бы один коммит |
| `diff_stats(base_branch: str) -> DiffStats` | Статистика изменений base...HEAD |
| `file_has_changes(path: str) -> bool` | Есть ли незакоммиченные изменения файла |

### Алгоритм определения default branch

Реализован в `ExternalBackend.get_default_branch()`:

1. Пробует `git symbolic-ref refs/remotes/origin/HEAD` -- если успех, извлекает имя ветки из `refs/remotes/origin/<name>`
   - Если локальная ветка `refs/heads/<name>` существует -- возвращает `<name>`
   - Иначе -- возвращает `origin/<name>` (remote-tracking ref)
2. Перебирает `["main", "master", "trunk", "develop"]` -- возвращает первую существующую локальную ветку
3. Fallback -- `"master"`

Дополнительно `_matches_default_branch(branch, default_branch)`:
- Снимает prefix `origin/` для сравнения
- Если `default_branch` пуст, проверяет `branch == "main" or branch == "master"`

### Операции с ветками

**create_branch(name: str) -> None**
- Делегирует в `backend.create_branch()` -- `git checkout -b <name>`

**create_branch_for_plan(plan_file: str, default_branch: str) -> None**
- Основной метод для создания feature-ветки при запуске плана
- Последовательность:
  1. `_resolve_filesystem_case(plan_file)` -- разрешение регистра имени файла
  2. `_prepare_plan_branch()` -- валидация, извлечение имени ветки, проверка dirty files
  3. Если уже не на default branch -- return (уже на feature branch)
  4. Если ветка существует -- `checkout`, иначе `checkout -b`
  5. Если plan file имеет изменения (единственный dirty файл) -- auto-commit: `git add` + `git commit "add plan: <branch>"`

**_prepare_plan_branch(plan_file: str, default_branch: str) -> tuple[str, bool]** (private)
- Проверяет текущую ветку: если не на default branch, возвращает пустое имя (caller skip)
- Извлекает имя ветки через `plan.extract_branch_name(plan_file)`
- Проверяет dirty files через `has_changes_other_than(plan_file)` -- ошибка если есть
- Проверяет `file_has_changes(plan_file)` -- возвращает bool для auto-commit

### Операции с plan-файлами

**commit_plan_file(plan_file: str) -> None**
- Коммитит plan-файл
- `git add` + `git commit "add plan: <branch>"`

**move_plan_to_completed(plan_file: str) -> None**
- Перемещает план в `completed/` поддиректорию
- Создаёт `completed/` если не существует
- Если source не существует, но dest существует -- log + return (idempotent)
- Пробует `git mv` -- если ошибка (untracked файл), fallback на `os.rename` + `git add`
- Коммитит: "move completed plan: <filename>"

### Вспомогательные операции

**ensure_has_commits(prompt_fn: Callable[[], bool]) -> None**
- Проверяет наличие коммитов через `has_commits()`
- Если пуст -- вызывает `prompt_fn()` для подтверждения
- `create_initial_commit()` -- `git add -A` + `git commit`

### Case-insensitive path resolution

```python
def _resolve_filesystem_case(self, path: str) -> str
```

Обрабатывает macOS APFS case-insensitive filesystems, где git может трекать файл в одном регистре, а caller передаёт другой.

Алгоритм:
1. Читает parent directory через `os.listdir(dir)`
2. Если exact match найден -- возвращает оригинальный path
3. Если case-insensitive match найден -- возвращает путь с реальным регистром
4. Fallback -- оригинальный path

Используется в: `create_branch_for_plan()`, `commit_plan_file()`.

## ExternalBackend

```python
class ExternalBackend:
    def __init__(self, path: str):
        self._path: str   # absolute path to repository root
        self._command: str = "git"
```

Реализует Backend через вызовы CLI.

### Конструктор

```python
def __init__(self, path: str) -> None
```

1. `Path(path).resolve()` -- абсолютный путь
2. `git rev-parse --show-toplevel` -- валидация и получение корня
3. `os.path.realpath(root)` -- резолв symlinks для консистентности
4. Если ошибка -- parsing stderr для информативного сообщения

### run() -- выполнение команд

```python
def _run(self, *args: str) -> str
```

- `subprocess.run([self._command, *args], cwd=self._path, capture_output=True, text=True)`
- Trailing whitespace удаляется (`rstrip()`), leading сохраняется (нужно для porcelain)
- При ошибке -- stderr включается в сообщение

### DiffFingerprint

SHA256 хэш состояния working tree для stalemate detection:

1. `git diff HEAD` -- tracked изменения
2. `git ls-files -z --others --exclude-standard` -- untracked файлы (null-terminated для спецсимволов)
3. Хэширует: diff output + для каждого untracked файла: имя + `git hash-object` (blob hash содержимого)
4. Это обеспечивает обнаружение изменений в существующих untracked файлах, не только создание новых

### has_commits

- `git rev-parse HEAD` с `LC_ALL=C` для English stderr
- Exit code 128 + `"ambiguous argument"` в stderr -> пустой репозиторий (return False)
- Другие exit-128 причины (corruption, permission) -> propagate error

### current_branch

- `git symbolic-ref --short HEAD` с `LC_ALL=C`
- Exit 128 + `"not a symbolic ref"` -> detached HEAD (return "")
- Другие exit-128 причины -> propagate error

### is_dirty

- `git status --porcelain`
- Проходит по строкам, игнорирует untracked (`??`) -- они не считаются dirty
- Любая другая строка (modified, staged, deleted) -> dirty

### file_has_changes / has_changes_other_than

`file_has_changes(path)`:
- Конвертирует в relative через `_to_relative()`
- `git status --porcelain -uall -- <rel>` (-uall для развёрнутых путей, не collapsed директорий)
- Непустой output = есть изменения

`has_changes_other_than(path)`:
- `git status --porcelain -uall` -- все файлы
- Парсит через `_extract_path_from_porcelain()` каждую строку
- Case-insensitive сравнение для исключения plan-файла
- Возвращает list dirty файлов

### _extract_path_from_porcelain

```python
def _extract_path_from_porcelain(self, line: str) -> str
```

Парсит формат `"XY path"` или `"XY original -> renamed"`:
- Пропускает первые 3 символа (2-char status + space)
- Обрабатывает rename (`" -> "`) -- берёт новое имя

### diff_stats

```python
def diff_stats(self, base_branch: str) -> DiffStats
```

1. `_resolve_ref(base_branch)` -- разрешает имя ветки в ref
2. Если ref не найден или HEAD == base hash -- return zero stats
3. `git diff --numstat <baseRef>...HEAD`
4. Парсит строки `additions\tdeletions\tfile`
5. Бинарные файлы (`-` для additions/deletions) -- только +1 к Files

### _resolve_ref

```python
def _resolve_ref(self, branch_name: str) -> str
```

Пробует разрешить имя в порядке:
1. Локальная ветка: `refs/heads/<name>`
2. Remote tracking: `refs/remotes/origin/<name>`
3. As-is для `origin/`-prefixed: `refs/remotes/origin/<remoteName>`
4. Произвольный ref через `git rev-parse --verify --quiet <name>` (commit hash, tag)
5. Пустая строка если ничего не найдено

### _ref_exists

- `git show-ref --verify --quiet <ref>` -- exit 0 = существует

### _to_relative

```python
def _to_relative(self, path: str) -> str
```

Конвертирует путь в relative от корня репозитория:
- Если путь относительный -- `os.path.normpath()`, проверка на `..` (escape)
- Если абсолютный -- `os.path.realpath` для dir + `os.path.relpath` от `self._path`
- Ошибка если путь вне репозитория

### Операции с файлами и коммитами

| Метод backend | git команда |
|---|---|
| `add(path)` | `git add -- <rel>` |
| `move_file(src, dst)` | `git mv -- <srcRel> <dstRel>` |
| `commit(msg)` | `git commit -m <msg>` |
| `commit_files(msg, *paths)` | `git commit -m <msg> -- <rel1> <rel2> ...` |
| `create_initial_commit(msg)` | `git add -A` + проверка staged + `git commit -m <msg>` |
| `create_branch(name)` | `git checkout -b <name>` |
| `checkout_branch(name)` | `git checkout <name>` |
| `branch_exists(name)` | `git show-ref --verify --quiet refs/heads/<name>` |

---

## Модуль plan

### Типы данных

```python
class TaskStatus(str, Enum):
    PENDING = "pending"   # нет отмеченных checkbox
    ACTIVE = "active"     # часть checkbox отмечена
    DONE = "done"         # все checkbox отмечены
    FAILED = "failed"     # определён, но не устанавливается парсером

@dataclass
class Checkbox:
    text: str
    checked: bool

@dataclass
class Task:
    number: int
    title: str
    status: TaskStatus
    checkboxes: list[Checkbox]

@dataclass
class Plan:
    title: str
    tasks: list[Task]
```

### Формат plan-файла

Markdown с определённой структурой:
- **Title**: первый `# heading` (h1)
- **Task headers**: `### Task N: Title` или `### Iteration N: Title` (regex: `^###\s+(?:Task|Iteration)\s+([^:]+?):\s*(.*)$`)
- **Checkboxes**: строки `- [ ] text` или `- [x] text` (regex: `^\s*-\s+\[([ xX])\]\s*(.*)$`), поддерживает отступы
- **Section boundaries**: `##` (h2) или `#` (h1, когда title уже установлен) закрывает текущий task -- checkboxes ниже не привязываются к task
- Важно: `###` и `####` НЕ закрывают task (это subsections)

Пример:
```markdown
# Feature Implementation Plan

## Overview
Description here...

### Task 1: Setup project structure
- [x] Create directory layout
- [ ] Add configuration files
  - [ ] Add config.yaml (indented sub-items supported)

### Task 2: Implement core logic
- [ ] Write parser
- [ ] Add validation

## Success criteria
- All tests pass
```

### Format-description checkboxes

Regex `format_in_text = re.compile(r'\[\s*[ xX]?\s*\]')` определяет checkbox-ы, чей текст содержит паттерн `[ ]` или `[x]`. Это описания формата, а не actionable items. Они игнорируются при определении completion status.

Пример: `- [ ] Plan format: Checkboxes (\`- [ ]\` / \`- [x]\`) belong only in Task sections` -- текст содержит `[ ]`, поэтому это format-description, не actionable.

### parse_plan(content: str) -> Plan

Парсит markdown строку в структурированный Plan:
1. Ищет первый `# heading` как title
2. На каждом `### Task N: Title` / `### Iteration N: Title`:
   - Сохраняет предыдущий task (если есть) с вычисленным status
   - Создаёт новый task с извлечённым номером и заголовком
3. Checkbox-строки внутри task-контекста добавляются к текущему task
4. `##` или `# (после title)` закрывает текущий task
5. Последний task сохраняется после конца файла

`_parse_task_num(s)` -- `int(s)`, возвращает 0 для нечисловых значений.

### parse_plan_file(path: str) -> Plan

Обёртка: `Path(path).read_text()` -> `parse_plan(content)`.

### file_has_uncompleted_checkbox(path: str) -> bool

Сканирует файл на наличие незавершённых actionable checkbox-ов без привязки к task headers. Используется для malformed plans (без `### Task` заголовков), чтобы не считать их завершёнными.

- Игнорирует format-description checkbox-ы (через `format_in_text`)
- Возвращает True при первом найденном `- [ ]` с actionable текстом

### determine_task_status(checkboxes: list[Checkbox]) -> TaskStatus

- Пустой list -> `Pending`
- Все отмечены -> `Done`
- Часть отмечена -> `Active`
- Ни одного -> `Pending`

### Task.has_uncompleted_actionable_work() -> bool

Возвращает True если есть хотя бы один непроставленный actionable checkbox (текст без `[ ]`/`[x]` паттерна).

### Checkbox.is_actionable() -> bool

Возвращает False если `format_in_text` матчит текст checkbox-а.

### Selector

```python
class Selector:
    def __init__(self, plans_dir: str, colors: Colors):
        self.plans_dir = plans_dir
        self.colors = colors
```

### select(plan_file: str, optional: bool) -> str

Основная точка входа для выбора plan-файла:
- `plan_file` предоставлен: валидирует существование (`Path.exists()`)
- `plan_file` пуст, `optional=True`: возвращает `""`
- `plan_file` пуст, `optional=False`: нумерованный выбор из доступных планов
- Всегда возвращает absolute path через `Path.resolve()`

### _select_with_numbers() -> str

Интерактивный выбор через нумерованный список:
1. Проверяет существование `plans_dir`
2. `glob.glob(plans_dir + "/*.md")` -- ищет plan-файлы (исключая `completed/` неявно -- glob не рекурсивный)
3. Нет файлов -> `NoPlansFoundError`
4. Один файл -> auto-select
5. Несколько файлов:
   - Выводит нумерованный список файлов
   - Промпт "Enter number (1-N):" для выбора
   - Отмена пользователем -> "no plan selected" error

### find_recent(start_time: datetime) -> str

Находит последний модифицированный plan-файл после `start_time`. Используется plan creation mode для нахождения только что созданного плана. Возвращает `""` если не найден.

### extract_branch_name(plan_file: str) -> str

Извлекает имя ветки из имени plan-файла:
1. `Path(plan_file).stem` -- только имя файла без расширения
2. Regex `^[\d-]+` убирает date prefix (e.g., `2024-01-15-`)
3. Убирает ведущие дефисы
4. Если результат пустой (только дата) -- возвращает оригинальное имя без `.md`

Примеры:
- `2024-01-15-auth-refactor.md` -> `auth-refactor`
- `feature-login.md` -> `feature-login`
- `2024-01-15.md` -> `2024-01-15` (fallback на полное имя)

### prompt_description(colors: Colors) -> str

Запрашивает описание плана у пользователя через stdin. Используется когда нет существующих планов и можно перейти в plan creation mode. Возвращает пустую строку при Ctrl+C/Ctrl+D.

### NoPlansFoundError

Sentinel error. Проверяется через `isinstance()` в caller'е для переключения в plan creation mode.

---

## Связь между модулями

Модуль `git` импортирует `plan` для одной функции: `plan.extract_branch_name()` используется в `_prepare_plan_branch()` для извлечения имени ветки из plan-файла. Это единственная зависимость.

`move_plan_to_completed()` находится в модуле `git` (а не `plan`), потому что операция включает `git mv` + `git commit` -- это git-операция, не plan-парсинг.

## Соображения для Python-порта

### Git backend
Прямой эквивалент Go exec.CommandContext -- `subprocess.run()`. Все операции через subprocess для простоты.

### Path resolution
`os.path.abspath` / `pathlib.Path.resolve()`. `os.path.realpath()` для symlinks. `os.path.relpath()` для relative paths. Case-insensitive resolution: `os.listdir()` + casefold comparison.

### Plan parsing
Markdown парсинг через regex -- прямой перевод. Python `re` module полностью совместим. Итерация по `str.splitlines()`.

### JSON serialization
`dataclasses.asdict()` + `json.dumps()`.

### Error types
`NoPlansFoundError` -- custom exception class. `isinstance()` для проверки.
