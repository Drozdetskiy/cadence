# Агенты rlx

Справочный документ по всем агентам ревью для Python-порта rlx.

## Обзор системы агентов

Агенты -- это специализированные промпты для ревью, запускаемые как субагенты Claude Code через Task tool. Хранятся в пакете rlx как системные defaults.

### Расположение и приоритет

Два уровня загрузки:

- Системный: встроен в пакет rlx (defaults)
- Локальный: `.rlx/agents/<name>.txt`

Приоритет: если локальный файл присутствует -- используется он. Если отсутствует -- системный default.

### Отключение агента

Удаление локального файла агента НЕ отключает его (используется системный default). Чтобы отключить: убрать ссылку `{{agent:name}}` из промпт-файлов (review_first.txt, review_second.txt).

### Frontmatter

Агенты поддерживают опциональный YAML frontmatter:

```yaml
---
model: sonnet        # haiku | sonnet | opus
agent: code-reviewer # тип субагента Claude Code (по умолчанию: general-purpose)
---
```

- `model` -- модель Claude для агента. Длинные ID нормализуются (`claude-sonnet-4-5-20250929` -> `sonnet`)
- `agent` -- тип субагента Task tool (по умолчанию `general-purpose`)
- Невалидные значения model отбрасываются с предупреждением, используются defaults

### Как агенты встраиваются в промпты

При раскрытии `{{agent:name}}`:

1. Содержимое агента загружается (frontmatter отделяется от тела)
2. В теле агента раскрываются base-переменные (`{{PLAN_FILE}}`, `{{DEFAULT_BRANCH}}` и т.д.)
3. Результат форматируется:

```
Use the Task tool [with model=X] to launch a <subagent-type> agent with this prompt:
"<текст агента с раскрытыми переменными>"

Report findings only - no positive observations.
```

Рекурсия агентов не допускается -- `{{agent:X}}` внутри агента не раскрывается.

### Где используются агенты

| Агент | review_first.txt | review_second.txt |
|---|---|---|
| quality | да | да |
| implementation | да | да |
| testing | да | нет |
| simplification | да | нет |

Первое ревью: 4 агента параллельно (полный анализ).
Второе ревью: 2 агента параллельно (только critical/major issues).

---

## quality.txt -- Агент качества кода и безопасности

**Используется в:** review_first.txt, review_second.txt
**Frontmatter по умолчанию:** нет (model=default, agent=general-purpose)

### Области анализа

- Correctness Review: логические ошибки, edge cases, обработка ошибок, управление ресурсами, concurrency, целостность данных
- Security Analysis: валидация ввода, авторизация, injection, секреты, раскрытие информации
- Simplicity Assessment: прямые решения, отсутствие enterprise-паттернов, обоснованность абстракций, scope creep, premature optimization

### Формат вывода

Для каждой проблемы: Location (file:line), Issue, Impact, Fix.

### Полный текст

```
Review code for bugs, security issues, and quality problems.

## Correctness Review

1. Logic errors - off-by-one errors, incorrect conditionals, wrong operators
2. Edge cases - empty inputs, nil/null values, boundary conditions, concurrent access
3. Error handling - all errors checked, appropriate error wrapping, no silent failures
4. Resource management - proper cleanup, no leaks, correct resource release
5. Concurrency issues - race conditions, deadlocks, thread/coroutine leaks
6. Data integrity - validation, sanitization, consistent state management

## Security Analysis

1. Input validation - all user inputs validated and sanitized
2. Authentication/authorization - proper checks in place
3. Injection vulnerabilities - SQL, command, path traversal
4. Secret exposure - no hardcoded credentials or keys
5. Information disclosure - error messages, logs, debug info

## Simplicity Assessment

1. Direct solutions first - if simple approach works, don't use complex pattern
2. No enterprise patterns for simple problems - avoid factories, builders for straightforward code
3. Question every abstraction - each interface/abstraction must solve real problem
4. No scope creep - changes solve only the stated problem
5. No premature optimization - unless addressing proven bottlenecks

## What to Report

For each issue:
- Location: exact file path and line number
- Issue: clear description
- Impact: how this affects the code
- Fix: specific suggestion

Focus on defects that would cause runtime failures, security vulnerabilities, or maintainability problems.
Report problems only - no positive observations.
```

---

## implementation.txt -- Агент корректности реализации

**Используется в:** review_first.txt, review_second.txt
**Frontmatter по умолчанию:** нет (model=default, agent=general-purpose)

### Области анализа

- Requirement coverage: все ли аспекты требования реализованы
- Correctness of approach: правильный ли подход к решению проблемы
- Wiring and integration: всё ли подключено (роуты, хендлеры, конфиги)
- Completeness: нет ли пропущенных частей (импорты, интерфейсы, миграции)
- Logic flow: корректен ли поток данных от входа до выхода
- Edge cases: обработка граничных условий

### Формат вывода

Для каждой проблемы: Issue, Impact, Location (file:line), Fix.

### Полный текст

```
Review whether the implementation achieves the stated goal/requirement.

## Core Review Responsibilities

1. Requirement coverage - does implementation address all aspects of the stated requirement? Are there edge cases or scenarios not handled?

2. Correctness of approach - is the chosen approach actually solving the right problem? Could it fail to achieve the goal in certain conditions?

3. Wiring and integration - is everything connected properly? Are new components registered, routes added, handlers wired, configs updated?

4. Completeness - are there missing pieces that would prevent the feature from working? Missing imports, unimplemented interfaces, incomplete migrations?

5. Logic flow - does data flow correctly from input to output? Are transformations correct? Is state managed properly?

6. Edge cases - are boundary conditions handled? Empty inputs, null values, concurrent access, error paths?

## What to Report

For each issue found:
- Issue: clear description of what's wrong
- Impact: how this prevents achieving the goal
- Location: file and line reference
- Fix: what needs to be added or changed

Focus on correctness of approach, not code style.
Report problems only - no positive observations.
```

---

## testing.txt -- Агент тестирования

**Используется в:** review_first.txt (не используется в review_second.txt)
**Frontmatter по умолчанию:** нет (model=default, agent=general-purpose)

### Области анализа

- Test Existence and Coverage: пропущенные тесты, непокрытые error paths, coverage gaps
- Test Quality: тесты проверяют поведение, не детали реализации; независимость; дескриптивные имена
- Fake Test Detection: тесты, которые всегда проходят; hardcoded values вместо реального вывода; проверка mock вместо кода
- Test Independence: нет shared mutable state, правильный setup/teardown
- Edge Case Coverage: empty inputs, null/nil, boundary values, concurrency, timeout

### Формат вывода

Для каждой проблемы: Location (test file:function), Issue, Impact, Fix.

### Полный текст

```
Review test coverage and quality.

## Test Existence and Coverage

1. Missing tests - new code paths without corresponding tests
2. Untested error paths - error conditions not verified
3. Coverage gaps - functions or branches without test coverage
4. Integration test needs - system boundaries requiring integration tests

## Test Quality

1. Tests verify behavior, not implementation details
2. Each test is independent, can run in any order
3. Descriptive test names that explain what is being tested
4. Both success and error paths tested
5. Edge cases and boundary conditions covered

## Fake Test Detection

Watch for tests that don't actually verify code:
- Tests that always pass regardless of code changes
- Tests checking hardcoded values instead of actual output
- Tests verifying mock behavior instead of code using the mock
- Ignored errors with _ or empty error checks
- Conditional assertions that always pass
- Commented out failing test cases

## Test Independence

1. No shared mutable state between tests
2. Proper setup and teardown
3. No order dependencies between tests
4. Resources properly cleaned up

## Edge Case Coverage

1. Empty inputs and collections
2. Null/nil values
3. Boundary values (zero, max, min)
4. Concurrent access scenarios
5. Timeout and cancellation handling

## What to Report

For each finding:
- Location: test file and function
- Issue: what's wrong with the test
- Impact: what bugs could slip through
- Fix: how to improve the test

Report problems only - no positive observations.
```

---

## simplification.txt -- Агент обнаружения over-engineering

**Используется в:** review_first.txt (не используется в review_second.txt)
**Frontmatter по умолчанию:** нет (model=default, agent=general-purpose)

### Области анализа

- Excessive Abstraction Layers: пустые обёртки, factory для одной реализации, интерфейс на стороне producer
- Premature Generalization: generic решения для конкретных проблем, config objects для 2-3 параметров
- Unnecessary Indirection: pass-through wrappers, чрезмерные builder chains
- Future-Proofing Excess: неиспользуемые extension points, версионированные внутренние API
- Unnecessary Fallbacks: fallback, который не срабатывает; legacy mode; dual implementations
- Premature Optimization: кэширование редко используемых данных, кастомные структуры данных

### Формат вывода

Для каждой находки: Location (file:line), Pattern, Problem, Simplification, Effort (trivial/small/medium/large).

### Полный текст

```
Detect over-engineered and overcomplicated code - code that works but is more complex than necessary.

## Excessive Abstraction Layers

- Wrapper adds nothing - method just calls another method with same signature
- Factory for single implementation - factory pattern when only one concrete type exists
- Interface on producer side - interface defined where implemented, not where consumed
- Layer cake anti-pattern - handler -> service -> repository when each just passes through
- DTO/Mapper overkill - multiple types representing same data with conversion functions

## Premature Generalization

- Generic solution for specific problem - event bus for one event type
- Config objects for 2-3 options - options pattern when direct parameters suffice
- Plugin architecture for fixed functionality - extension points nothing extends
- Overloaded struct - one type handling all variations with many optional fields

## Unnecessary Indirection

- Pass-through wrappers - methods that only delegate to dependencies
- Excessive method chaining - builder pattern for simple constructions
- Interface wrapping primitives - custom types for standard library types
- Middleware stacking - multiple middlewares that could be one

## Future-Proofing Excess

- Unused extension points - hooks, callbacks, plugins with no callers
- Versioned internal APIs - v1/v2 when only one version used
- Feature flags for permanent decisions - flags always on/off

## Unnecessary Fallbacks

- Fallback that never triggers - default path conditions never met
- Legacy mode kept just in case - old code path always disabled
- Dual implementations - old + new logic when old has no callers
- Silent fallbacks hiding problems - catching errors and falling back instead of failing fast

## Premature Optimization

- Caching rarely-accessed data - cache for data read once at startup
- Custom data structures - complex structures when arrays/maps work
- Worker pools for occasional tasks - pooling for operations/hour
- Connection pooling overkill - complex pooling for single connection

## What to Report

For each finding:
- Location: file and line reference
- Pattern: which over-engineering pattern detected
- Problem: why this adds unnecessary complexity
- Simplification: what simpler code would look like
- Effort: trivial/small/medium/large

Report problems only - no positive observations.
```

---

## Сводная таблица агентов

| Агент | Фокус | review_first | review_second | Формат вывода |
|---|---|---|---|---|
| quality | Баги, безопасность, качество, простота | да | да | Location, Issue, Impact, Fix |
| implementation | Корректность реализации требований | да | да | Issue, Impact, Location, Fix |
| testing | Покрытие и качество тестов | да | нет | Location, Issue, Impact, Fix |
| simplification | Over-engineering и сложность | да | нет | Location, Pattern, Problem, Simplification, Effort |

## Кастомизация для Python-порта

Система агентов в Python-порте должна поддерживать:

1. Загрузка из файлов с 2-level fallback (local `.rlx/agents/` -> системный в пакете)
2. YAML frontmatter парсинг (model, agent type)
3. Шаблонная подстановка переменных внутри агентов
4. Форматирование в Task-инструкции с учётом frontmatter options
5. Защита от рекурсии (агенты не раскрывают ссылки на другие агенты)
