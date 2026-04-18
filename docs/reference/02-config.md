# Система конфигурации

Справочный документ по системе конфигурации rlx (Python-порт ralphex).

## Каскад загрузки

Конфигурация загружается из двух уровней с приоритетом (от высшего к низшему):

```
CLI flags  >  local config (.rlx/config.toml)  >  defaults (in code)
```

Глобальный уровень (`~/.config/`) отсутствует. Конфигурация привязана к конкретному проекту.

Основные функции:
- `config.load(config_dir)` -- обнаруживает `.rlx/`, загружает config.toml, мержит с defaults
- `config.detect_local_dir()` -- ищет `.rlx/` в cwd

### Порядок загрузки

1. Загрузка defaults из кода (dict/dataclass)
2. Если `.rlx/config.toml` существует -- парсинг через `tomllib` (stdlib, Python 3.11+)
3. Merge: значения из TOML перезаписывают defaults (absent key = не установлено, используется default)
4. Загрузка prompts: per-file fallback local -> embedded через `importlib.resources`
5. Загрузка agents: union всех .txt файлов, per-file fallback local -> embedded
6. Сборка итогового `Config` объекта
7. Применение CLI overrides

### Стратегия merge

**Config (values):** per-field merge. TOML-значение перезаписывает default. Отсутствующий ключ в TOML -- default сохраняется. Нет необходимости в `*Set` tracking: в TOML absent key однозначно означает "не задано", а явный `false`/`0` -- задано.

**Prompts:** per-file fallback. Для каждого prompt файла: local `.rlx/prompts/` -> embedded (через `importlib.resources`). Если файл содержит только комментарии/пробелы -- fallback на embedded default.

**Agents:** per-file fallback + union файлов. Собирается объединение .txt файлов из embedded и local. Для каждого уникального файла: local -> embedded.

**Colors:** per-field merge. TOML-значение перезаписывает default hex.

## Формат TOML

- Парсер: `tomllib` (стандартная библиотека Python 3.11+)
- Файл: `.rlx/config.toml`
- Inline comments поддерживаются (`# ...`)
- Списки: нативные TOML массивы `["a", "b", "c"]`
- Duration: строка с суффиксом, парсится вручную -- `"30m"`, `"1h"`, `"90s"`, `"1h30m"`
- Boolean: `true`/`false` (стандарт TOML)
- Tilde expansion: применяется к `vcs_command`

### Пример `.rlx/config.toml`

```toml
# Claude executor
claude_command = "claude"
claude_args = "--dangerously-skip-permissions --output-format stream-json --verbose"
claude_model = "sonnet"
review_model = "opus"

# Timing
iteration_delay_ms = 2000
task_retry_count = 1
max_iterations = 50
session_timeout = "0"
idle_timeout = "0"
wait_on_limit = "0"

# Feature flags
finalize_enabled = false

# Paths and VCS
plans_dir = "docs/plans"
default_branch = ""
vcs_command = "git"
commit_trailer = ""

# Error patterns
claude_error_patterns = [
    "You've hit your limit",
    "API Error:",
    "cannot be launched inside another Claude Code session",
    "Not logged in",
]
claude_limit_patterns = [
    "You've hit your limit",
]

# Colors (hex format)
[colors]
task = "#2e8b57"
review = "#1a9e9e"
warn = "#d4930d"
error = "#cc0000"
signal = "#d25252"
timestamp = "#707070"
info = "#808080"
```

## Все поля конфигурации

### Claude executor

| TOML key | Type | Default | Описание |
|----------|------|---------|----------|
| `claude_command` | string | `"claude"` | Команда для запуска Claude Code |
| `claude_args` | string | `"--dangerously-skip-permissions --output-format stream-json --verbose"` | Аргументы для claude |
| `claude_model` | string | `""` (default модель Claude Code) | Модель для task execution (opus/sonnet/haiku или полный ID) |
| `review_model` | string | `""` (fallback на claude_model) | Модель для review фаз |

### Timing и iteration control

| TOML key | Type | Default | Validation | Описание |
|----------|------|---------|------------|----------|
| `iteration_delay_ms` | int | `2000` | >= 0 | Задержка между итерациями (мс) |
| `task_retry_count` | int | `1` | >= 0 | Кол-во повторов при FAILED (0=нет, 1=одна попытка) |
| `max_iterations` | int | `50` | >= 1 | Макс. итераций задач на plan |

### Timeouts и rate limit

| TOML key | Type | Default | Описание |
|----------|------|---------|----------|
| `session_timeout` | duration string | `"0"` (disabled) | Макс. длительность одной claude сессии |
| `idle_timeout` | duration string | `"0"` (disabled) | Kill сессии при отсутствии output за указанное время |
| `wait_on_limit` | duration string | `"0"` (disabled) | Время ожидания перед retry при rate limit |

### Feature flags

| TOML key | Type | Default | Описание |
|----------|------|---------|----------|
| `finalize_enabled` | bool | `false` | Включить finalize step после review |

### Paths и VCS

| TOML key | Type | Default | Описание |
|----------|------|---------|----------|
| `plans_dir` | string | `"docs/plans"` | Директория с plan файлами |
| `default_branch` | string | `""` (auto-detect) | Override default branch |
| `vcs_command` | string | `"git"` | VCS команда (tilde-expanded) |
| `commit_trailer` | string | `""` (disabled) | Trailer для всех коммитов (e.g., Co-authored-by) |

### Error pattern detection

| TOML key | Type | Default | Описание |
|----------|------|---------|----------|
| `claude_error_patterns` | list[string] | `["You've hit your limit", "API Error:", "cannot be launched inside another Claude Code session", "Not logged in"]` | Паттерны ошибок claude (case-insensitive substring) |
| `claude_limit_patterns` | list[string] | `["You've hit your limit"]` | Rate limit паттерны claude (для wait+retry) |

Приоритет проверки: limit patterns проверяются первыми. Если match + `wait_on_limit > 0` -> wait и retry. Если match + `wait_on_limit == 0` -> fallthrough к error pattern behavior (exit). Limit patterns намеренно пересекаются с error patterns; `wait_on_limit` работает как toggle.

### Output colors

| TOML key | Default hex | Default RGB | Описание |
|----------|-------------|-------------|----------|
| `colors.task` | `#2e8b57` | `46,139,87` | Task execution phase (green) |
| `colors.review` | `#1a9e9e` | `26,158,158` | Review phase (teal) |
| `colors.warn` | `#d4930d` | `212,147,13` | Warning messages (amber) |
| `colors.error` | `#cc0000` | `204,0,0` | Error messages (red) |
| `colors.signal` | `#d25252` | `210,82,82` | Completion/failure signals (salmon red) |
| `colors.timestamp` | `#707070` | `112,112,112` | Timestamp prefix (gray) |
| `colors.info` | `#808080` | `128,128,128` | Informational messages (gray) |

Формат в TOML: `#RRGGBB` hex string в секции `[colors]`.

## Система шаблонных переменных

### Базовые переменные (все промпты)

| Variable | Fallback | Source |
|----------|----------|--------|
| `{{PLAN_FILE}}` | `"(no plan file - reviewing current branch)"` | Проверяет original path, затем completed/ |
| `{{PROGRESS_FILE}}` | `"(no progress file available)"` | Путь к файлу прогресса |
| `{{GOAL}}` | -- | `"implementation of plan at <path>"` или `"current branch vs <branch>"` |
| `{{DEFAULT_BRANCH}}` | `"master"` | config `default_branch` или auto-detected |
| `{{PLANS_DIR}}` | `"docs/plans"` | config `plans_dir` |

### Iteration-aware переменные (review промпты)

| Variable | First iteration | Subsequent iterations |
|----------|-----------------|----------------------|
| `{{DIFF_INSTRUCTION}}` | `"git diff <DEFAULT_BRANCH>...HEAD"` | `"git diff"` |
| `{{PREVIOUS_REVIEW_CONTEXT}}` | `""` (empty) | Formatted block с предыдущим ответом Claude |

### Специальные переменные (конкретные промпты)

| Variable | Used in | Description |
|----------|---------|-------------|
| `{{PLAN_DESCRIPTION}}` | make_plan.txt | Содержимое файла, переданного в --plan |

### Agent references

| Pattern | Description |
|---------|-------------|
| `{{agent:name}}` | Expands в Task tool instruction с промптом агента |

Regex: `\{\{agent:([a-zA-Z0-9_-]+)\}\}`

Expansion format:
```
Use the Task tool[ with model=X] to launch a <subagent-type> agent with this prompt:
"<agent prompt with base variables expanded>"

Report findings only - no positive observations.
```

- Agent lookup map строится из загруженных custom agents
- Missing agents: warning в лог, reference оставляется unexpanded
- Agent content: base variables расширяются, но рекурсивного расширения agent references нет
- Frontmatter `model` и `agent` type учитываются при expansion

### Commit trailer instruction

Когда `commit_trailer` настроен, к каждому промпту добавляется инструкция:
```
When making git commits, add the following trailer after a blank line at the end of the commit message:
<trailer value>
```

Применяется один раз к финальному собранному промпту.

### Функции expansion

| Function | Variables | Used for |
|----------|-----------|----------|
| `replace_base_variables()` | PLAN_FILE, PROGRESS_FILE, GOAL, DEFAULT_BRANCH, PLANS_DIR | Базовый набор для всех промптов |
| `replace_prompt_variables()` | base + `{{agent:name}}` + commit trailer | Task, review промпты |
| `replace_variables_with_iteration()` | base + DIFF_INSTRUCTION + `{{agent:name}}` + PREVIOUS_REVIEW_CONTEXT + commit trailer | Review промпты с iteration context |
| `build_plan_prompt()` | base + PLAN_DESCRIPTION + commit trailer | Plan creation prompt |

## Обработка комментариев и fallback

### Comment functions

| Function | Behaviour | Used for |
|----------|-----------|----------|
| `strip_comments()` | Удаляет все строки начинающиеся с `#` | Проверка на emptiness (полностью закомментированный файл -> fallback) |
| `strip_leading_comments()` | Удаляет блок из 2+ подряд идущих `#`-строк в начале. Одиночный `# Title` сохраняется | Загрузка промптов (meta-comment block stripped, markdown header preserved) |
| `strip_leading_comment_lines()` | Удаляет все подряд идущие `#`-строки в начале (включая одиночную) | Agent frontmatter detection (comments before `---` stripped) |
| `normalize_crlf()` | CRLF -> LF | Все файлы перед обработкой |

### Fallback chain для prompt файлов

1. Читаем файл из local dir `.rlx/prompts/`
2. `normalize_crlf` -> `strip_comments` -> проверка на emptiness
3. Если empty (только комментарии/пробелы) -> fallback на embedded
4. Если не empty -> `strip_leading_comments` -> trim -> возвращаем
5. Embedded (через `importlib.resources`): `strip_leading_comments` -> trim -> возвращаем

### Fallback chain для agent файлов

1. Собираем union всех .txt filenames из embedded + local
2. Для каждого filename: local `.rlx/agents/` -> embedded
3. При загрузке из файла: `strip_comments` проверяет emptiness, `parse_options` проверяет наличие body
4. Если нет body -> fallback на embedded default
5. Если frontmatter options но нет body -> warning + fallback (frontmatter dropped)
6. `build_agent()`: пробует `parse_options` на raw content, при неудаче -- `strip_leading_comment_lines` + retry

## Frontmatter для агентов

### Формат

```yaml
---
model: sonnet
agent: custom-reviewer
---
Agent prompt text here...
```

### Options

```python
@dataclass
class AgentOptions:
    model: str = ""      # keyword form: haiku, sonnet, opus
    agent_type: str = ""  # subagent type для Task tool
```

### Parsing logic (`parse_options`)

1. Проверяет prefix `---\n`
2. Ищет closing `\n---` (должен быть на отдельной строке)
3. YAML parsing через `PyYAML` или стандартный парсер
4. `normalize_model()`: извлекает keyword из полного ID (e.g., `"claude-sonnet-4-5-20250929"` -> `"sonnet"`)
5. Если YAML malformed -> treat as no frontmatter, return original content
6. Return parsed Options + body (trimmed)

### Validation

- Valid models: `haiku`, `sonnet`, `opus` (после normalization)
- Invalid model: warning в лог, Options сбрасываются в defaults
- Пустой model: допускается (используется default модель)

### Agent build flow

```
build_agent(name, prompt):
  1. parse_options(prompt) -> opts, body
  2. если нет frontmatter -> strip_leading_comment_lines(prompt) + retry parse_options
  3. если body пустой -> используем raw prompt с default Options
  4. validate() -> warnings -> при warnings Options = default
  5. Return CustomAgent(name=name, prompt=body, options=opts)
```

## Embedded defaults

### Package resources (importlib.resources)

Структура пакета:
```
rlx/
  defaults/
    prompts/
      task.txt
      review_first.txt
      review_second.txt
      make_plan.txt
      finalize.txt
    agents/
      implementation.txt
      quality.txt
      documentation.txt
      simplification.txt
      testing.txt
```

Defaults для config values хранятся в коде (dataclass/dict), не в файле.

## Конфигурационная директория

```
.rlx/
```

Ищется в текущей рабочей директории (cwd). Глобальная директория отсутствует.

## Переменные окружения

| Env var | Описание |
|---------|----------|
| `RLX_CONFIG_DIR` | Override пути к конфигурационной директории |

## Сигналы (output markers)

Формат сигналов: `<<<RLX:...>>>` (вместо `<<<RALPHEX:...>>>` в оригинале).
