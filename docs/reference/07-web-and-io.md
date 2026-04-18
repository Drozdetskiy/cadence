# Логирование прогресса и система ввода

Справочный документ по модулям `progress` и `input` для Python-порта rlx.

## Обзор

Два модуля обеспечивают I/O и пользовательский интерфейс:

- `progress` -- логирование прогресса в файл и stdout с timestamps, цветами и file locking
- `input` -- терминальный ввод: нумерованные списки, редактор, markdown-рендеринг

Ключевые модули:
- `rlx/progress/logger.py` -- Logger class, Config, файловый формат, timestamps
- `rlx/progress/colors.py` -- Colors class, phase-to-color mapping, RGB parsing
- `rlx/progress/flock.py` -- file locking (Unix fcntl.flock)
- `rlx/input/input.py` -- TerminalCollector, AskQuestion, AskDraftReview, read_line_with_context

---

## Логирование прогресса (progress)

### Config

```python
@dataclass
class Config:
    plan_file: str = ""        # имя файла плана (для генерации имени progress файла)
    plan_description: str = "" # описание плана для plan mode (для имени файла)
    mode: str = ""             # "full", "review", "plan"
    branch: str = ""           # текущая ветка
    no_color: bool = False     # отключить цвета
```

### Logger class

```python
class Logger:
    def __init__(self, cfg: Config, colors: Colors, holder: PhaseHolder):
        self._file: IO           # handle progress-файла
        self._stdout: IO         # sys.stdout
        self._start_time: datetime  # время создания (для elapsed time)
        self._holder: PhaseHolder   # текущая фаза для цвета
        self._colors: Colors        # конфигурация цветов
```

Процедура создания:
1. Генерация имени файла через `_progress_filename(cfg)`
2. Резолвинг в абсолютный путь
3. Создание parent директории (0o750)
4. Открытие файла с append mode (0o600)
5. Получение exclusive file lock (fcntl.flock)
6. Проверка наличия completion footer:
   - Есть footer -> truncate файл, записать свежий header
   - Нет footer, файл не пуст -> записать restart separator
   - Файл пуст -> записать header

### Публичные методы Logger

| Метод | Описание |
|---|---|
| `path() -> str` | абсолютный путь к progress файлу |
| `print(format, *args)` | timestamp + сообщение в файл и stdout (цвет текущей фазы) |
| `print_raw(format, *args)` | алиас для `print()` (с timestamp) |
| `print_section(section)` | заголовок секции: "\n--- {label} ---\n" |
| `print_aligned(text)` | timestamp на каждой строке, пропуск пустых, word wrap, list indent, подсветка сигналов |
| `error(format, *args)` | "ERROR: " prefix в красном |
| `warn(format, *args)` | "WARN: " prefix в жёлтом |
| `log_question(question, options)` | "QUESTION: " + "OPTIONS: opt1, opt2, ..." |
| `log_answer(answer)` | "ANSWER: " |
| `log_draft_review(action, feedback)` | "DRAFT REVIEW: " + опционально "FEEDBACK: " |
| `log_diff_stats(files, additions, deletions)` | "DIFFSTATS: files=F additions=A deletions=D" (только в файл) |
| `elapsed() -> str` | форматированное время с начала (>1h: truncate to minutes; <1h: truncate to seconds) |
| `close()` | footer с разделителем и timestamp завершения, release lock, закрытие файла |

### Формат файла

**Header (свежий старт):**
```
# RLX Progress Log
Plan: path/to/plan.md
Branch: feature-branch
Mode: full
Started: 2006-01-02 15:04:05
------------------------------------------------------------

```

**Timestamped строки:**
```
[YY-MM-DD HH:MM:SS] message text
[YY-MM-DD HH:MM:SS] ERROR: error message
[YY-MM-DD HH:MM:SS] WARN: warning message
[YY-MM-DD HH:MM:SS] QUESTION: what to do?
[YY-MM-DD HH:MM:SS] OPTIONS: opt1, opt2, opt3
[YY-MM-DD HH:MM:SS] ANSWER: opt1
[YY-MM-DD HH:MM:SS] DRAFT REVIEW: accept
[YY-MM-DD HH:MM:SS] FEEDBACK: looks good
[YY-MM-DD HH:MM:SS] DIFFSTATS: files=5 additions=42 deletions=10
```

**Section headers:** `\n--- section label ---\n`

**Сигналы:** `<<<RLX:SIGNAL_NAME>>>` -- рендерятся в signal color

**Restart separator** (при append к незавершённому файлу):
```


--- restarted at 2006-01-02 15:04:05 ---


```

**Footer (при close):**
```
------------------------------------------------------------
Completed: 2006-01-02 15:04:05 (1h23m45s)
```

### Fresh start: усечение завершённых файлов

Определение завершённости (`_is_progress_completed`):
1. Читаются последние ~256 байт файла
2. Ищется паттерн: 60-дефисный разделитель + строка "Completed:"
3. Простая проверка на "Completed:" дала бы false positive, если Claude упомянул это слово в output

Порядок проверки:
1. Lock приобретается ДО stat (предотвращает TOCTOU race)
2. Если файл > 0 и содержит footer -> truncate
3. Если файл > 0 без footer -> restart separator, существующий контент сохраняется
4. Если файл пуст -> свежий header

### Генерация имени файла (_progress_filename)

| Ситуация | Шаблон |
|---|---|
| Plan mode с описанием | `progress-plan-{sanitized}.txt` |
| Plan file + full mode | `progress-{planFileBase}.txt` |
| Plan file + review mode | `progress-{planFileBase}-review.txt` |
| Без plan + plan mode | `progress-plan.txt` |
| Без plan + review mode | `progress-review.txt` |
| Без plan + full mode | `progress.txt` |

Санитизация (`_sanitize_plan_name`): lowercase, пробелы в дефисы, только alphanumeric + дефисы, collapse, trim, limit 50 chars, fallback "unnamed".

### File locking

**Unix (fcntl.flock):**

```python
def lock_file(f: IO) -> None      # fcntl.flock(fd, LOCK_EX) -- blocking exclusive lock
def unlock_file(f: IO) -> None    # fcntl.flock(fd, LOCK_UN)
def try_lock_file(f: IO) -> bool  # LOCK_EX|LOCK_NB -- non-blocking
```

`try_lock_file` возвращает:
- `True` -- lock приобретён (файл не был залочен)
- `False` -- файл залочен другим процессом (EWOULDBLOCK)

Lock приобретённый через try_lock_file немедленно освобождается (цель -- только проверка).

File locking обеспечивает эксклюзивный доступ к progress-файлу -- два процесса rlx не будут писать в один файл одновременно.

### Colors class

```python
class Colors:
    def __init__(self, cfg: ColorConfig):
        self._task: Style       # фазовый цвет
        self._review: Style     # фазовый цвет
        self._warn: Style       # служебный цвет
        self._err: Style        # служебный цвет
        self._signal: Style     # служебный цвет
        self._timestamp: Style  # UI цвет
        self._info: Style       # UI цвет
        self._phases: dict[Phase, Style]  # mapping для for_phase()
```

Все цвета парсятся из RGB формата ("r,g,b", значения 0-255). Ошибка при невалидном значении -- это ошибка конфигурации, не runtime.

Phase-to-color mapping:

| Phase | Цвет |
|---|---|
| PhaseTask | task (green) |
| PhaseReview | review |
| PhasePlan | task (reuses green) |
| PhaseFinalize | task (reuses green) |

Методы:
- `for_phase(p: Phase) -> Style` -- цвет для фазы (fallback: task)
- `timestamp() -> Style`
- `warn() -> Style`
- `error() -> Style`
- `signal() -> Style`
- `info() -> Style`

---

## Система ввода (input)

### TerminalCollector class

```python
class TerminalCollector:
    def __init__(self, no_color: bool = False):
        self._stdin: IO = sys.stdin
        self._stdout: IO = sys.stdout
        self._no_color: bool
```

### ask_question

`ask_question(question: str, options: list[str]) -> str`

Предлагает выбор из опций через нумерованный список:
1. Добавляет "Other (type your own answer)" в конец списка
2. Фильтрует входящие опции от коллизий с sentinel "Other"
3. Выводит нумерованный список ("Enter number (1-N):")
4. При выборе "Other" -- промпт для свободного ввода

### ask_yes_no

`ask_yes_no(prompt: str) -> bool`

Промпт с `[y/N]` форматом:
- "y", "yes" (case-insensitive) -> True
- Всё остальное -> False
- EOF, пустой ввод, ошибки чтения -> False

### ask_draft_review

`ask_draft_review(question: str, plan_content: str) -> tuple[str, str]`

Показывает план для ревью:
1. Рендеринг markdown через rich (если no_color=False)
2. Отображение с рамкой
3. Нумерованное меню с 4 опциями:
   - **Accept** -- возвращает `ACTION_ACCEPT`, feedback=""
   - **Revise** -- промпт для текста ревизии, возвращает `ACTION_REVISE` + feedback
   - **Interactive review** -- открывает $EDITOR, вычисляет unified diff
   - **Reject** -- возвращает `ACTION_REJECT`, feedback=""

Action константы:
```python
ACTION_ACCEPT = "accept"
ACTION_REVISE = "revise"
ACTION_REJECT = "reject"
```

Interactive review flow:
1. Открывает $EDITOR с temp файлом (`rlx-plan-*.md`)
2. `difflib.unified_diff(original, edited)` -- unified diff с контекстом
3. Если diff пуст -- "no changes detected", повтор меню
4. Если diff не пуст -- возвращает `ACTION_REVISE` с diff, обёрнутым в инструкции для Claude

Порядок поиска редактора: `$VISUAL` -> `$EDITOR` -> `vi`. Поддерживает редакторы с аргументами (e.g., `"code --wait"`).

### read_line_with_context

`read_line_with_context(reader: IO) -> str`

Чтение строки из reader с поддержкой прерывания:
1. Проверка на отмену
2. Чтение строки через `readline()`

Позволяет Ctrl+C (SIGINT) прервать блокирующий stdin read.

### Граф вызовов

```
ask_question
  └─ _select_with_numbers() → _read_custom_answer() → read_line_with_context()

ask_draft_review
  ├─ _render_markdown() [rich]
  ├─ _select_with_numbers() [в retry loop]
  ├─ read_line_with_context() [для feedback]
  ├─ _open_editor() [subprocess $EDITOR]
  └─ _compute_diff() [difflib]

ask_yes_no
  └─ read_line_with_context()
```

---

## Соображения для Python-порта

### File locking
`fcntl.flock` (Unix). На Windows -- `msvcrt.locking` или no-op.

### Progress file формат
Формат текстовый, парсится построчно. `open(file, 'a')` для append, `fcntl.flock` для locking, `datetime.strftime` для timestamps.

### Terminal input
Numbered selection -- `input()` с валидацией. Markdown rendering -- `rich.markdown.Markdown`. Editor -- `subprocess.run([$EDITOR, tmpfile])`.

### Цвета
`rich` (full RGB support). `rich.console.Console` с `style` параметрами.

### Word wrap и list indent
`shutil.get_terminal_size()`. Word wrap -> `textwrap.fill()`. List indent -- тривиально.

### Unified diff
`difflib.unified_diff` (stdlib). Формат совпадает.
