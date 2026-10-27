## 🎯 What you'll learn

By the end of this workshop, you will:

- Have **inspected your 9.2 traces for four common failure signatures** — ask-permission loops, status toggling, thin subagent summaries, under-decomposition
- Have **generated additional traces on purpose** to see the failure modes your 9.2 session didn't naturally produce
- Have **matched each failure signature to the harness mechanism** that addresses it (planning, Ralph, subagents, directive prompts)
- Have built the beginning of an intuition for *what normal looks like*, which is the real skill underneath specific signature recognition

The lesson has an honest limit: four signatures aren't a complete taxonomy. The point isn't to memorize them — it's to build the pattern-matching habit so you can recognize new signatures on your own.

---

## 🧠 The core idea

> **Trace-reading is a skill, not a lookup table. Specific signatures help you recognize familiar failures fast, but the real skill is knowing what normal looks like well enough that abnormal stands out. You build that intuition by inspecting many traces — including boring ones that went fine. A trace tells you more by what it doesn't show than by what it does: no update_plan calls, no write calls, a single huge LLM call, nesting that's suddenly flat where you expected depth.**
> 

The failure signatures below are scaffolding. The habit is the actual lesson.

---

## 🧰 Setup

Open smith.langchain.com and navigate to your `agent-harness` project. You should see runs from your 9.2 session (the Flask blog build, if you followed the lesson directly) plus any additional work since.

If you don't have any traces because you skipped 9.2's demo, go back and run that first — this lesson has nothing to work with otherwise.

Keep the dashboard open in a tab. Each failure walkthrough below will ask you to inspect specific runs.

**One general caveat before starting.** The specific UI references below are LangSmith's. If you're using Langfuse, OpenTelemetry, or another backend, the exact field names and panel locations differ — but the underlying patterns (span counts, nesting depth, input size, latency distributions) are universal. Translate accordingly.

---

## 🔍 Failure signature 1 — Ask-permission loops

### What it is

The model stops after each piece of work to ask the user for confirmation. *"Would you like me to save this to a file?"* *"Shall I proceed with the next task?"* The agent produces plausible-sounding text but doesn't actually do anything until the user replies.

Covered in 8.2 and 8.3 as a real failure mode. The 8.3 Ralph loop was partly designed to break out of these by injecting fresh continuation prompts.

### Where to find it

**In your existing traces:** probably not — the 9.2 demo used `/ralph now execute — begin implementing the plan, do not ask for confirmation between tasks`, which specifically avoids this. If you ever ran a plain multi-turn session without directive continuations, check those.

**To reliably reproduce it:** clean the workspace and run this specifically crafted turn:

bash

```bash
rm -rf workspace/*rm -rf workspace/.tool_outputsrm -rf .harness/subagentspython -m harness.agent
```

```
you > I want to build a simple todo list REST API with Flask. Could
      you walk me through building it step by step? I'd like to review
      each step before you proceed to the next one.
```

The phrasing *"walk me through,"* *"step by step,"* and *"review each step"* reliably triggers ask-permission mode in most models. Let the agent run one turn and respond to any question with *"continue."* Repeat 2-3 times. Each turn should show the model proposing to do something and asking for permission rather than doing it.

### What to look for in the trace

Open the project view and look at the top-level runs for your ask-permission session. Characteristic signals:

- **High count of `ChatOpenAI` runs** relative to **low count of `dispatch` child spans.** A healthy working session has many tool dispatches nested under LLM calls. An ask-permission session has LLM calls that produce text responses but few or no tool calls.
- **Click into one of the LLM runs.** Its outputs will show the model's response as pure text (not a tool_calls array). Check several runs in a row — if most are text-only responses, the model is talking rather than doing.
- **Short run latency.** Text-only responses finish fast (~2-4 seconds) because the model isn't dispatching tools or waiting for results. A session that should take minutes finishes in a flurry of fast turns that produce nothing on disk.
- **Zero `write`, `bash`, or `update_plan` dispatches across the session.** This is the giveaway. If the task should have produced files or plan updates and you see neither in the trace, the model never actually worked.

### Root cause

The task framing conditioned the model into deferential mode. Phrases like *"walk me through,"* *"review each step,"* *"let me know when ready"* read as *"I want to be in the driver's seat; narrate before acting."* The model complied, producing narration instead of action.

### What addresses it

- **The planning + Ralph pattern from 8.3** — ask for the plan explicitly, then use `/ralph now execute` with a directive continuation prompt. The directive prompt breaks the deferential framing.
- **Prompt phrasing matters.** Terse, imperative task descriptions (*"Build X with Y, Z, and W"*) rarely trigger ask-permission mode. Collaborative-sounding descriptions (*"let's walk through this together"*) often do.

---

## 🔍 Failure signature 2 — Status toggling

### What it is

The agent calls `update_plan` to mark a task done, then calls it again to mark the same task back to todo or in-progress, then again to mark it done. The plan status flips repeatedly without the agent doing any actual work on the task. Visible in your 8.3 Ralph test runs (continuations #3 and #4 of your real session).

### Where to find it

**In your existing traces:** possibly in your 9.2 Flask blog run — this behavior is model-dependent and sometimes appears when Ralph fires continuations. Check your Ralph-driven sessions for runs that contain multiple `dispatch: update_plan` spans in sequence.

**To reliably reproduce it:** this one is harder to force on demand because it depends on model mood and context state. Your best bet is to re-run your 9.2 Flask blog build — if you see 2+ Ralph continuations, there's a decent chance at least one of them exhibits toggling.

### What to look for in the trace

Find a `ChatOpenAI` run that contains several `update_plan` dispatches. Look at the arguments:

```
└─ ChatOpenAI                                   (8.2s)
   ├─ dispatch: update_plan                     (0.1s)
   │   inputs: {"task": "Create models", "status": "done"}
   ├─ dispatch: update_plan                     (0.1s)
   │   inputs: {"task": "Create models", "status": "todo"}
   └─ dispatch: update_plan                     (0.1s)
       inputs: {"task": "Create models", "status": "in_progress"}
```

Three `update_plan` calls on the same task, status flipping, no other tool calls (no `write`, no `bash`) between them. The agent is reorganizing the plan state instead of doing work.

Compare to a healthy turn:

```
└─ ChatOpenAI                                   (10.1s)
   ├─ dispatch: write                           (0.2s)
   │   inputs: {"path": "app/models.py", ...}
   └─ dispatch: update_plan                     (0.1s)
       inputs: {"task": "Create models", "status": "done"}
```

One `write` to produce the file, one `update_plan` to mark it done. Linear forward motion.

### Root cause

Fresh-context resets (from Ralph) sometimes leave the model uncertain about what state the plan is actually in. Without the prior turn's reasoning in context, the model may re-interpret a task's status based on surface signals in plan.md — reading *"Create models"* as done then second-guessing itself and flipping back.

Also happens when the Guidelines rule *"ALWAYS call update_plan when you complete a step"* fires in the model's head but the model hasn't completed anything new — it reaches for `update_plan` because the rule says to, picks the first open task, toggles its status, then reconsiders.

### What addresses it

- **The behavior is annoying but not blocking.** Ralph's plan-check reads the final state of plan.md, so toggling that lands on *"done"* for genuinely completed tasks still terminates correctly. In your 8.3 run, continuation #4 did a mass cleanup after #3's chaos.
- **Directive continuation prompts help** — `/ralph <instruction>` with specific work instructions is less prone to this than plain `/ralph` with its generic *"continue"* prompt. The specific instruction gives the model something concrete to do, which beats status-management as the "obvious thing to do next."
- **8.4's subagent pattern reduces this failure's visibility** — subagents don't call `update_plan`, so when work is delegated the parent only toggles after the subagent returns (clean one-at-a-time updates).

---

## 🔍 Failure signature 3 — Thin subagent summaries

### What it is

A subagent is spawned, runs for a few seconds, returns a plausible-sounding summary string. The parent captures the summary and marks the plan task done. But the subagent didn't actually do much work — it used knowledge from the model's training rather than fetching anything fresh, or it took one shortcut and declared completion.

Specifically visible in the e-commerce evaluation run from 8.4 — subagents reached for `recall` on topics like Kubernetes and event sourcing, getting mostly empty results, then wrote up general knowledge as their summary.

### Where to find it

**In your existing traces:** likely. If your 9.2 Flask blog session triggered subagent spawns, check the subagent subtrees. If you ran the 8.4 e-commerce demo with tracing on, check those subagent spans.

**To reliably reproduce it:** run a research-heavy task with subagents and see what retrieval tools they reach for:

```
you > I want three technical evaluations: whether to adopt (1) Rust for
      our backend, (2) Postgres over MySQL, (3) a monorepo structure.
      Each independent of the others.

      Draw up a plan first with one task per evaluation. Don't execute yet.
```

Then:

```
/ralph now execute — these evaluations are independent
```

Check the subagent subtrees in the trace.

### What to look for in the trace

Find a `dispatch: spawn_subagent` run and expand the `subagent_react_loop` child. A thin subagent trace looks like:

```
└─ dispatch: spawn_subagent                     (6.1s)
   └─ subagent_react_loop                       (6.0s)
      ├─ ChatOpenAI                             (3.2s)
      ├─ dispatch: recall                       (0.4s)
      │   inputs: {"query": "Rust backend adoption"}
      │   outputs: "[no relevant results]"
      └─ ChatOpenAI                             (2.3s)
          outputs: "Rust offers strong performance guarantees..."
```

**Signals:**

- **Few tool dispatches overall** — 1-2 instead of the 4-6 you'd see on a thorough research run
- **Zero `web_search` calls** on a task that clearly needed external information (framework comparisons, current best practices, 2026 state of X)
- **`recall` calls with empty or thin outputs** followed by substantive-looking summaries — the model is confabulating from training knowledge after retrieval failed
- **Short total subagent latency** — 4-8 seconds instead of 20-40 seconds for a well-researched evaluation

Compare to a healthy research subagent:

```
└─ dispatch: spawn_subagent                     (28.4s)
   └─ subagent_react_loop                       (28.3s)
      ├─ ChatOpenAI                             (3.1s)
      ├─ dispatch: web_search                   (1.4s)
      ├─ dispatch: read                         (0.1s)
      ├─ ChatOpenAI                             (4.2s)
      ├─ dispatch: web_search                   (1.6s)
      ├─ ChatOpenAI                             (3.9s)
      └─ ChatOpenAI                             (3.1s)
          outputs: "Rust offers strong performance guarantees. Current
                    2026 adoption data shows... [specific numbers from
                    sources]"
```

Multiple `web_search` calls, follow-up `read`s on offloaded results, substantive output with grounded specifics.

### Root cause

The subagent's system prompt (`harness/prompts/subagent.txt`) is deliberately minimal — just worker framing and tool-handling notes. It doesn't contain the retrieval-routing guidance that the parent's `system.txt` has (*"use web_search for external information, recall only for project corpus"*). The subagent, lacking that guidance, defaults to whatever retrieval tool it reaches first.

### What addresses it

- **Short-term:** when you see this in a subagent trace, the fix is in the parent — give subagents more scoped task descriptions that explicitly name the research expectation (*"spawn_subagent with task='Research adoption of X. Use web_search extensively; recall only if you suspect project-specific context exists.'"*). The parent can inject this routing into the task text.
- **Longer-term:** add a retrieval-routing paragraph to `subagent.txt`, as discussed in the 8.4 build walkthrough. This is the cleanest fix but changes the harness code — appropriate for a real deployment, out of scope for this workshop.
- **Diagnostic value:** this signature tells you more about *your task description to subagents* than about the model. If subagents are consistently thin, your task descriptions aren't demanding enough.

---

## 🔍 Failure signature 4 — Under-decomposition

### What it is

The agent produces a plan.md with one or two tasks for what should be a 6-10 subtask job. Then executes the whole thing in a single ReAct cycle with many tool calls, marks the one giant task done, and claims completion. The plan existed but didn't do its job — nothing was decomposed.

Visible in your original 8.3 Flask API run (before the demo revision) — the agent made a single plan item *"Build a Flask REST API for a small blog"* and did everything inside one ReAct loop.

### Where to find it

**In your existing traces:** possible. Check your 9.2 session's first LLM runs after the planning turn. If the plan.md injection shows 1-2 tasks for a substantial build, that's it.

**To reliably reproduce it:** send a terse task without any plan-first instruction:

bash

```bash
rm -rf workspace/*rm -rf workspace/.tool_outputsrm -rf .harness/subagentspython -m harness.agent
```

```
you > Build a Flask REST API for a small blog with user auth, posts
      CRUD, comments, and tests.
```

No plan-first instruction, no "draw up a detailed plan" phrasing — just the task. The model will often skip meaningful planning and attempt the whole thing at once.

### What to look for in the trace

Find the first `ChatOpenAI` run after the user turn. Its outputs will show `update_plan` tool calls if planning fired at all. Count how many — if there are 0 or 1-2, decomposition didn't happen.

If plan.md was created (even thinly), check the next `ChatOpenAI` run — the one that starts executing:

```
└─ ChatOpenAI                                   (34.7s)
   ├─ dispatch: mkdir                           (0.1s)
   ├─ dispatch: write                           (0.2s)   (requirements.txt)
   ├─ dispatch: write                           (0.3s)   (app/__init__.py)
   ├─ dispatch: write                           (0.4s)   (app/models.py)
   ├─ dispatch: write                           (0.2s)   (app/auth.py)
   ├─ dispatch: write                           (0.4s)   (app/posts.py)
   ├─ dispatch: write                           (0.4s)   (app/comments.py)
   ├─ dispatch: write                           (0.5s)   (tests/test_auth.py)
   ├─ dispatch: write                           (0.4s)   (tests/test_posts.py)
   ├─ dispatch: write                           (0.3s)   (README.md)
   ├─ dispatch: git_commit                      (0.9s)
   └─ dispatch: update_plan                     (0.1s)   (status="done")
```

**Signals:**

- **One LLM call with many tool dispatches.** The whole project built inside a single model turn. Impressive-looking but concerning — the model had to decide everything at once with no checkpoints.
- **Very high model latency** (20-40+ seconds) because the model is generating a long sequence of tool calls in one shot
- **Single `update_plan` dispatch at the end** marking everything done
- **High input token count** on the LLM call — the model had to hold the whole plan in context to produce all the writes at once

Compare to a well-decomposed trace: 6-10 separate `ChatOpenAI` runs (one per Ralph continuation or per logical subtask), each with 1-3 writes and an `update_plan`. Linear progress, each step verifiable.

### Root cause

Without an explicit *"draw up a detailed plan first, don't start executing yet"* instruction, models default to going straight for the work. The Guidelines rule *"For any multi-step task, create initial plan on first turn"* (from 8.2) is soft guidance — the model's bias toward *"do the task"* often wins over *"plan first."*

### What addresses it

- **The plan-first-then-execute pattern from 8.3** — explicitly ask for a plan with granular tasks before any work. Then use `/ralph` to execute.
- **8.4's subagent pattern depends on good decomposition.** With no decomposition, there's nothing independent to delegate — the parent does everything itself and subagents never fire.
- **This is the single biggest leverage point you have.** Most of the long-horizon mechanism stack (Ralph's plan-check, subagent delegation, Ralph's semantic verification) depends on a reasonable plan existing. Fix the planning problem and most downstream problems reduce.

---

## 🔍 One meta-pattern across all four signatures

Look at the four signatures together. Each one has the same shape:

1. **What the trace shows** — specific span counts, nesting shapes, latency, input sizes
2. **What's missing from the trace** — expected spans that aren't there (no writes, no plan updates, no web_searches)
3. **The root cause** — usually in prompt framing, Guidelines rules, or subagent system prompt
4. **The fix** — change the harness-level input (prompts, continuation phrasing, task descriptions)

The failure is almost never in the mechanism itself. The mechanisms work — planning writes to plan.md when called, Ralph continues when plan has open items, subagents spawn when the parent decides to. The failure is in whether the model reaches for the right mechanism at the right time, and that's a prompt-level problem that trace-reading exposes.

This is why trace-reading is useful: it tells you where to look next, which is almost always upstream of the mechanism in a prompt or Guidelines rule.

---

## 🤔 Reflection prompts

Take 10-15 minutes to work through these. There are no universal right answers — the point is to commit to a position so your intuition sharpens.

**1. Pick a trace from your 9.2 session that went well. What signals told you it went well?**

Not just "the output was correct." Look at the trace structure. Nesting depth, span count distribution, latency balance, which tools fired how often. Describe what a healthy trace of *this specific task shape* looks like.

**2. Pick a trace that went poorly — either from your real sessions or from the reproduce-prompts above. Which signature did it match?**

Does your failure match one of the four exactly, or is it something else? If it's something else, try to characterize it: what spans are abnormal, what are missing, what's the root cause you'd guess?

**3. If you had to pick ONE signature to monitor for in production, which would it be and why?**

Different signatures imply different fixes. Which failure, left undetected, would cost the most? Which is easiest to fix once detected? These usually aren't the same signature.

**4. The four signatures all have fixes rooted in prompt framing, not harness code. Is that satisfying or frustrating?**

Prompts are soft and model-dependent; code is hard and deterministic. Fixing failures through prompts means the fix is less robust but requires less change. Think about which failures *should* get a code-level fix (new mechanism, tightening Guidelines rules) versus a prompt-level fix.

**5. You've now seen the Chapter 8 mechanisms (planning, Ralph, subagents) and their failure modes through traces. If you were starting over, would you change the mechanism design to make the failures less likely?**

Design question. Could you prevent ask-permission loops by making the system prompt reject deferential phrasing? Could you prevent under-decomposition with harder enforcement than Guidelines? What would each change cost?

**6. The Dashboard shows you traces individually. What questions could you only answer by looking at traces in aggregate?**

Think about things like *"how often does X happen,"* *"what percent of sessions have Y,"* *"is my model making this failure more over time."* These are questions LangSmith's filtering and aggregation can start to answer — but you need to know what you're looking for.

---

## 📌 What you should take away

**The four signatures are scaffolding, not a catalog.** They give you starting points for recognizing failures. The deeper skill — the one that matters past this workshop — is pattern-matching against what *normal* looks like. You develop that by inspecting many traces, including boring ones that went fine.

**Trace-reading is upstream of fixing.** When you find a failure signature in a trace, you've diagnosed *what* went wrong. The fix is usually in a prompt, Guidelines rule, or subagent task description — not in the mechanism itself. The trace tells you *where to look*; it doesn't tell you *what to change*.

**The dashboard isn't the only artifact.** LangSmith's trace view, your terminal scrollback, the files in `.harness/`, and `workspace/plan.md` all give different views of what happened. Combine them. The trace shows the API calls and tool dispatches; the files show the durable state the mechanisms produced; scrollback shows UX notices like `[Ralph continuation #N]`. All three together give you the full picture; any one alone is partial.

**Pattern matching requires volume.** You can't develop intuition for *what normal looks like* from four examples. Inspect every trace you produce for a while — not just the broken ones. The faster you can scan a trace and declare *"this looks like a normal Flask build"* vs. *"something's off here,"* the more valuable observability becomes.

**Honest limits of this lesson.** Four signatures leave gaps. Context pollution, over-decomposition, subagent context contamination, Ralph cap-hits, compaction-induced forgetting — all are real failures that produce distinctive trace shapes we didn't cover. You'll find them. When you do, apply the same four-step diagnosis: what the trace shows, what's missing, root cause, what addresses it. Document your findings. Over time, your personal signature library gets more useful than any lesson's version.

---

## 🔗 Where this leaves us

You now have:

- The tracing infrastructure (9.2)
- A diagnostic vocabulary for common failures (this lesson)
- The habit of pattern-matching against normal (reinforced by every trace you'll inspect going forward)

The last piece is **what to do with what you find**. Spotting a failure is step one. Changing the harness or prompts to address it is step two. Verifying that your change actually improved things — not just moved the problem — is step three.