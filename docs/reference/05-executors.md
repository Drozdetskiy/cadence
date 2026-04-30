# Executor Layer

Справочный документ по слою исполнителей для планирования Python-порта.

## Обзор

Модуль executor предоставляет единственный исполнитель -- ClaudeExecutor -- для запуска Claude CLI. Executor инкапсулирует запуск процесса, потоковый парсинг вывода, обнаружение сигналов и паттернов ошибок. Executor не знает об оркестрации -- он получает prompt и возвращает Result.

Ключевые модули:
- `executor/claude_executor.py` -- CommandRunner protocol, ClaudeExecutor, парсинг JSON stream, detect_signal(), match_pattern(), типы ошибок
- `executor/line_reader.py` -- чтение строк из process.stdout с cancellation
- `executor/process_group.py` -- управление process group: start_new_session, SIGTERM/SIGKILL

## Result dataclass

Единый тип результата:

```python
@dataclass
class Result:
    output: str = ""          # весь накопленный текстовый вывод
    recent_text: str = ""     # последние 10 текстовых блоков, используется для pattern matching
    signal: str = ""          # обнаруженный сигнал (COMPLETED, FAILED, и т.д.) или пустая строка
    error: Exception | None = None  # ошибка исполнения, если была
    idle_timed_out: bool = False    # True когда сработал idle timeout (процесс убит, но не по запросу пользователя)
```

### Поле recent_text

Последние `RECENT_BLOCK_COUNT` (10) текстовых блоков, объединённых в хронологическом порядке. Используется для pattern matching вместо полного вывода -- это предотвращает false positive, когда Claude в начале сессии анализирует текст, содержащий фразы вроде "rate limit".

Реализация: кольцевой буфер (collections.deque(maxlen=10)). При сборке recent_text элементы объединяются в хронологическом порядке.

## Типы ошибок

### PatternMatchError

Возвращается когда в выводе обнаружен настроенный error pattern:

```python
class PatternMatchError(Exception):
    def __init__(self, pattern: str, help_cmd: str):
        self.pattern = pattern    # паттерн, который сработал
        self.help_cmd = help_cmd  # команда для справки (e.g., "claude /usage")
```

### LimitPatternError

Возвращается когда обнаружен паттерн rate limit. Если настроен `wait_on_limit`, вызывающий код (processor) ретраит вместо выхода:

```python
class LimitPatternError(Exception):
    def __init__(self, pattern: str, help_cmd: str):
        self.pattern = pattern    # паттерн, который сработал
        self.help_cmd = help_cmd  # команда для справки
```

### Приоритет проверки паттернов

1. Сначала проверяются limit patterns
2. Если limit pattern найден -- возвращается `LimitPatternError`
3. Затем проверяются error patterns
4. Если error pattern найден -- возвращается `PatternMatchError`

Функция `match_pattern(output, patterns)` -- case-insensitive substring search. Пустые паттерны и пробельные строки пропускаются.

## ClaudeExecutor

Единственный executor для всех фаз (task, review first, review second, finalize, plan creation).

### Поля класса

```python
class ClaudeExecutor:
    command: str                        # команда для запуска, по умолчанию "claude"
    args: str                           # дополнительные аргументы (строка через пробел), по умолчанию стандартные
    model: str                          # override модели ("opus", "sonnet", "haiku"); пустая = default CLI
    output_handler: Callable[[str], None] | None  # callback для каждого текстового чанка
    debug: bool                         # включить debug вывод
    error_patterns: list[str]           # паттерны ошибок
    limit_patterns: list[str]           # паттерны rate limit (проверяются перед error patterns)
    idle_timeout: float                 # убить сессию после молчания (сек), 0 = отключено
    cmd_runner: CommandRunner | None    # для тестирования, None = реальный runner
```

### Построение команды

1. Если `command` пуст, используется `"claude"`
2. Если `args` не пуст, парсится через `split_args()` (поддержка кавычек и escape) или `shlex.split()`
3. Если `args` пуст, используются дефолтные флаги:
   - `--dangerously-skip-permissions`
   - `--output-format stream-json`
   - `--verbose`
4. Если `model` не пуст, добавляются `--model <value>`
5. Всегда добавляется `--print` в конец (non-interactive mode)
6. Prompt передаётся через stdin (не через `-p` аргумент) -- обходит лимит Windows 8191 символов

### split_args()

Парсер строки аргументов в список. Поддерживает:
- Одинарные и двойные кавычки (не включаются в результат)
- Escape через backslash
- Пробелы внутри кавычек сохраняются

Альтернатива: `shlex.split()` из стандартной библиотеки Python.

### Фильтрация окружения

`filter_env()` удаляет из `os.environ`:
- `ANTHROPIC_API_KEY` -- claude использует другую аутентификацию
- `CLAUDECODE` -- предотвращает ошибки вложенных сессий

### Idle Timeout

Механизм обнаружения зависших сессий:

1. Если `idle_timeout > 0`, создаётся `threading.Timer(idle_timeout, kill_process)`
2. На каждой строке вывода: timer.cancel() + создание нового timer (reset)
3. Если timer срабатывает -- процесс убивается через process.terminate()

При срабатывании idle timeout:
- Процесс убит, но это не по запросу пользователя
- Перед возвратом проверяются limit/error patterns (idle может сработать после rate limit сообщения)
- Устанавливается `result.idle_timed_out = True`
- `result.error` очищается (не ошибка, а нормальное завершение idle сессии)

### Парсинг JSON Stream

Метод `parse_stream(idle_touch)` читает вывод claude построчно из `process.stdout`. Каждая строка парсится как JSON dict:

```python
# Структура stream event (JSON):
{
    "type": str,                    # тип события
    "message": {
        "content": [
            {"type": str, "text": str}
        ]
    },
    "content_block": {
        "type": str,
        "text": str
    },
    "delta": {
        "type": str,
        "text": str
    },
    "result": str | dict            # может быть string или {"output": "..."}
}
```

### Извлечение текста из событий (extract_text)

| Тип события | Логика извлечения |
|---|---|
| `"assistant"` | Все элементы `message["content"]` с `type == "text"` -- объединяются в строку |
| `"content_block_delta"` | Если `delta["type"] == "text_delta"` -- возвращается `delta["text"]` |
| `"message_stop"` | Первый элемент `message["content"]` с `type == "text"` |
| `"result"` | Пробуется как string (session summary -- пропускается, контент уже стримился). Затем как `{"output": "..."}` -- возвращается output |

Не-JSON строки записываются как есть в output и recent blocks (с debug-логом, если включён).

### Обнаружение сигналов (detect_signal)

Функция `detect_signal(text)` ищет в тексте известные сигналы через `in`:
- `<<<CADENCE:ALL_TASKS_DONE>>>` (Completed)
- `<<<CADENCE:TASK_FAILED>>>` (Failed)
- `<<<CADENCE:REVIEW_DONE>>>` (ReviewDone)
- `<<<CADENCE:PLAN_READY>>>` (PlanReady)

Примечание: `Question` и `PlanDraft` не проверяются в `detect_signal` -- они обрабатываются в processor'е через отдельные signal helpers.

Последний обнаруженный сигнал перезаписывает предыдущий (нет аккумуляции).

### Обработка ошибок при выходе

Логика после `process.wait()`:
1. Idle timeout path: если процесс убит по idle timeout -- проверить patterns, вернуть idle_timed_out=True
2. Если `process.returncode != 0`:
   - Если процесс был убит пользователем (cancelled) -- вернуть cancellation error (обходит pattern checks)
   - Если output пуст -- вернуть ошибку напрямую ("claude exited with error")
   - Если output не пуст и signal пуст -- claude не сделал полезной работы, вернуть ошибку
   - Если output не пуст и signal не пуст -- работа сделана, игнорировать exit code
3. Проверить limit patterns (приоритет)
4. Проверить error patterns
5. Вернуть результат

Важный нюанс: cancellation paths обходят pattern checks. Это предотвращает ситуацию, когда cancellation маскируется как pattern match.

## CommandRunner protocol

Интерфейс для абстракции запуска процессов (используется для тестирования):

```python
class CommandRunner(Protocol):
    def run(self, name: str, *args: str) -> tuple[IO[str], Callable[[], int]]:
        """
        Returns:
            output: readable stream (stdout + stderr merged)
            wait: callable that waits for process exit, returns returncode
        """
        ...
```

Реальная реализация использует `subprocess.Popen` с `start_new_session=True`.

## Чтение строк из process.stdout

В Python эквивалент Go `readLines()` -- итерация по `process.stdout`:

```python
for line in process.stdout:
    line = line.rstrip('\n').rstrip('\r')
    handler(line)
```

Особенности:
- `subprocess.Popen(stdout=PIPE, stderr=STDOUT, text=True)` -- stdout + stderr merged, text mode
- Итерация по `process.stdout` блокируется до получения строки или EOF
- Cancellation: idle timeout убивает процесс через process.terminate(), что закрывает pipe и прерывает итерацию
- `line.rstrip()` для удаления trailing newlines
- EOF завершает итерацию естественно

## Управление процессами

### Unix

Полное управление process group через subprocess.Popen:

**Запуск процесса:**
```python
process = subprocess.Popen(
    cmd,
    stdin=subprocess.PIPE,
    stdout=subprocess.PIPE,
    stderr=subprocess.STDOUT,
    text=True,
    start_new_session=True,  # эквивалент Go Setsid: true
    env=filtered_env,
)
```

`start_new_session=True` -- создаёт новую сессию, отделяя child от controlling terminal родителя. Предотвращает SIGTTIN/SIGTTOU сигналы от потомков. Child становится session leader своей process group.

**ProcessGroupCleanup class:**
- `process: subprocess.Popen` -- процесс
- `_killed: bool` -- защита от повторного kill

**Lifecycle:**
1. Создание: ProcessGroupCleanup(process)
2. `kill_process_group()`:
   - `os.killpg(process.pid, signal.SIGTERM)` -- отправка всей process group
   - Если ProcessLookupError (group уже не существует) -- early return
   - `time.sleep(0.1)` -- graceful shutdown delay
   - `os.killpg(process.pid, signal.SIGKILL)` -- force kill
3. `wait() -> int`:
   - `process.wait()`
   - `kill_process_group()` -- убивает orphaned descendants (node subagents, MCP servers)
   - return process.returncode

### Windows

Упрощённая версия:

- `start_new_session` не поддерживается так же как на Unix
- `process.terminate()` -- только прямой процесс (не child processes)
- Нет post-exit orphan cleanup (потребовались бы Job Objects)
- Нет graceful shutdown (SIGTERM не поддерживается)

Примечание: SIGQUIT (Ctrl+\) break-механизм не поддерживается на Windows.

## Соображения для Python-порта

### Парсинг JSON stream
`for line in process.stdout:` + `json.loads(line)`. Построчное чтение блокируется до получения строки. Cancellation через kill процесса (pipe закрывается, итерация завершается).

### Process group management
`subprocess.Popen(start_new_session=True)` + `os.killpg(process.pid, signal.SIGTERM)` -- прямой эквивалент Go Setsid + syscall.Kill(-pid). На Windows -- `CREATE_NEW_PROCESS_GROUP` или Job Objects.

### Idle timeout
`threading.Timer` с cancel/restart на каждой строке. При срабатывании -- process.terminate() + os.killpg(). Timer создаётся заново после каждого cancel (threading.Timer не поддерживает reset).

### Pattern matching
`match_pattern()` -- тривиальная функция. Case-insensitive substring через `pattern.lower() in text.lower()`.
