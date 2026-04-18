# Processor / Orchestration Layer

Справочный документ по оркестрационному слою для планирования Python-порта.

## Обзор

Модуль processor -- ядро rlx. Содержит `Runner`, который управляет всем жизненным циклом исполнения: от запуска задач до review и finalize. Runner не знает о CLI, конфигурационных файлах или git-операциях напрямую -- он работает через интерфейсы.

Ключевые модули:
- `processor/runner.py` -- Runner class, все run-методы, циклы итераций
- `processor/prompts.py` -- шаблонная система промптов, подстановка переменных, раскрытие агентов
- `processor/signals.py` -- парсинг сигналов (QUESTION, PLAN_DRAFT), helper-функции

## Runner class и зависимости

```python
class Runner:
    cfg: Config                          # конфигурация Runner
    log: Logger                          # логирование прогресса
    claude: Executor                     # executor для task phase
    review_claude: Executor              # executor для review phases (может отличаться моделью)
    git: GitChecker                      # проверка HEAD hash / diff fingerprint
    input_collector: InputCollector      # пользовательский ввод (plan creation)
    phase_holder: PhaseHolder            # thread-safe текущая фаза
    iteration_delay: float               # пауза между итерациями (default 2.0 сек)
    task_retry_count: int                # количество retry при FAILED (default 1)
    wait_on_limit: float                 # время ожидания при rate limit (сек)
    break_event: threading.Event         # event для break-сигнала (Ctrl+\)
    pause_handler: Callable[[],  bool]   # callback pause/resume
    last_session_timed_out: bool         # флаг: последняя сессия завершилась по timeout
    task_phase_override: Callable | None # test seam
```

### Config dataclass

```python
@dataclass
class Config:
    plan_file: str              # путь к plan-файлу
    plan_description: str       # описание для plan creation mode
    progress_path: str          # путь к файлу прогресса
    mode: Mode                  # режим исполнения
    max_iterations: int         # макс итераций task phase
    debug: bool                 # debug output
    no_color: bool              # отключить цвета
    iteration_delay_ms: int     # задержка между итерациями (ms)
    task_retry_count: int       # retry при FAILED
    claude_model: str           # модель для task phase
    review_model: str           # модель для review phases
    finalize_enabled: bool      # включен ли finalize step
    default_branch: str         # default branch (из git)
    app_config: AppConfig       # полный application config
```

### Протоколы (интерфейсы)

Runner определяет 4 протокола:

**Executor** -- запуск CLI и получение результата:
```python
class Executor(Protocol):
    def run(self, prompt: str) -> Result: ...
```

**Logger** -- логирование прогресса с поддержкой structured секций и Q&A:
```python
class Logger(Protocol):
    def print(self, format: str, *args) -> None: ...        # форматированная строка с timestamp
    def print_raw(self, format: str, *args) -> None: ...    # без timestamp
    def print_section(self, section: Section) -> None: ...  # заголовок секции
    def print_aligned(self, text: str) -> None: ...         # выравненный вывод (для streaming)
    def log_question(self, question: str, options: list[str]) -> None: ...  # Q&A для plan creation
    def log_answer(self, answer: str) -> None: ...
    def log_draft_review(self, action: str, feedback: str) -> None: ...
    def path(self) -> str: ...                              # путь к файлу прогресса
```

**InputCollector** -- интерактивный ввод для plan creation:
```python
class InputCollector(Protocol):
    def ask_question(self, question: str, options: list[str]) -> str: ...
    def ask_draft_review(self, question: str, plan_content: str) -> tuple[str, str]: ...
```

**GitChecker** -- инспекция git-состояния для review loop optimization:
```python
class GitChecker(Protocol):
    def head_hash(self) -> str: ...              # текущий HEAD commit hash
    def diff_fingerprint(self) -> str: ...       # хеш рабочего дерева diff
```

### Executors dataclass

Группирует executor-зависимости:
```python
@dataclass
class Executors:
    claude: Executor                     # обязателен: task phase
    review_claude: Executor | None       # опционально: отдельная модель для reviews
```

Если `review_claude` не задан (None), используется тот же executor что и claude.

### Конструкторы

`Runner(cfg, log, holder)` -- основной конструктор:
1. Создает `ClaudeExecutor` с параметрами из AppConfig (command, args, error/limit patterns, idle timeout, model)
2. Если review_model отличается от claude_model, создает отдельный review executor
3. Вызывает `Runner.from_executors()`

`Runner.from_executors(cfg, log, execs, holder)` -- конструктор с готовыми executors (для тестирования):
1. Устанавливает `iteration_delay` из config или default (2.0 сек)
2. Устанавливает `task_retry_count` с учетом explicit zero vs not set
3. Устанавливает `wait_on_limit` из AppConfig
4. Если review_claude is None, копирует claude executor

### Setter-методы

```
set_input_collector(c)   -- устанавливает input collector (plan creation mode)
set_git_checker(g)       -- устанавливает git checker (review loops)
set_break_event(event)   -- устанавливает break event (Ctrl+\, через threading.Event)
set_pause_handler(fn)    -- устанавливает callback pause/resume
```

## Методы режимов исполнения

`run()` -- entry point, dispatches по `cfg.mode`:

### run_full()

Полный pipeline: tasks -> review_first -> review_loop -> finalize.

```
1. PhaseTask: run_task_phase()
   - при UserAbortedError: логирует и возвращает UserAbortedError
   - при ошибке: оборачивает в "task phase: ..."

2. PhaseReview: run_claude_review(ReviewFirstPrompt)
   - section "claude review 0: all findings"
   - single pass, 5 агентов

3. PhaseReview: run_claude_review_loop()
   - review loop (critical/major)

4. run_finalize()
```

Требует: plan_file != ""

### run_review_only()

Review pipeline без task phase.

```
1. PhaseReview: run_claude_review(ReviewFirstPrompt)
2. PhaseReview: run_claude_review_loop()
3. run_finalize()
```

### run_tasks_only()

Только task phase, без reviews.

```
1. PhaseTask: run_task_phase()
```

Требует: plan_file != ""

### run_plan_creation()

Интерактивное создание плана через Q&A с Claude.

```
max_plan_iterations = max(5, max_iterations // 5)
last_revision_feedback = ""

loop 1..max_plan_iterations:
  1. print_section(PlanIterationSection(i))
  2. Собрать prompt = build_plan_prompt()
  3. Если есть last_revision_feedback: добавить "PREVIOUS DRAFT FEEDBACK: ..."
  4. run_with_limit_retry(claude.run, prompt, "claude")

  Обработка результата:
  - Error: handle_pattern_match_error, return
  - FAILED signal: return error
  - PLAN_READY signal: return (успех)
  - Session timeout: skip output parsing, retry (preserve last_revision_feedback)

  Если не timed out:
  - Clear last_revision_feedback (если был)
  - Проверить PLAN_DRAFT: handle_plan_draft(output)
    - accept: continue (last_revision_feedback = "")
    - revise: continue (last_revision_feedback = feedback)
    - reject: raise UserRejectedPlanError
  - Проверить QUESTION: handle_plan_question(output)
    - question handled: continue
  - Иначе: continue (ждем следующий iteration)
```

Требует: plan_description != "", input_collector is not None

## Фазы исполнения (детальное описание)

### Task phase: run_task_phase()

Цикл исполнения задач из плана. Каждая итерация -- одна задача (один Task section).

```
prompt = replace_prompt_variables(TaskPrompt)
retry_count = 0

loop i = 1..max_iterations:
  1. Определить номер задачи:
     - task_num = next_plan_task_position() (из плана, 1-indexed)
     - если 0: fallback на i (loop counter)
  2. print_section(TaskIterationSection(task_num))

  3. Создать break scope:
     - отдельный threading.Event, проверяемый при break
     - для отмены только текущей сессии при Ctrl+\

  4. result = run_with_limit_retry(claude.run, prompt, "claude")

  5. Проверить manual break:
     - is_break(): break_event.is_set() и основной поток не отменен

  6. Если manual break:
     - break_event.clear() (очистить pending signal)
     - если pause_handler is None или not pause_handler(): raise UserAbortedError
     - break_event.clear() (очистить signal полученный во время pause prompt)
     - i -= 1 (сохранить iteration budget, перезапустить ту же задачу)
     - retry_count = 0
     - continue

  7. Если result.error:
     - handle_pattern_match_error -> return
     - иначе: raise error

  8. Если COMPLETED signal:
     - has_uncompleted_tasks(): проверить план на наличие [ ]
     - если есть uncompleted: warning, continue
     - если все done: return

  9. Если FAILED signal:
     - если retry_count < task_retry_count: retry_count += 1, sleep, continue
     - иначе: raise error

  10. Сброс retry_count = 0
  11. sleep(iteration_delay)
```

Ключевые особенности:
- Prompt один и тот же каждую итерацию -- Claude перечитывает план из файла
- next_plan_task_position() парсит план и находит первый uncompleted task section
- has_uncompleted_tasks() проверяет только Task sections (не Success criteria/Overview/Context)
- Для malformed plans (checkboxes без task headers): проверяет файл целиком
- break-resume: та же задача перезапускается с fresh session, план перечитывается

### Review phase: run_claude_review(prompt)

Одиночный review pass. Используется для "review 0: all findings" с ReviewFirstPrompt (5 агентов).

```
1. result = run_with_limit_retry(review_claude.run, prompt, "claude")
2. Error: handle_pattern_match_error -> raise
3. FAILED signal: raise error
4. REVIEW_DONE signal: ok
5. Нет REVIEW_DONE: warning "did not complete cleanly", continue
```

### Review loop: run_claude_review_loop()

Итеративный review loop с ReviewSecondPrompt (2 агента: critical/major findings only).

```
max_review_iterations = max(3, max_iterations // 10)

loop i = 1..max_review_iterations:
  1. print_section(ClaudeReviewSection(i, ": critical/major"))
  2. head_before = head_hash() (для no-commit detection)
  3. result = run_with_limit_retry(review_claude.run, ReviewSecondPrompt, "claude")
  4. Error: handle_pattern_match_error -> return
  5. FAILED signal: raise error
  6. REVIEW_DONE signal: return ("no more findings")
  7. last_session_timed_out: skip HEAD check, continue
  8. HEAD unchanged (head_after == head_before): return ("no changes detected")
  9. log "issues fixed, running another review iteration..."
  10. sleep(iteration_delay)

max iterations reached: log warning, return
```

Логика no-commit detection: если Claude не сделал коммитов, значит нечего было исправлять. Session timeout обходит эту проверку (сессия могла быть убита до коммита).

### Finalize: run_finalize()

Опциональный шаг после успешных reviews.

```
if not finalize_enabled: return

PhaseFinalize
print_section("finalize step")
prompt = replace_prompt_variables(FinalizePrompt)
result = run_with_limit_retry(review_claude.run, prompt, "claude")

Error handling:
- KeyboardInterrupt / cancellation: propagate (user abort)
- PatternMatchError / LimitPatternError: log via handle_pattern_match_error, return (best-effort)
- other error: log, return (best-effort)
- FAILED signal: log, return (best-effort)
- success: log "finalize step completed"
```

Best-effort семантика: единственное исключение -- cancellation (пользователь хочет прервать). Все остальные ошибки логируются, но не propagate.

## Session timeout и idle timeout

### Session timeout

`run_with_session_timeout(run, prompt, tool_name)`:

```
Если session_timeout <= 0 или tool_name != "claude":
  result = run(prompt)
  Если result.idle_timed_out and signal == "":
    last_session_timed_out = True  # treat как session timeout для review loops
  return result

# Запуск с таймаутом через threading.Timer
result = run(prompt)  # с отдельным threading.Timer на session_timeout

Если session timed out:
  result.error = None
  result.signal = ""  # нельзя доверять partial session
  last_session_timed_out = True

Если result.idle_timed_out and signal == "":
  last_session_timed_out = True  # idle timeout без signal = session timeout behavior
```

`last_session_timed_out` используется в:
- run_claude_review_loop(): skip HEAD-check и retry (не путать timeout с "ничего не нашел")

### Idle timeout

Реализован в `ClaudeExecutor.run()` (модуль executor), не в processor.
Processor обрабатывает результат через `result.idle_timed_out` flag.

Если idle timeout сработал без signal: `last_session_timed_out = True`.
Это нужно потому что idle timeout без signal выглядит как "ничего не нашел" для review loops, но на самом деле сессия "зависла".

## Rate limit retry: run_with_limit_retry

```
loop:
  result = run_with_session_timeout(run, prompt, tool_name)

  Если not error: return result
  Если не LimitPatternError: return result (не retry)
  Если wait_on_limit <= 0: return result (нет wait config)

  log "rate limit detected, waiting..."
  sleep_with_cancel(wait_on_limit)
  -- retry indefinitely
```

Порядок проверок:
1. LimitPatternError: если есть wait -> retry, если нет wait -> return (упадет в error handling)
2. PatternMatchError (обычная ошибка): return без retry
3. Другие ошибки: return без retry

Retry indefinitely: цикл не ограничен по количеству попыток, только по cancellation.

## Break / pause / resume механизм

### break_event (threading.Event)

`threading.Event`, устанавливаемый при получении break-сигнала (Ctrl+\ на Unix).
Если break_event is None: break-механизм отключен.

### is_break() -> bool

Определяет, был ли break: `break_event.is_set()` и основной поток не отменен.

### clear_break()

`break_event.clear()` -- сброс события.
Вызывается после pause+resume чтобы предотвратить немедленную отмену следующей итерации.
Не вызывается на обычных границах итераций -- сохраняет legitimate Ctrl+\ между итерациями.

### Поведение по фазам

Task phase:
- break_event проверяется на каждой итерации
- на break: kill текущей сессии -> pause_handler -> resume (i -= 1) или abort (UserAbortedError)
- clear_break() после pause prompt (очистка pending signal)

Claude review loop:
- Нет break-проверки -- review loop не прерывается по Ctrl+\
- (только cancellation через SIGINT/KeyboardInterrupt)

## Расчет итераций

Константы:
```
MIN_REVIEW_ITERATIONS    = 3     # минимум для claude review
REVIEW_ITERATION_DIVISOR = 10    # review iterations = max_iterations // 10
MIN_PLAN_ITERATIONS      = 5     # минимум для plan creation
PLAN_ITERATION_DIVISOR   = 5     # plan iterations = max_iterations // 5
```

При max_iterations = 50 (default):
- Task: 1..50
- Review: max(3, 50 // 10) = 5
- Plan: max(5, 50 // 5) = 10

## Система промптов

### Шаблонные переменные

Определены в `processor/prompts.py`:

| Переменная                     | Значение                                          | Где используется              |
|-------------------------------|---------------------------------------------------|-------------------------------|
| `{{PLAN_FILE}}`               | путь к план-файлу или "(no plan file...)"         | все промпты                   |
| `{{PROGRESS_FILE}}`           | путь к progress файлу или "(no progress file...)" | все промпты                   |
| `{{GOAL}}`                    | "implementation of plan at ..." или "current branch vs ..." | все промпты     |
| `{{DEFAULT_BRANCH}}`          | имя default branch или "master"                   | все промпты                   |
| `{{PLANS_DIR}}`               | директория планов или "docs/plans"                | все промпты (base variable)   |
| `{{PLAN_DESCRIPTION}}`        | описание плана (user input)                       | make_plan prompt              |
| `{{agent:name}}`              | раскрывается в Task tool instructions             | review промпты                |

### Иерархия замены

Два уровня функций замены:

1. `replace_base_variables(prompt)` -- базовые: PLAN_FILE, PROGRESS_FILE, GOAL, DEFAULT_BRANCH, PLANS_DIR
2. `replace_prompt_variables(prompt)` -- базовые + agent references + commit trailer

Порядок в replace_prompt_variables:
1. replace_base_variables()
2. expand_agent_references() -- раскрытие агентских ссылок
3. append_commit_trailer_instruction()

### Agent expansion

`expand_agent_references(prompt)`:
- Regex: `\{\{agent:([a-zA-Z0-9_-]+)\}\}`
- Строит dict name -> CustomAgent из app_config.custom_agents
- Для каждого match:
  - Если агент не найден: warning, оставить reference as-is
  - Если найден: replace_base_variables() на контент агента, затем format_agent_expansion()
  - Рекурсия не поддерживается: агентский контент не проходит через expand_agent_references()

```
format_agent_expansion(prompt, opts):
  subagent = opts.agent_type or "general-purpose"
  model_clause = f" with model={opts.model}" если opts.model задан
  -> "Use the Task tool{model_clause} to launch a {subagent} agent with this prompt:
      \"{prompt}\"
      Report findings only - no positive observations."
```

### Commit trailer

`append_commit_trailer_instruction(prompt)`:
- Если app_config.commit_trailer пуст: return prompt unchanged
- Иначе: добавляет instruction "When making git commits, add the following trailer..."
- Вызывается ОДИН РАЗ на финальном собранном prompt (не внутри agent expansion)

### Build-функции для промптов

| Функция                         | Prompt source              | Специальные переменные          |
|--------------------------------|----------------------------|---------------------------------|
| build_plan_prompt()             | MakePlanPrompt             | {{PLAN_DESCRIPTION}}            |

## Парсинг сигналов

Определены в `processor/signals.py`.

### Helper-функции

```
is_review_done(signal)  -> signal == "<<<RLX:REVIEW_DONE>>>"
is_plan_ready(signal)   -> signal == "<<<RLX:PLAN_READY>>>"
```

### QUESTION signal

Формат в output:
```
<<<RLX:QUESTION>>>
{"question": "...", "options": ["...", "..."]}
<<<RLX:END>>>
```

`parse_question_payload(output)`:
1. Проверить наличие `<<<RLX:QUESTION>>>` подстроки
2. Regex extract JSON между QUESTION и END маркерами
3. json.loads в QuestionPayload dataclass
4. Валидация: question != "", options не пустой

### PLAN_DRAFT signal

Формат в output:
```
<<<RLX:PLAN_DRAFT>>>
# Plan content...
<<<RLX:END>>>
```

`parse_plan_draft_payload(output)`:
1. Проверить наличие `<<<RLX:PLAN_DRAFT>>>` подстроки
2. Regex extract content между PLAN_DRAFT и END маркерами
3. strip(), проверить непустоту

### Draft review handling

`handle_plan_draft(output) -> DraftReviewResult`:
1. parse_plan_draft_payload(output)
2. Если нет draft: return DraftReviewResult(handled=False)
3. input_collector.ask_draft_review("Review the plan draft", plan_content)
4. log_draft_review(action, feedback)
5. Match action:
   - "accept": return DraftReviewResult(handled=True) (continue to PLAN_READY)
   - "revise": return DraftReviewResult(handled=True, feedback=feedback)
   - "reject": return DraftReviewResult(handled=True, error=UserRejectedPlanError)

## Вспомогательные функции

### Plan file resolution

`resolve_plan_file_path()`:
1. Если plan_file пуст: return ""
2. Проверить Path(plan_file).exists():
   - exists: return plan_file
   - permission error: return plan_file
3. Проверить completed/ subdirectory
4. Fallback: return original plan_file

### has_uncompleted_tasks()

Проверяет наличие uncompleted checkboxes в Task sections плана:
1. resolve_plan_file_path()
2. parse_plan_file()
3. Итерация по tasks: has_uncompleted_actionable_work()
4. Malformed plans (нет task headers): file_has_uncompleted_checkbox()

Игнорирует checkboxes в Success criteria, Overview, Context -- для корректного ALL_TASKS_DONE.

### next_plan_task_position()

Возвращает 1-indexed позицию первого uncompleted task:
1. parse_plan_file()
2. Итерация по tasks: has_uncompleted_actionable_work()
3. Return i + 1 (1-indexed) или 0 если нет

### sleep_with_cancel(duration)

Cancelable sleep через threading.Event.wait(timeout) или аналог.

## Sentinel errors

```python
class UserAbortedError(Exception):
    """break + decline resume"""
    pass

class UserRejectedPlanError(Exception):
    """reject draft in plan creation"""
    pass
```

## Соображения для Python-порта

### Concurrency model

Python 3.14+, sync + threading (не asyncio). Для отмены и timeout:
- `threading.Event` как замена break channel (Go `chan struct{}`)
- `threading.Timer` для session timeout и idle timeout
- Cancellation через kill процесса (subprocess.Popen.terminate/kill)

### Executor interface

Simple interface: `run(prompt) -> Result`. В Python:
- Protocol class
- `def run(self, prompt: str) -> Result` (синхронный)
- Cancellation через process.terminate() / process.kill()

### Signal detection

Строковый поиск подстрок в output. В Python: тривиальная реализация через `str.find()` / `re.search()`.

### Timer management

`threading.Timer` с cancel/restart для idle timeout. На каждой строке вывода: timer.cancel() + создание нового timer.

### Template system

Simple string replacement `str.replace()`. Agent expansion regex: `re.sub()` с callback function.

### Plan parsing dependency

Runner вызывает `parse_plan_file()` для has_uncompleted_tasks() и next_plan_task_position(). Эти вызовы происходят синхронно в loop, файл перечитывается каждую итерацию.
