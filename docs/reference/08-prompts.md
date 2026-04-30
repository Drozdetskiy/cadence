# Промпты rlx

Справочный документ по всем промпт-файлам для Python-порта rlx.

## Обзор системы промптов

Промпты хранятся в пакете rlx как embedded-ресурсы и могут быть переопределены пользователем:
- Локально (проект): `.rlx/prompts/<name>.txt`

Приоритет загрузки: локальный -> системный (в пакете). Если локальный файл существует -- используется он; если отсутствует -- системный default.

### Шаблонные переменные

Все промпты поддерживают переменные вида `{{NAME}}`, подставляемые при загрузке:

| Переменная | Описание | Fallback |
|---|---|---|
| `{{PLAN_FILE}}` | Путь к файлу плана (fallback: sibling `<stem>-completed<ext>`) | "(no plan file - reviewing current branch)" |
| `{{PROGRESS_FILE}}` | Путь к файлу прогресса | "(no progress file available)" |
| `{{GOAL}}` | Описание цели работы | "current branch vs {default_branch}" |
| `{{DEFAULT_BRANCH}}` | Имя default-ветки (main, master и т.д.) | "master" |
| `{{PLANS_DIR}}` | Директория планов | "docs/plans" |
| `{{agent:name}}` | Разворачивается в Task-инструкцию для агента | оставляется как есть с предупреждением |
| `{{PLAN_DESCRIPTION}}` | Описание пользователя для создания плана (только make_plan) | -- |

Дополнительно: если задан `commit_trailer` в конфиге, инструкция о trailer добавляется в конец каждого промпта (кроме agent-промптов -- трейлер добавляется один раз к финальному промпту).

### Раскрытие агентов ({{agent:name}})

При обработке промптов:
1. Ищет паттерн `{{agent:([a-zA-Z0-9_-]+)}}`
2. Для каждого совпадения загружает агента
3. Раскрывает содержимое агента с подстановкой base-переменных (без рекурсии агентов)
4. Форматирует:

```
Use the Task tool [with model=X] to launch a <subagent-type> agent with this prompt:
"<agent prompt with expanded variables>"

Report findings only - no positive observations.
```

Frontmatter агента влияет на вывод: `model` добавляет ` with model=X`, `agent` меняет тип субагента (по умолчанию `general-purpose`).

---

## task.txt -- Промпт выполнения задач

**Фаза:** PhaseTask (phase 1)
**Используется в:** run_task_phase() -> run_with_limit_retry()

### Переменные

- `{{PLAN_FILE}}` -- путь к плану
- `{{PROGRESS_FILE}}` -- путь к файлу прогресса
- `{{GOAL}}` -- описание цели
- `{{DEFAULT_BRANCH}}` -- default-ветка

### Сигналы

- `<<<RLX:ALL_TASKS_DONE>>>` -- все задачи завершены (COMPLETED mapped)
- `<<<RLX:TASK_FAILED>>>` -- задача провалена (FAILED mapped)

### Полный текст

```
Read the plan file at {{PLAN_FILE}}. Find the FIRST Task section (### Task N: or ### Iteration N:) that has uncompleted checkboxes ([ ]).

If NO Task section has [ ] but ## Success criteria, ## Overview, or ## Context still has [ ]: either satisfy those items and mark them [x] if actionable, or output <<<RLX:ALL_TASKS_DONE>>> if they are verification-only (manual testing, deployment, etc.) — do not loop indefinitely when remaining items are not actionable by you.

If a Task section has [ ] checkboxes you cannot complete (manual testing, deployment verification, external checks): mark them [x] with a note like "[x] manual test (skipped - not automatable)" and proceed. Do not loop indefinitely on non-automatable items inside Task sections.

NOTE: Progress is logged to {{PROGRESS_FILE}} - this file contains detailed execution steps and can be reviewed for debugging.

CRITICAL CONSTRAINT: Complete ONE Task section per iteration.
A Task section is a "### Task N:" or "### Iteration N:" header with all its checkboxes underneath.
Complete ALL checkboxes in that section, then STOP.
Do NOT continue to the next section - the external loop will call you again for it.

STEP 0 - ANNOUNCE:
Before starting work, output a brief overview (up to 200 words) explaining:
- Which task number you picked and its title
- What the task will accomplish
- Key files or components involved
This helps the user understand what's happening in the current iteration.

STEP 1 - IMPLEMENT:
- Read the plan's Overview and Context sections to understand the work
- Implement ALL items in the current Task section (all [ ] checkboxes under it)
- Write tests for the implementation

STEP 2 - VALIDATE:
- Run the test and lint commands specified in the plan (e.g., "cargo test", "go test ./...", etc.)
- Fix any failures, repeat until all validation passes

STEP 3 - COMPLETE (after validation passes):
- Update progress: edit {{PLAN_FILE}} and change [ ] to [x] for each checkbox you implemented in the current Task section. If Task sections are complete but ## Success criteria, ## Overview, or ## Context has [ ] items that the implementation satisfies, mark them [x] in this same edit to avoid extra loop iterations. If any such items are NOT satisfied, do NOT mark them and do NOT output ALL_TASKS_DONE — continue to the next iteration to address them.
- Commit all changes (code + updated plan) with message: feat: <brief task description>
- Check if any [ ] checkboxes remain in Task sections (### Task N: or ### Iteration N:)
- If NO more [ ] checkboxes in the entire plan, output exactly: <<<RLX:ALL_TASKS_DONE>>>
- If more Task sections have [ ] checkboxes, STOP HERE - do not continue

If any phase fails after reasonable fix attempts, output exactly: <<<RLX:TASK_FAILED>>>

REMINDER: ONE section (Task/Iteration) per loop cycle. After commit, STOP and let the loop handle the next section.

OUTPUT FORMAT: No markdown formatting (no **bold**, `code`, # headers). Plain text and - lists are fine. Do not echo phase names or step numbers - just do the work.
```

---

## make_plan.txt -- Промпт создания плана

**Фаза:** PhasePlan
**Используется в:** run_plan_creation() -> build_plan_prompt()
**Построение:** подставляет `{{PLAN_DESCRIPTION}}`, затем base variables + trailer

### Переменные

- `{{PLAN_DESCRIPTION}}` -- содержимое файла, указанного через `--plan <file>` (читается из файла)
- `{{PROGRESS_FILE}}` -- файл прогресса с историей Q&A
- `{{DEFAULT_BRANCH}}` -- default-ветка
- `{{PLANS_DIR}}` -- директория планов

### Сигналы

- `<<<RLX:QUESTION>>>` + JSON `{"question": "...", "options": [...]}` + `<<<RLX:END>>>` -- вопрос пользователю
- `<<<RLX:PLAN_DRAFT>>>` + содержимое плана + `<<<RLX:END>>>` -- черновик для ревью
- `<<<RLX:PLAN_READY>>>` -- план записан на диск
- `<<<RLX:TASK_FAILED>>>` -- пользователь отклонил план

### Механика --plan

`--plan <file>` читает содержимое указанного файла как описание плана. После принятия плана, результат записывается как `<file>-plan.md` рядом с исходным файлом (а не в plans_dir).

### Полный текст

```
You are helping create an implementation plan for: {{PLAN_DESCRIPTION}}

Progress log: {{PROGRESS_FILE}} (contains previous Q&A from this session)

IMPORTANT: Read the progress file first to see any questions you already asked and answers provided. Do not repeat questions.

## Step 0: Check for Existing Plan

FIRST, check if a plan file already exists in {{PLANS_DIR}}/ matching this request.
If a plan file for this feature already exists:
- Output <<<RLX:PLAN_READY>>> immediately
- Do NOT modify the existing plan
- STOP - do not output anything else

## Step 1: Read Progress File

Read {{PROGRESS_FILE}} to understand:
- What questions you have already asked
- What answers the user provided
- Any exploration notes from previous iterations

## Step 2: Explore the Codebase

If this is your first iteration (no Q&A in progress file):
- Search for relevant files and patterns
- Understand the project structure
- Identify existing conventions and patterns
- Find related code that will inform the implementation

## Step 3: Ask Clarifying Questions (if needed)

If you need user input to create a good plan, emit a QUESTION signal:

<<<RLX:QUESTION>>>
{"question": "Your question here?", "options": ["Option 1", "Option 2", "Option 3"]}
<<<RLX:END>>>

Rules for questions:
- Ask ONE question at a time
- Provide 2-4 concrete options (not vague like "other")
- Only ask if you genuinely need clarification
- Do not ask about implementation details you can decide yourself
- Focus on architectural choices, feature scope, and user preferences

After emitting QUESTION, STOP immediately. Do not continue. The loop will collect the answer and run another iteration.

## Step 3.5: Present Draft for Review

When you have enough information to create a plan, present it as a draft for user review BEFORE writing to disk.

Emit the plan draft:

<<<RLX:PLAN_DRAFT>>>
# <Title>

## Overview
<Brief description of what will be implemented>

## Context
- Files involved: <list relevant files>
...

## Implementation Steps
...
<<<RLX:END>>>

CRITICAL: After emitting PLAN_DRAFT, STOP immediately. Do not continue. Do not write the plan file yet.

The loop will:
1. Display the draft to the user with terminal rendering
2. Ask the user to Accept, Revise, Interactive review (open in $EDITOR), or Reject
3. Run another iteration with the user's decision

**Handling user responses:**

If user ACCEPTS (progress file contains "DRAFT REVIEW: accept"):
- Proceed to Step 4 to write the plan file to disk
- Then emit PLAN_READY

If user requests REVISION (progress file contains "DRAFT REVIEW: revise" and "FEEDBACK:"):
- Read the feedback from the progress file
- Feedback may be free-form text (from "Revise") or a unified diff with interpretation instructions (from "Interactive review" where the user edited the plan in $EDITOR). Both formats indicate what the user wants changed — apply the requested modifications
- Modify the plan based on the feedback
- Emit a new PLAN_DRAFT with the updated plan
- STOP and wait for next review

If user REJECTS (progress file contains "DRAFT REVIEW: reject"):
- Output exactly: <<<RLX:TASK_FAILED>>>
- STOP immediately - the user has cancelled plan creation

## Step 4: Write Plan File (after draft accepted)

This step executes ONLY after the user accepts your draft (progress file contains "DRAFT REVIEW: accept").

Write the accepted plan to disk:

1. Create the plan file next to the prompt file (the file that was passed to --plan)
2. Use this structure:

---
# <Title>

## Overview
<Brief description of what will be implemented>

## Context
- Files involved: <list relevant files>
- Related patterns: <existing patterns to follow>
- Dependencies: <external dependencies if any>

## Development Approach
- **Testing approach**: Regular (code first, then tests) or TDD (test first)
- Complete each task fully before moving to the next
- <Any project-specific approaches>
- **CRITICAL: every task MUST include new/updated tests**
- **CRITICAL: all tests must pass before starting next task**

## Implementation Steps

### Task 1: <Title>

**Files:**
- Modify: `path/to/file`
- Create: `path/to/new_file` (if any)

- [ ] first implementation step
- [ ] second implementation step
- [ ] write tests for this task
- [ ] run project test suite - must pass before task 2

### Task 2: <Title>
...

(continue for all tasks)

### Task N: Verify acceptance criteria

- [ ] run full test suite (use project-specific command)
- [ ] run linter (use project-specific command)
- [ ] verify test coverage meets 80%+

### Task N+1: Update documentation

- [ ] update README.md if user-facing changes
- [ ] update CLAUDE.md if internal patterns changed
---

## Step 4.5: Validate Plan Before Draft

Before emitting PLAN_DRAFT in Step 3.5, verify the plan against these criteria:

**Scope & Feasibility:**
- [ ] Tasks are reasonably sized (aim for 3-7 items; adjust if needed for coherence)
- [ ] Each task focuses on one component or closely related files
- [ ] Task dependencies are linear (no circular deps)
- [ ] External dependencies are minimized and clearly noted

**Completeness:**
- [ ] All requirements from the original description are addressed
- [ ] Each task specifies file paths where known (use patterns for discovery tasks)
- [ ] Each task that modifies code includes test items
- [ ] Task section checkboxes are automatable by the agent (no manual testing, deployment, or external verification items as `- [ ]` inside Task sections; those go in Post-Completion)

**Simplicity (YAGNI):**
- [ ] No unnecessary abstractions
- [ ] No "future-proofing" features not in the original request
- [ ] No backwards compatibility or fallbacks unless explicitly requested
- [ ] New files only for genuinely new components, not minor additions
- [ ] No over-engineered patterns when simpler solutions work

If validation fails, fix the plan before emitting PLAN_DRAFT.

Only after validation passes:
1. Emit PLAN_DRAFT (Step 3.5) and wait for user review
2. If user accepts, write the plan file (Step 4)
3. After writing the file, emit PLAN_READY:
   - Output exactly: <<<RLX:PLAN_READY>>>
   - STOP IMMEDIATELY - do not output anything else after this signal

CRITICAL RULES:
- DO NOT ask "Would you like to proceed?" or "Should I implement this?" or similar
- DO NOT wait for user approval - rlx handles confirmation externally
- DO NOT use natural language questions - only use <<<RLX:QUESTION>>> signal format
- DO NOT iterate or refine the plan after validation passes
- The PLAN_READY signal means "plan is complete, session is done"

OUTPUT FORMAT: No markdown formatting in your response text (no **bold**, `code`, # headers). Plain text and - lists are fine. The plan FILE should use markdown.
```

---

## review_first.txt -- Промпт первого ревью (4 агента)

**Фаза:** PhaseReview (phase 2)
**Используется в:** run_claude_review() с ReviewFirst prompt

### Переменные

- `{{PLAN_FILE}}` -- путь к плану
- `{{PROGRESS_FILE}}` -- файл прогресса
- `{{GOAL}}` -- описание цели
- `{{DEFAULT_BRANCH}}` -- default-ветка
- `{{agent:quality}}` -- агент качества кода
- `{{agent:implementation}}` -- агент корректности реализации
- `{{agent:testing}}` -- агент тестирования
- `{{agent:simplification}}` -- агент упрощения

### Сигналы

- `<<<RLX:REVIEW_DONE>>>` -- ревью завершено, проблем не найдено
- `<<<RLX:TASK_FAILED>>>` -- найдены проблемы, не удалось исправить
- Без сигнала -- проблемы найдены и исправлены, нужна ещё итерация

### Полный текст

```
Code review of: {{GOAL}}

Progress log: {{PROGRESS_FILE}} (contains task execution and previous review iterations)

## Step 1: Get Branch Context

Run both commands to understand what was done:
- `git log {{DEFAULT_BRANCH}}..HEAD --oneline` - see commit history (what was implemented)
- `git diff {{DEFAULT_BRANCH}}...HEAD` - see actual code changes

## Step 2: Launch ALL 4 Review Agents IN PARALLEL

All Task tool calls MUST be in the same message for parallel foreground execution.
Do NOT use run_in_background. Foreground agents run in parallel and block until all complete — no TaskOutput polling needed.

CRITICAL: Do NOT proceed to Step 3 until ALL 4 agents have returned results.

Agents to launch:
{{agent:quality}}
{{agent:implementation}}
{{agent:testing}}
{{agent:simplification}}

Each agent prompt should be short — do NOT paste the diff into it. Instead, instruct each agent to:
1. Run `git diff {{DEFAULT_BRANCH}}...HEAD` and `git diff --stat {{DEFAULT_BRANCH}}...HEAD` to get the changes
2. Read the actual source files to review code in full context
3. Report problems only - no positive observations

## Step 3: Collect, Verify, and Fix Findings

After agents complete:

### 3.1 Collect and Deduplicate
- Merge findings from all agents
- Same file:line + same issue → merge
- Cross-agent duplicates → merge, note both sources

### 3.2 Verify EVERY Finding (CRITICAL)
For EACH issue (bugs, test gaps, smells, over-engineering, error handling, docs, etc.):
1. Read actual code at file:line
2. Check full context (20-30 lines around)
3. Verify issue is real, not a false positive
4. Check for existing mitigations

Classify as:
- CONFIRMED: Real issue, fix it
- FALSE POSITIVE: Doesn't exist or already mitigated - discard

IMPORTANT: Pre-existing issues (linter errors, failed tests) should also be fixed.
Do NOT reject issues just because they existed before this branch - fix them anyway.

### 3.3 Fix All Confirmed Issues
1. Fix all CONFIRMED issues (all types: bugs, tests, smells, docs, etc.)
2. Run tests and linter to verify fixes - ALL tests must pass, ALL linter issues resolved
3. Commit fixes: `git commit -m "fix: address code review findings"`

## Step 4: Signal Completion

SIGNAL LOGIC - READ CAREFULLY:

IMPORTANT: Do not decide on a signal path until you have completed Steps 1-3 in full — all agents finished, all results collected, all findings verified and acted on.

REVIEW_DONE means "this iteration found ZERO issues" - NOT "I finished fixing issues".

Path A - NO confirmed issues found:
- You reviewed the code and found nothing to fix
- Output: <<<RLX:REVIEW_DONE>>>

Path B - Issues found AND fixed:
- You found issues, fixed them, and committed
- STOP HERE. Do NOT output any signal. Do NOT output REVIEW_DONE.
- The external loop will run another review iteration to verify your fixes.
- Your fixes might have introduced new issues - another iteration must check.

Path C - Issues found but cannot fix:
- Output: <<<RLX:TASK_FAILED>>>

OUTPUT FORMAT: No markdown formatting (no **bold**, `code`, # headers). Plain text and - lists are fine.
```

---

## review_second.txt -- Промпт второго ревью (2 агента)

**Фаза:** PhaseReview (phase 2, subsequent iterations)
**Используется в:** run_claude_review_loop() с ReviewSecond prompt

### Переменные

- `{{PLAN_FILE}}` -- путь к плану
- `{{PROGRESS_FILE}}` -- файл прогресса
- `{{GOAL}}` -- описание цели
- `{{DEFAULT_BRANCH}}` -- default-ветка
- `{{agent:quality}}` -- агент качества кода
- `{{agent:implementation}}` -- агент корректности реализации

### Сигналы

Те же, что у review_first.txt:
- `<<<RLX:REVIEW_DONE>>>` -- ревью завершено, проблем не найдено
- `<<<RLX:TASK_FAILED>>>` -- проблемы не удалось исправить
- Без сигнала -- проблемы исправлены, нужна ещё итерация

### Отличия от первого ревью

- Запускает только 2 агента (quality + implementation) вместо 4
- Фокус только на critical/major проблемах -- minor/style игнорируются

### Полный текст

```
Second code review pass of: {{GOAL}}

Progress log: {{PROGRESS_FILE}} (contains task execution and previous review iterations)

## Step 1: Get Branch Context

Run both commands to understand what was done:
- `git log {{DEFAULT_BRANCH}}..HEAD --oneline` - see commit history (what was implemented)
- `git diff {{DEFAULT_BRANCH}}...HEAD` - see actual code changes

## Step 2: Launch Review Agents IN PARALLEL

All Task tool calls MUST be in the same message for parallel foreground execution.
Do NOT use run_in_background. Foreground agents run in parallel and block until all complete — no TaskOutput polling needed.

CRITICAL: Do NOT proceed to Step 3 until BOTH agents have returned results.

Agents to launch:
{{agent:quality}}
{{agent:implementation}}

Each agent prompt should be short — do NOT paste the diff into it. Instead, instruct each agent to:
1. Run `git diff {{DEFAULT_BRANCH}}...HEAD` and `git diff --stat {{DEFAULT_BRANCH}}...HEAD` to get the changes
2. Read the actual source files to review code in full context
3. Report problems only - no positive observations

Focus only on critical and major issues. Ignore style/minor issues.

## Step 3: Verify and Evaluate Findings

### 3.1 Verify Each Finding
For each issue reported:
1. Read actual code at file:line
2. Verify issue is real (not false positive)
3. Check if it's truly critical/major severity

### 3.2 Act on Verified Findings

IMPORTANT: Pre-existing issues (linter errors, failed tests) should also be fixed.
Do NOT reject issues just because they existed before this branch - fix them anyway.

SIGNAL LOGIC - READ CAREFULLY:

IMPORTANT: Do not decide on a signal path until you have completed Steps 1-3 in full — all agents finished, all results collected, all findings verified and acted on.

REVIEW_DONE means "this iteration found ZERO issues" - NOT "I finished fixing issues".

Path A - NO issues found in this iteration:
- You reviewed the code and found nothing critical/major to fix
- Output: <<<RLX:REVIEW_DONE>>>

Path B - Issues found AND fixed:
1. Fix verified critical/major issues only
2. Run tests and linter - ALL tests must pass, ALL linter issues resolved
3. Commit fixes: `git commit -m "fix: address code review findings"`
4. STOP HERE. Do NOT output any signal. Do NOT output REVIEW_DONE.
   The external loop will run another review iteration to verify your fixes.
   Your fixes might have introduced new issues - another iteration must check.

Path C - Issues found but cannot fix:
- Output: <<<RLX:TASK_FAILED>>>

OUTPUT FORMAT: No markdown formatting (no **bold**, `code`, # headers). Plain text and - lists are fine.
```

---

## finalize.txt -- Промпт финализации

**Фаза:** PhaseFinalize
**Используется в:** run_finalize()
**Условие запуска:** `finalize_enabled = true` в конфиге, только после успешных ревью
**Режимы:** ModeFull, ModeReview (режимы с review pipeline)

### Переменные

- `{{DEFAULT_BRANCH}}` -- default-ветка

### Сигналы

Не использует сигналы -- best-effort, результат логируется, ошибки не блокируют.

### Полный текст

```
Post-completion finalize step.

Rebase your commits onto the latest {{DEFAULT_BRANCH}} and organize them for merge.

Steps:

1. Fetch latest changes: `git fetch origin`

2. Rebase onto {{DEFAULT_BRANCH}}:
   - Run: `git rebase origin/{{DEFAULT_BRANCH}}`
   - If conflicts occur, resolve them and continue rebase
   - If rebase fails completely, abort with `git rebase --abort` and report the issue

3. Review commit history:
   - Run: `git log origin/{{DEFAULT_BRANCH}}..HEAD --oneline`
   - If there are many small fix commits, consider squashing them
   - Keep meaningful commit boundaries (feature commits separate from fix commits)

4. Optional: Interactive rebase to clean up history:
   - Only if there are 5+ commits that could be logically combined
   - Run: `git rebase -i origin/{{DEFAULT_BRANCH}}`
   - Squash related fix commits into their parent feature commits
   - Reword commit messages if needed for clarity

5. Verify the branch is ready:
   - Run tests using the project's test command (check CLAUDE.md or plan file for the correct command)
   - Run linter if applicable

Report what was done. This step is best-effort - if rebase fails, explain why and the branch remains as-is.

OUTPUT FORMAT: No markdown formatting (no **bold**, `code`, # headers). Plain text and - lists are fine.
```

---

## Карта промптов по фазам

| Фаза | Промпт | Кол-во агентов | Сигналы |
|---|---|---|---|
| PhaseTask | task.txt | 0 | ALL_TASKS_DONE, TASK_FAILED |
| PhasePlan | make_plan.txt | 0 | QUESTION, PLAN_DRAFT, PLAN_READY, TASK_FAILED |
| PhaseReview (1st) | review_first.txt | 4 (quality, implementation, testing, simplification) | REVIEW_DONE, TASK_FAILED |
| PhaseReview (2nd) | review_second.txt | 2 (quality, implementation) | REVIEW_DONE, TASK_FAILED |
| PhaseFinalize | finalize.txt | 0 | нет (best-effort) |

## Потоки данных между промптами

```
task.txt
  └─ [ALL_TASKS_DONE] ──> review_first.txt (4 agents)
                              └─ [REVIEW_DONE] ──> review_second.txt (2 agents)
                                                      └─ [REVIEW_DONE] ──> finalize.txt
```
