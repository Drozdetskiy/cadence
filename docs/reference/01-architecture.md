# Архитектура cadence

Справочный документ по архитектуре cadence -- упрощенного Python-порта ralphex.

## Высокоуровневая схема системы

```
CLI (typer)
  |
  |-- parse flags (typer)
  |-- load config (tomllib)
  |-- validate environment (claude in PATH, git repo)
  |-- determine execution mode
  |-- select plan (numbered list)
  |-- create branch (git)
  |
  v
Processor / Orchestration
  |
  |-- Runner.run() -- dispatches to mode method
  |     |
  |     |-- run_full()         -- tasks -> review -> review loop -> finalize
  |     |-- run_review_only()  -- review -> review loop -> finalize
  |     |-- run_tasks_only()   -- tasks only
  |     |-- run_plan_creation() -- interactive Q&A loop
  |     |
  |     |-- run_task_phase()          -- loop: execute task, check signals
  |     |-- run_claude_review()       -- single review pass
  |     |-- run_claude_review_loop()  -- iterate review passes
  |     |-- run_finalize()            -- optional post-completion step
  |     |-- run_with_limit_retry()    -- wrapper: retry on rate limit
  |
  |-- uses Executor interface for CLI execution
  |-- uses Logger interface for progress output
  |-- uses InputCollector for user interaction (plan creation)
  |-- uses GitChecker for HEAD hash / diff fingerprint
  |
  v
Executors                              Git
  |                                       |
  |-- ClaudeExecutor  (claude CLI)        |-- Service
  |                                       |-- branch, commit, diff ops
  |                                       |-- default branch detection
  v                                       |-- VCS command abstraction
Progress                                  |
  |                                     Plan
  |-- Logger                              |
  |-- file format: header + timestamps    |-- parse_plan(), parse_plan_file()
  |                                       |-- plan selection (numbered list)
  |                                       |-- branch name extraction
  |
Input
  |
  |-- TerminalCollector
  |-- ask_question (numbered picker)
  |-- ask_draft_review (accept/revise/reject)
  |-- read_line_with_context
```

## Граф зависимостей модулей

```
cadence (CLI entry point)
  +-- cadence.config     (configuration loading)
  +-- cadence.processor  (orchestration)
  +-- cadence.git        (branch management)
  +-- cadence.plan       (plan file selection/parsing)
  +-- cadence.input      (terminal user input)
  +-- cadence.progress   (progress logging)
  +-- cadence.status     (shared types: signals, phases, sections)

cadence.processor
  +-- cadence.executor   (CLI execution)
  +-- cadence.config     (config types for prompts/agents)
  +-- cadence.plan       (plan parsing for task phase)
  +-- cadence.status     (phases, signals, sections)

cadence.executor
  +-- cadence.status     (signal detection)

cadence.progress
  +-- cadence.status     (sections, phases for color mapping)
  +-- cadence.config     (ColorConfig for colors)

cadence.config
  (leaf module, no internal dependencies)

cadence.git
  +-- cadence.plan       (extract_branch_name for branch creation)

cadence.plan
  +-- cadence.input      (read_line_with_context for prompt_description)
  +-- cadence.progress   (Colors for formatted output)

cadence.input
  (no internal dependencies, uses stdin)

cadence.status
  (leaf module, no dependencies)
```

## Режимы исполнения (execution modes)

Режим определяется на основе CLI-флагов.

| Mode constant   | CLI flag          | Описание                                         |
|-----------------|-------------------|--------------------------------------------------|
| ModeFull        | --task <file>     | tasks -> review -> review loop -> finalize       |
| ModeReview      | --review          | review -> review loop -> finalize                |
| ModePlan        | --plan <file>     | interactive plan creation via Q&A                |

Флаги проверяются на конфликты: нельзя комбинировать --review, --task, --plan.

## Поток исполнения по режимам

### ModeFull (полный pipeline)

```
1. PhaseTask: run_task_phase()
   |  loop 1..max_iterations:
   |    print section "task iteration N"
   |    run claude with task prompt (via run_with_limit_retry)
   |    check signals:
   |      COMPLETED -> return (move to reviews)
   |      FAILED    -> retry up to task_retry_count, then error
   |    no signal -> continue loop
   |
2. PhaseReview: run_claude_review() -- "claude review 0: all findings"
   |  single pass with ReviewFirstPrompt (4 agents)
   |  check REVIEW_DONE signal
   |
3. PhaseReview: run_claude_review_loop() -- review loop
   |  loop 1..max_review_iterations (max(3, max_iterations/10)):
   |    print section "claude review N: critical/major"
   |    run claude with ReviewSecondPrompt (2 agents)
   |    check REVIEW_DONE -> return
   |    check HEAD hash unchanged (no commits made) -> return
   |
4. PhaseFinalize: run_finalize()
   |  (only if finalize_enabled)
   |  single pass, best-effort (errors logged, not propagated)
```

### ModeReview (только review pipeline)

```
1. PhaseReview: run_claude_review() -- "claude review 0: all findings"
2. PhaseReview: run_claude_review_loop() -- review loop
3. PhaseFinalize: run_finalize()
```

### ModePlan (создание плана)

```
1. PhasePlan: run_plan_creation()
   |  loop 1..max_plan_iterations (max(5, max_iterations/5)):
   |    run claude with make_plan prompt + Q&A history
   |    check signals:
   |      PLAN_DRAFT -> present to user (accept/revise/interactive/reject)
   |      QUESTION   -> parse JSON payload, ask user via numbered picker
   |      PLAN_READY -> return success
   |
   |  after success: find new plan file, ask user to continue with ModeFull
```

## Жизненный цикл фаз (Phase lifecycle)

Фазы определены в `cadence/status.py` как тип `Phase` (string).

| Phase constant  | String value   | Цвет    | Описание                                 |
|-----------------|----------------|---------|------------------------------------------|
| PhaseTask       | "task"         | green   | исполнение задач из плана                |
| PhaseReview     | "review"       | cyan    | code review (Claude)                     |
| PhasePlan       | "plan"         | green   | создание плана (reuses task color)       |
| PhaseFinalize   | "finalize"     | green   | финализация (rebase, squash, tests)      |

`PhaseHolder` -- thread-safe обертка для текущей фазы:
- `set(phase)` -- атомарное обновление + вызов callback
- `get()` -- чтение текущей фазы
- `on_change(callback)` -- регистрация listener на изменение фазы

PhaseHolder используется:
- Runner меняет фазу при переключении этапов
- Progress logger выбирает цвет на основе текущей фазы

## Модель сигнальной коммуникации

Сигналы -- строки формата `<<<CADENCE:...>>>`, которые Claude вставляет в свой вывод.
Определены в `cadence/status.py`, детектируются в `cadence/executor.py` функцией `detect_signal()`.

| Сигнал     | Строка                           | Фаза         | Значение                              |
|------------|----------------------------------|--------------|---------------------------------------|
| Completed  | `<<<CADENCE:ALL_TASKS_DONE>>>`  | Task         | все задачи выполнены                  |
| Failed     | `<<<CADENCE:TASK_FAILED>>>`     | Task         | текущая задача провалена              |
| ReviewDone | `<<<CADENCE:REVIEW_DONE>>>`     | Review       | review завершен, нет findings         |
| Question   | `<<<CADENCE:QUESTION>>>`        | Plan         | вопрос пользователю (JSON payload)    |
| PlanReady  | `<<<CADENCE:PLAN_READY>>>`      | Plan         | план создан и записан в файл          |
| PlanDraft  | `<<<CADENCE:PLAN_DRAFT>>>`      | Plan         | черновик плана для review              |

### Детекция сигналов

`detect_signal(text)` в `cadence/executor.py`:
- ищет подстроку в тексте вывода Claude
- возвращает первый найденный сигнал или пустую строку
- для ClaudeExecutor: проверяется во время streaming (внутри scanner loop), сигнал сохраняется в Result.signal

### Формат payload для QUESTION

```json
{"question": "Текст вопроса?", "options": ["Вариант 1", "Вариант 2", "Вариант 3"]}
```

Парсится `parse_question_payload()` в `cadence/processor/signals.py`.
Пользователю предлагается выбрать через numbered picker, с опцией "Other" для ввода произвольного ответа.

### Формат payload для PLAN_DRAFT

Содержимое плана заключено между маркерами `<<<CADENCE:PLAN_DRAFT>>>` и `<<<CADENCE:END>>>`.
Парсится `parse_plan_draft_payload()` в `cadence/processor/signals.py`.

## Модель управления процессами

### Обработка сигналов ОС

Настройка в CLI entry point:

1. `signal.signal(SIGINT, handler)` / `signal.signal(SIGTERM, handler)` -- основной handler, инициирует shutdown по Ctrl+C
2. Interrupt watcher thread:
   - при срабатывании логирует "interrupting..."
   - ждет 5 секунд для graceful shutdown
   - если не завершилось -- вызывает cleanup (restore terminal) и `sys.exit(1)`

### Управление дочерними процессами (process groups)

Реализовано в `cadence/executor/procgroup_unix.py` и `procgroup_windows.py`.

Unix:
- `setup_process_group(proc)` -- устанавливает `start_new_session=True` для создания новой сессии и группы процессов
- `kill_process_group(proc)` -- SIGTERM -> 100ms delay -> SIGKILL по всей группе (-pid)
- ESRCH early-return: если группа уже завершена, пропускает delay

Windows:
- `setup_process_group()` -- no-op
- `kill_process(proc)` -- убивает только прямой процесс (не дочерние)

### Таймауты сессий

Session timeout (`--session-timeout`):
- `run_with_session_timeout()` оборачивает вызов executor с таймаутом через `threading.Timer`
- при превышении: сессия убивается, цикл продолжает со следующей итерацией
- применяется только к Claude executor
- `last_session_timed_out` флаг предотвращает ложный выход из review loop по "no commits"

Idle timeout (`--idle-timeout`):
- в `ClaudeExecutor.run()`: `threading.Timer` с closure-based timer reset
- каждая строка вывода сбрасывает таймер
- при срабатывании: отменяет idle context -> убивает процесс
- `Result.idle_timed_out` флаг сообщает вызывающему

### Rate limit retry

`run_with_limit_retry()` в `cadence/processor/runner.py`:
- оборачивает вызов executor
- при `LimitPatternError`: ждет `wait_on_limit` duration, затем retry
- при `PatternMatchError` (обычная ошибка): возвращает ошибку
- limit patterns проверяются до error patterns (приоритет)
- без `--wait`: limit match проваливается в error pattern behavior

## Расчет максимальных итераций

Константы в `cadence/processor/runner.py`:

```
Task iterations:   1..max_iterations (default 50, configurable)
Review iterations: max(3, max_iterations / 10) = max(3, 5) = 5 при default
Plan iterations:   max(5, max_iterations / 5) = max(5, 10) = 10 при default
```

Минимумы: review = 3, plan = 5.

Divisors: review = 10, plan = 5.

## Типы секций (Section types)

Определены в `cadence/status.py`, используются для progress logging.

| SectionType             | Формат строки                    |
|-------------------------|----------------------------------|
| SectionGeneric          | custom label                     |
| SectionTaskIteration    | "task iteration N"               |
| SectionClaudeReview     | "claude review N: suffix"        |
| SectionPlanIteration    | "plan iteration N"               |

Helper-функции: `new_task_iteration_section(n)`, `new_claude_review_section(n, suffix)`, `new_plan_iteration_section(n)`, `new_generic_section(label)`.

## Ключевые интерфейсы Runner

Определены в `cadence/processor/runner.py` (Python protocols):

```python
# Executor -- запуск CLI и получение результата
class Executor(Protocol):
    def run(self, prompt: str, *, timeout: float | None = None) -> Result: ...

# Logger -- логирование прогресса
class Logger(Protocol):
    def print(self, format: str, *args: Any) -> None: ...
    def print_raw(self, format: str, *args: Any) -> None: ...
    def print_section(self, section: Section) -> None: ...
    def print_aligned(self, text: str) -> None: ...
    def log_question(self, question: str, options: list[str]) -> None: ...
    def log_answer(self, answer: str) -> None: ...
    def log_draft_review(self, action: str, feedback: str) -> None: ...
    def path(self) -> str: ...

# InputCollector -- пользовательский ввод (plan creation)
class InputCollector(Protocol):
    def ask_question(self, question: str, options: list[str]) -> str: ...
    def ask_draft_review(self, question: str, plan_content: str) -> tuple[str, str]: ...

# GitChecker -- проверка состояния git (review loop optimization)
class GitChecker(Protocol):
    def head_hash(self) -> str: ...
    def diff_fingerprint(self) -> str: ...
```

## Runner struct

```python
@dataclass
class Runner:
    cfg: Config
    log: Logger
    claude: Executor                # task phase executor
    review_claude: Executor         # review phase executor (may differ in model)
    git: GitChecker
    input_collector: InputCollector
    phase_holder: PhaseHolder
    iteration_delay: float
    task_retry_count: int
    wait_on_limit: float            # rate limit retry wait (seconds)
    last_session_timed_out: bool    # prevents false "no commits" exit
```

## Executors struct

```python
@dataclass
class Executors:
    claude: Executor                # required: task phase
    review_claude: Executor         # optional: separate model for reviews
```
