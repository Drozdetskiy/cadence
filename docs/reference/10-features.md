# Каталог фич и кроссплатформенные заметки

Справочный документ по всем пользовательским фичам rlx и платформенным различиям для планирования Python-порта.

## Обзор

Этот документ перечисляет каждую пользовательскую фичу rlx со стороны пользователя: что делает, как конфигурируется, какие CLI-флаги используются. Также покрывает кроссплатформенные различия между Linux/macOS и Windows.

---

## Режимы выполнения

### ModeFull (по умолчанию)

Полный автономный пайплайн: задачи -> первый ревью (5 агентов) -> второй ревью (2 агента) -> finalize.

- CLI: `rlx docs/plans/feature.md` (без специальных флагов)
- Требуется: план-файл (или выбор через нумерованный список)
- Создаёт ветку из имени плана, выполняет все фазы последовательно

### ModeReview (`--review` / `-r`)

Пропускает фазу задач, запускает только ревью-пайплайн на существующих изменениях.

- CLI: `rlx --review` или `rlx --review docs/plans/feature.md`
- План-файл опционален (даёт ревьюерам контекст)
- Поток: первый ревью -> второй ревью -> finalize
- Подходит для ревью кода, написанного вручную или другими инструментами

### ModeTasksOnly (`--task` / `-t`)

Только выполнение задач из плана, без ревью.

- CLI: `rlx --task docs/plans/feature.md`
- Требуется: план-файл
- Подходит для быстрых итераций и тестирования

### ModePlan (`--plan <file>`)

Интерактивное создание плана через диалог с Claude.

- CLI: `rlx --plan prompt.md`
- `--plan` принимает путь к файлу с описанием; содержимое читается как prompt для создания плана
- Результат: `<file>-plan.md` рядом с исходным файлом (e.g., `prompt.md` -> `prompt-plan.md`)
- Поток: Claude исследует кодовую базу -> задаёт уточняющие вопросы -> генерирует черновик -> пользователь ревьюит -> принятие/доработка/отклонение
- После принятия: предлагает продолжить выполнение плана

Signals: QUESTION (JSON payload с options), PLAN_DRAFT (plan content between markers), PLAN_READY.

### Разрешение конфликтов флагов

Флаги режимов разрешаются по приоритету: `--plan` > `--task` > `--review` > full. При указании нескольких флагов побеждает флаг с наибольшим приоритетом.

---

## Manual Break (Ctrl+\)

Прерывание выполнения через SIGQUIT с разным поведением в зависимости от текущей фазы.

- Сигнал: SIGQUIT (Ctrl+\) на Unix; недоступен на Windows

### Поведение в фазе задач (Pause/Resume)

1. Ctrl+\ отменяет текущую Claude-сессию
2. Показывается: "session interrupted. press Enter to continue, Ctrl+C to abort"
3. Пауза -- пользователь может отредактировать план-файл
4. Enter: та же задача перезапускается с новой сессией (перечитывает план)
5. Ctrl+C: abort
6. Счётчик итераций декрементируется для сохранения бюджета

### Поведение в фазе ревью (Immediate Break)

1. Ctrl+\ отменяет текущую сессию ревью
2. Цикл завершается немедленно: "manual break requested, review terminated early"
3. Без паузы -- жёсткая остановка

---

## Session Timeout

Фиксированный wall-clock лимит на Claude-сессию. Убивает зависшие сессии.

- CLI: `--session-timeout 30m`
- Config: `session_timeout = "30m"`
- Формат: duration string ("30m", "1h", "1h30m")
- Только Claude-сессии
- Default: disabled (пустая строка или 0)

Поведение при срабатывании:
- Error и Signal очищаются
- Устанавливается флаг `last_session_timed_out`
- Review loops трактуют timeout как "ничего не найдено" и продолжают
- Не засчитывается как completing iteration -- loop retry автоматически

---

## Idle Timeout

Таймаут по отсутствию вывода. Убивает Claude-сессию, которая замолчала.

- CLI: `--idle-timeout 5m`
- Config: `idle_timeout = "5m"`
- Формат: duration string
- Только Claude
- Default: disabled

Ключевое отличие от session timeout: таймер сбрасывается на каждой строке вывода. Срабатывает только когда сессия полностью замолкает.

Приоритет при timeout: limit patterns проверяются ПЕРВЫМИ (выше приоритет), затем error patterns.

---

## Rate Limit Detection и Wait-Retry

Обнаружение rate limit в выводе executor и автоматический retry после ожидания.

- CLI: `--wait 1h`
- Config: `wait_on_limit = "1h"`
- Config patterns:
  - `claude_limit_patterns = "You've hit your limit"` (default)
- Pattern matching: case-insensitive substring

Логика:
1. Limit patterns проверяются ПЕРВЫМИ (приоритет выше error patterns)
2. Если match И `wait_on_limit > 0`: ждать указанное время, retry (бесконечный цикл)
3. Если match И `wait_on_limit == 0`: fallback к error pattern поведению (exit)
4. Retry loop прерывается только: успех, не-limit ошибка, прерывание

---

## Error Pattern Detection

Конфигурируемые паттерны для обнаружения ошибок в выводе claude.

- Config:
  - `claude_error_patterns = "You've hit your limit,API Error:,cannot be launched inside another Claude Code session,Not logged in"`
- Matching: case-insensitive substring
- Claude: проверяются последние 10 текстовых блоков (не весь вывод) -- предотвращает false positive
- Пересечение с limit patterns намеренное -- `wait_on_limit` выступает переключателем поведения

При match: graceful exit с именем паттерна и подсказкой команды (e.g., "claude /usage").

---

## Stalemate Detection (Review Patience)

Раннее завершение ревью, когда reviewer не может договориться.

- CLI: `--review-patience=N`
- Config: `review_patience = N`
- Default: 0 (disabled)
- N = количество подряд идущих неизменных раундов до остановки

Механизм обнаружения:
1. До Claude-оценки: запомнить HEAD hash и diff fingerprint
2. После оценки: сравнить
3. Unchanged = тот же HEAD hash И тот же diff fingerprint
4. Session timeout: пропуск stalemate check

При stalemate: "stalemate detected after N unchanged rounds, review terminated early", переход к следующей фазе.

---

## Interactive Plan Creation

Создание плана через диалог с Claude с Q&A и ревью черновика.

- CLI: `--plan <file>` (triggers ModePlan)
- Prompt: `make_plan.txt`

Полный flow:
1. Claude исследует codebase, задаёт уточняющие вопросы
2. Вопросы отображаются через нумерованный список с опцией "Other (type your own answer)"
3. Q&A история хранится в progress file для контекста
4. Claude генерирует PLAN_DRAFT signal с содержимым плана
5. План отображается с рамками
6. Пользователь выбирает действие:
   - Accept: план готов
   - Revise: ввести feedback текст, Claude модифицирует
   - Interactive review: открыть `$EDITOR` (VISUAL, EDITOR env vars или vi)
     - При сохранении: вычисляется unified diff -> feedback для Claude
     - Если нет изменений: меню показывается снова
   - Reject: отмена
7. Loop до Accept и PLAN_READY signal
8. Файл плана записывается как `<input-file>-plan.md` рядом с исходным файлом
9. Промпт: "Continue with plan implementation?" -> если Yes, создаётся ветка и запускается full execution

Signals: QUESTION (JSON payload с options), PLAN_DRAFT (plan content between markers), PLAN_READY.

---

## Plan Selection (нумерованный список)

Автоматический выбор плана при запуске без указания файла.

- Когда: нет позиционного аргумента и не в review-only режиме
- Поиск: `plans_dir` (default: `docs/plans/`), исключая `completed/` subdirectory
- 1 файл: auto-select с сообщением "auto-selected: <path>"
- Несколько файлов: нумерованный список с промптом "Enter number (1-N):"

Config: `plans_dir = "docs/plans"` (default).

---

## Local Project Config (.rlx/)

Проект-локальная конфигурация, перекрывающая системные defaults.

Структура:
```
.rlx/
├── config.toml     # TOML, per-field merge с системными defaults
├── prompts/        # per-file fallback: local -> системный
│   └── task.txt    # override только task prompt
└── agents/         # per-file fallback: local -> системный
    └── custom.txt  # project-specific agent
```

Merge strategy:
- Config: per-field override (локальные значения перекрывают системные defaults, отсутствующие -- fallback)
- Prompts/Agents: per-file fallback (local -> системный для каждого файла)

---

## Configurable Agents с Frontmatter

5 default agents, встроенных в пакет:
- `implementation.txt` -- корректность реализации
- `quality.txt` -- баги, безопасность, race conditions
- `documentation.txt` -- необходимость обновления документации
- `simplification.txt` -- over-engineering
- `testing.txt` -- покрытие и качество тестов

YAML frontmatter:
```yaml
---
model: haiku|sonnet|opus
agent: general-purpose|...
---
```

- `model`: override модели для конкретного агента (full IDs нормализуются к коротким: sonnet, haiku, opus)
- `agent`: тип subagent для Claude Code Task tool
- Invalid model: dropped с warning, fallback к default

Loading: per-file fallback (local `.rlx/agents/` -> системный). Удаление файла НЕ отключает агента (используется системный default). Для отключения: удалить `{{agent:name}}` из prompt файлов.

Template variables в агентах: `{{DEFAULT_BRANCH}}`, `{{PLAN_FILE}}`, etc. -- расширяются перед использованием.

---

## Model Configuration

Два уровня модели:
- `claude_model` / `--claude-model` -- для task execution phase
- `review_model` / `--review-model` -- для review phases (fallback на claude_model)
- Per-agent model через frontmatter

Приоритет: CLI flag > config > Claude Code default (пустая строка).

---

## Color Customization

24-bit RGB (true color) поддержка через rich.

- CLI: `--no-color` -- полностью отключить цвета
- Config: RGB color strings

```toml
[colors]
task = "46,139,87"            # фаза задач (green)
review = "26,158,158"         # фаза ревью (teal)
warn = "212,147,13"           # предупреждения (amber)
error = "204,0,0"             # ошибки (red)
signal = "210,82,82"          # сигналы завершения/ошибки (red)
timestamp = "112,112,112"     # timestamp prefix (gray)
info = "128,128,128"          # информационные сообщения (gray)
```

Per-field merge: local -> системные defaults для каждого цвета.

---

## Commit Trailer Injection

Автоматическое добавление trailer-строки ко всем коммитам, созданным rlx.

- Config: `commit_trailer = "Co-authored-by: rlx <noreply@rlx.dev>"`
- Default: disabled (пустая строка)
- Нет CLI override (только config)

Формат: пустая строка автоматически вставляется перед trailer. Применяется ко всем коммитам через Service.

Use cases: атрибуция автора, DCO compliance, трекинг автоматизации.

---

## Кроссплатформенные различия

### Таблица поддержки

| Фича | Linux | macOS | Windows |
|------|-------|-------|---------|
| Сборка и запуск | Полная | Полная | Да, с ограничениями |
| Process groups (graceful shutdown kills all descendants) | Да | Да | Нет -- убивает только прямой процесс |
| SIGQUIT / Ctrl+\ (manual break) | Да | Да | Нет (stub no-op) |
| File locking (flock для exclusive progress access) | Да | Да | Нет (disabled) |

### Process Group Signals (Unix only)

На Unix при запуске claude процесса создаётся новая process group. При graceful shutdown отправляется SIGTERM всей группе, затем SIGKILL после таймаута. На Windows убивается только прямой процесс -- дочерние могут остаться.

### File Locking (Unix only)

fcntl.flock используется для эксклюзивного доступа к progress файлу. Два процесса rlx не будут писать в один и тот же progress файл одновременно.

### Build Tags Pattern (Python)

Платформенный код разделяется через условные импорты:
- `sys.platform` checks для Unix/Windows-специфичного кода
- `fcntl` (Unix) vs no-op (Windows) для file locking
- `signal.SIGQUIT` (Unix) vs no-op (Windows) для manual break
