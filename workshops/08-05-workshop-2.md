## 🎯 What you'll learn

By the end of this workshop, you will:

- Have **run the full Chapter 8 stack** — planning, Ralph loop, and subagents — on a substantial real-world project
- Have **inspected the artifacts each mechanism produces** and can point to concrete evidence of what each did
- Have a **direct feel for which mechanisms earned their keep** on this specific task shape, and which were less useful
- Have run at least one **ablation** — the same task without one of the mechanisms — to feel the delta

The point isn't to build a perfect e-commerce API. It's to observe what happens when the harness you built runs a task big enough to exercise every layer.

---

## 🧪 The task

You're going to build an **e-commerce API** with the following requirements:

- **Users:** registration, login (JWT-based auth), get/update own profile, delete own account
- **Inventory:** admins can create/update/delete products; anyone can list and view; each product has name, description, price, stock count
- **Orders:** authenticated users can place orders (specifying products and quantities); orders check inventory availability, deduct stock atomically, and record the order; users can view their own order history
- **No payments** — orders record intent and stock changes only; payment integration is out of scope
- **Framework:** Flask + SQLAlchemy + Flask-JWT-Extended, SQLite for storage
- **Structure:** proper project structure with separate modules for models, auth, users, products, orders
- **Tests:** pytest suite covering happy paths for each endpoint plus at least four edge cases (unauthenticated access to protected endpoints, insufficient stock on order, non-admin trying to modify products, ordering a nonexistent product)
- **Docs:** requirements.txt with pinned versions, README with setup and usage instructions

This is genuinely more substantial than the Flask blog API from 8.3's demo. Real-world domain (e-commerce has actual complexity), realistic multi-role authorization (users vs. admins), and stateful operations (stock deduction, order history) that require the pieces to work together.

**Expected artifacts on disk when done:**

```
workspace/
├── plan.md
├── requirements.txt
├── README.md
├── app/
│   ├── __init__.py
│   ├── config.py
│   ├── models.py         (User, Product, Order, OrderItem)
│   ├── auth.py           (register, login)
│   ├── users.py          (profile CRUD)
│   ├── products.py       (inventory management)
│   └── orders.py         (order placement, history)
└── tests/
    ├── conftest.py
    ├── test_auth.py
    ├── test_users.py
    ├── test_products.py
    └── test_orders.py
```

Yours may vary in structure — the agent will make its own decomposition decisions.

---

## 🛠️ Setup

Clean the workspace and start fresh:

```bash
rm -rf workspace/*
rm -rf workspace/.tool_outputs
rm -rf .harness/subagents
python -m harness.agent
```

Confirm your config is at chapter defaults:

```python
# harness/config.py
COMPACTION_THRESHOLD: int = 60_000
OFFLOAD_THRESHOLD_TOKENS: int = 1_000
PLAN_REMINDER_INTERVAL: int = 2
RALPH_MAX_CONTINUATIONS: int = 10
SUBAGENT_STEP_BUDGET: int = 15
```

---

## 🅰️ The main run — plan-first, then Ralph with subagent hint

### Turn 1: Request the plan

Paste this at the `you >` prompt:

```
I want to build an e-commerce API. Requirements:

- Users: registration, login (JWT auth), get/update own profile, delete own account
- Inventory: admins create/update/delete products; anyone can list and view; each product has name, description, price, stock count
- Orders: authenticated users place orders (products + quantities); check inventory, deduct stock atomically, record the order; users can view their own history
- No payment integration
- Framework: Flask + SQLAlchemy + Flask-JWT-Extended, SQLite for storage
- Structure: proper project layout with separate modules for models, auth, users, products, orders
- Tests: pytest covering happy paths plus at least 4 edge cases (unauthenticated access to protected endpoints, insufficient stock, non-admin trying to modify products, ordering a nonexistent product)
- Docs: requirements.txt with pinned versions, README with setup and usage

Before writing any code, draw up a detailed plan in plan.md — one task per meaningful piece of work — so I can review. Don't start executing yet.
```

Wait for the agent to produce the plan.

### 🔍 Inspection point 1 — the plan

Before invoking Ralph, look at what the agent decomposed to:

```bash
cat workspace/plan.md
```

Take stock:

- **How many tasks?** A well-decomposed plan for this task should have 8-12 tasks (setup, models, auth, users, products, orders, tests, README, plus maybe validation/errors).
- **Task shape:** are tasks meaningful chunks of work (*"Implement products endpoints"*) or trivially fine (*"Import Flask"*)?
- **Independence signals:** which tasks look independent from each other, and which are clearly dependent? Auth and models are typically prerequisites for endpoints; the individual endpoint modules are usually independent once models exist.

If the plan is coarser or finer than you'd like, this is your chance to push back before code gets written:

```
Break the endpoints into per-resource tasks (one for users, one for products, one for orders) rather than a single "endpoints" task.
```

Or:

```
Merge the imports and config setup into one initial task.
```

Iterate until you're satisfied with the decomposition. This is the plan-first workflow's whole value.

### Turn 2: Invoke Ralph with an independence hint

Once the plan looks good, hand execution to Ralph:

```
/ralph now execute — several tasks are independent of each other
```

Now watch the harness work. You'll see:

- Ralph continuation notices between cycles
- Occasional `[Subagent spawning: ...]` and `[Subagent completed: ...]` notices when the parent decides to delegate
- `[update_plan(...)]` calls marking progress
- `[Offloaded N tokens from ...]` notices if any tool output was big enough (bash outputs from big git operations, etc.)
- Possibly `[Context compacted at X tokens]` if the parent's context grows past 60k

The whole run typically takes 5-15 minutes of wall-clock time and 3-10 Ralph continuations. When Ralph prints `[Ralph] Goal met: ...`, the parent returns control to you and the build is complete.

---

## 🔍 Guided artifact inspection

Now the interesting part. Every mechanism in Chapter 8 produced observable evidence during that run. Go look at each one — this is where the workshop's value lives.

### 1. `workspace/plan.md` — the plan's final state

```bash
cat workspace/plan.md
```

**What to notice:**

- All tasks should be marked `[x]` (done)
- The `## Log` section should have timestamped entries for every plan change — added, in-progress, done. Read the log top-to-bottom to see the temporal ordering of work.
- Look for `notes:` fields on tasks — subagent summaries often get captured here by the parent

**What this tells you:** plan.md is the durable state that survived Ralph's context resets. Every continuation started fresh but bootstrapped from this file. Without planning support (8.2), Ralph would have had nothing to check against.

### 2. `.harness/subagents/` — subagent execution logs

```bash
ls .harness/subagents/
```

Each file is one subagent's full execution log. Pick one and read it:

```bash
cat .harness/subagents/2026-*.jsonl | head -80
```

**What to notice:**

- The subagent's system prompt (worker-focused, from `subagent.txt`)
- The task the parent delegated (single user message with a scoped instruction)
- The tool calls the subagent made (filtered set — no `spawn_subagent`, no `update_plan`, no `remember`)
- The subagent's final summary — this is exactly the string the parent received

**What this tells you:** each subagent's tool calls stayed on disk, not in the parent's context. The parent got a one-paragraph summary; students can inspect the logs to see the full work. If the parent had done all this work itself, every one of those tool outputs would have accumulated in the parent's context.

### 3. `workspace/.tool_outputs/` — offloaded tool outputs

```bash
ls workspace/.tool_outputs/ 2>/dev/null | head
```

**What to notice:**

- May be empty (this task's tools don't produce huge outputs, unlike research tasks with big web_search results)
- If present, each file is one offloaded tool output — usually a big bash output or git operation
- The parent's context saw the head/tail; the full content lives here

**What this tells you:** offloading (7.3) was firing quietly in the background whenever a tool produced a lot of output. On this task it may not have fired much, which is fine — the mechanism exists for outputs that would otherwise flood context.

### 4. `.harness/context_log.jsonl` — compaction history

```bash
wc -l .harness/context_log.jsonl 2>/dev/null
```

**What to notice:**

- Line count — each compaction event writes one line
- May be 0 if compaction never fired (the run stayed under 60k tokens throughout)
- If nonzero, the file contains the pre-compaction messages that got summarized away

**What this tells you:** compaction (7.2) is a safety net. On a well-behaved run with subagents keeping the parent's context lean, compaction may not fire at all — which is a sign the higher-layer mechanisms (subagents, Ralph's fresh-context resets) are doing their job. Compaction firing many times would indicate the parent's context is growing faster than expected.

### 5. `workspace/app/`, `workspace/tests/` — the built API

```bash
ls workspace/app/
ls workspace/tests/
```

Check that the pieces exist:

```bash
find workspace -name "*.py" -not -path "*/.tool_outputs/*" | head -20
```

Try running the tests:

```bash
cd workspace
pip install -r requirements.txt
pytest
cd ..
```

**What to notice:**

- Do all requested modules exist (models, auth, users, products, orders)?
- Do the tests actually run? Do they pass?
- Are the four requested edge cases covered (search test files for the failure conditions)?
- Is the README present and reasonable?

**What this tells you:** the harness kept the multi-turn, multi-subagent work coherent enough to produce a working project. This is the point of the whole chapter — long-horizon execution that doesn't degrade.

---

## 🅱️ Ablation — run again without subagents

If you have another 15 minutes, this is where the mechanism's value becomes vivid. Clean up and try the same task without inviting subagent use:

```bash
rm -rf workspace/*
rm -rf workspace/.tool_outputs
rm -rf .harness/subagents
python -m harness.agent
```

Turn 1: same plan-first request as before (paste the same requirements). Wait for the plan.

Turn 2:

```
/ralph now execute — do all the work yourself in this session, do not spawn subagents
```

Watch the run. Compare to the main run:

- **Parent's context size at completion** — should be visibly larger than the main run's, because all the tool outputs from every subtask now accumulate in the parent's context
- **Compaction firings** — much more likely to fire because the parent's context grows fast without subagents keeping it lean
- **Wall-clock time** — often similar (both runs execute the same tool calls in aggregate; the difference is where they live) or actually *worse* for the ablation because compaction's summarization calls add latency
- **Result quality** — often comparable, sometimes worse. If the parent's context got messy enough, it may lose track of decisions made earlier in the run

The gap between the two runs is what subagent spawning bought you.

**Alternative ablation** (if you have time): run without Ralph. Same plan-first turn 1, but for turn 2 use plain multi-turn conversation (*"go ahead," "continue," "next task"* etc.) instead of `/ralph`. Count how many user turns it takes to reach completion. The difference is what Ralph automates.

---

## 🤔 Reflection prompts

Take 10 minutes to think through these. Write down your answers — committing to a position sharpens the thinking. There aren't universal right answers.

**1. Which mechanism did the most work on this task? Which did the least?**

Compare artifacts and inferred activity. Look at how many plan updates fired, how many Ralph continuations, how many subagents, whether compaction ever ran. Different tasks stress different mechanisms — this task's shape tells you where the weight fell.

**2. Was there a moment where the model surprised you?**

Ask-permission behavior, over-optimistic task completion, thin subagent summaries, plan status toggles, decomposition choices you disagreed with. What did you see, and did the mechanisms handle it or was it something you had to work around?

**3. On what shape of task would you *disable* each mechanism?**

Threshold or command-based, either works — set `RALPH_MAX_CONTINUATIONS = 0` to disable Ralph, don't invoke `/ralph`, tell the model not to spawn subagents, etc. When is each mechanism actively unhelpful?

**4. The subagent's summary is the parent's whole view of the subagent's work. What if a summary is wrong?**

You have the execution logs. Walk through the mental model: parent sees summary → parent makes a decision based on summary → decision is wrong because summary was wrong. What in the current design catches this, and what doesn't?

**5. The plan is durable across Ralph's context resets. What if the plan itself is wrong?**

Same shape as #4 but higher-stakes. If the initial decomposition is bad (missing subtasks, wrong dependencies), Ralph will drive the wrong plan to completion. What's the mitigation? What could the harness add here that we didn't build?

**6. You built this stack over four lessons. If you had to cut one mechanism to reduce complexity, which would go?**

Real design question. Rank them by "value delivered vs. code weight." Which mechanism could you live without, and what would you lose?

---

## 📌 What you should take away

**The full stack composed cleanly on a real project.** Planning gave the decomposition; Ralph drove the plan to completion across context resets; subagents kept the parent's context lean by delegating independent pieces. Compaction and offloading — from Chapter 7 — provided safety nets that mostly didn't need to fire on a well-mechanism'd session. This composition is the whole point of building the layers in order.

**Every mechanism has a cost.** Planning adds tool overhead (`update_plan` calls, plan injection on session start, periodic reminders). Ralph adds evaluation calls per continuation. Subagents add per-delegation overhead. On tasks that don't need the mechanisms, these costs are pure loss. The user-invoked `/ralph` design lets you opt in per task; subagent use is soft-guided; planning fires when the model recognizes multi-step shape. Cost pays off only when task shape matches.

**Artifacts on disk are the durable record.** `plan.md`, `.harness/subagents/*.jsonl`, `.harness/context_log.jsonl`, `workspace/.tool_outputs/` — these outlive any single session. Debugging a session that produced a bad result means reading these files, not asking the agent what happened. Chapter 9's observability work is going to formalize this into structured tracing, but even now, the artifact discipline gives you post-hoc inspection.

**Ralph doesn't verify substance; it verifies structure.** Ralph's plan-check catches *"plan items still open"* and its semantic check catches *"claimed completion doesn't match the goal shape."* Neither checks *"the tests actually pass"* or *"the code actually works."* If the agent claims completion and the artifact looks superficially right, Ralph declares goal met. This is a real limitation — actual verification requires either running tests, or Chapter 9's evaluation harness against a benchmark.

**Model behavior varies run-to-run.** Your run may have looked different from what these instructions describe. Different decomposition, different subagent choices, different completion timing. The mechanisms tolerate variance because they're designed around structural signals (plan open items, task delegation, context growth) rather than specific model outputs. That's why they work — a mechanism that assumed the model behaves the same way twice would break on the first divergent run.

---

## 🎯 Restoring your setup

Confirm your config is back to defaults after any ablation experiments:

```python
COMPACTION_THRESHOLD: int = 60_000
OFFLOAD_THRESHOLD_TOKENS: int = 1_000
PLAN_REMINDER_INTERVAL: int = 2
RALPH_MAX_CONTINUATIONS: int = 10
SUBAGENT_STEP_BUDGET: int = 15
```

You can keep the workspace with the built API, or clean it:

```bash
rm -rf workspace/*
rm -rf workspace/.tool_outputs
rm -rf .harness/subagents
```

The `.harness/context_log.jsonl` is your compaction audit trail — worth keeping for future debugging.

---

## 🔗 Where this leaves us

You've now built the complete long-horizon execution stack:

- **Planning support** (8.2) — persistent decomposition the model maintains via `update_plan`, harness-enforced reminders
- **Ralph loop** (8.3) — user-invoked continuation mechanism with two-stage completion evaluation and fresh-context resets
- **Subagent spawning** (8.4) — delegation of independent subtasks to isolated workers with tool subsets and execution logs

And you've used all three together on a real project.

Chapter 8 is complete. Your harness now handles tasks that would have degraded across a single session in Chapter 7 or stopped short of completion in Chapter 4.

**Chapter 9 shifts audiences.** Everything you've built so far was for the agent. Chapter 9 builds for *you* — the engineer running and improving the harness. Tracing, evaluation, failure taxonomies, optimization loops. Without observability, all the mechanisms you built are opaque: they work most of the time, but when they don't, you can't easily tell why. Chapter 9 is about seeing clearly enough to iterate.