## 🎯 What you'll learn

By the end of this workshop, you will:

- Have **observed both mechanisms firing** in a real long-horizon session — not from a demo transcript, from your own agent
- Have **direct comparison data** between running with compaction+offloading enabled vs. effectively disabled
- Understand where the mechanisms genuinely help and where they don't
- Understand the failure modes of each — what breaks when you push the mechanisms outside their comfort zone

By the time you finish, you'll have a much sharper sense of *when* to reach for each mechanism in your own harness work, and *why*.

---

## 🧪 The Setup

You're going to run the same task twice:

- **Run A (baseline)** — mechanisms effectively disabled via config
- **Run B (with mechanisms)** — default configuration

Both runs use the same task, the same prompts, the same starting state. What changes between them is *only* the config that governs when compaction and offloading fire.

You'll observe context growth, task completion quality, and cost across both runs. Then you'll compare.

### The task

**Draft a technical evaluation of adopting Kubernetes for a small SaaS company's infrastructure. Cover:**

1. **Team readiness** — what a team needs to know/have to run Kubernetes safely
2. **Migration complexity** — the actual work of moving from a Docker Compose setup
3. **Cost implications** — the financial reality of running Kubernetes at small scale
4. **Operational overhead** — the ongoing work Kubernetes requires
5. **Alternatives worth considering** — options besides Kubernetes for the same problems

Each section should be grounded in current 2026 best practices and include specific, actionable recommendations.

This task is intentionally long-horizon. Done properly, it takes 15-20 agent turns, involves ~10-15 `web_search` calls, and produces a document that requires synthesizing across everything researched. That's the shape that makes context management mechanisms observably matter.

---

## 🅰️ Run A — Baseline (mechanisms effectively disabled)

**Step 1. Update `harness/config.py` for Run A:**

```python
# For Run A: effectively disable both mechanisms
COMPACTION_THRESHOLD: int = 10_000_000       # so high it never fires
OFFLOAD_THRESHOLD_TOKENS: int = 1_000_000    # so high nothing crosses it
```

Restart the agent (Ctrl+C, then re-launch) so the new config takes effect.

**Step 2. Start a fresh session.** Clean workspace preferred:

```bash
rm -rf workspace/.tool_outputs
python -m harness.agent
```

**Step 3. Run the task.** Copy-paste the following prompt sequence into the agent, one prompt per turn, waiting for each response to complete before sending the next:

```
1. Research team readiness for adopting Kubernetes at a small SaaS
   company. What does a team need to know or have in place before
   Kubernetes is a safe choice?

2. Research migration complexity: what does moving from Docker Compose
   to Kubernetes actually involve in 2026?

3. Research the cost implications. Include managed vs. self-hosted
   options, and be specific about small-scale (say, 5-20 services).

4. Research operational overhead. What's the ongoing work Kubernetes
   requires that Docker Compose doesn't?

5. Research alternatives. What are teams choosing instead of Kubernetes
   in 2026 for similar use cases, and why?

6. Now synthesize everything into a technical evaluation document.
   Cover all five sections in order (team readiness, migration
   complexity, cost, operational overhead, alternatives), with specific
   recommendations grounded in what you researched. This is the
   deliverable — make it complete and coherent.
```

**Step 4. Observe as it runs.** For each turn, note:

- **Context size** shown in the `[Context: X / M tokens]` line before each prompt
- **Whether any `[Offloaded ...]` or `[Context compacted ...]` notices appear** (they shouldn't — mechanisms are disabled)
- **Response quality** — is the agent giving substantive research, or thin summaries?

**Step 5. Save the final synthesis** somewhere you can reference later — a text file, a note-taking app, whatever works for you. You'll compare it against Run B's version afterward, so keep it accessible.

**Step 6. Record your observations** in a scratch note:

- Final context size (from the last `[Context: ...]` line)
- Approximate total tokens used (multiply context size × 6 turns as a rough estimate — real cost is higher due to retries, but this gives a floor)
- Did the agent complete all 5 sections in the final document?
- Did later sections show signs of forgetting earlier research (contradictions, missing detail, generic advice replacing specific findings)?
- Did any turn take unusually long or produce weird outputs?

Set the file aside. Time for Run B.

---

## 🅱️ Run B — With mechanisms (default config)

**Step 1. Update `harness/config.py` back to defaults:**

```python
# For Run B: default settings, mechanisms active
COMPACTION_THRESHOLD: int = 60_000
OFFLOAD_THRESHOLD_TOKENS: int = 1_000
```

Restart the agent.

**Step 2. Start a fresh session with a clean workspace:**

```bash
rm -rf workspace/.tool_outputs
python -m harness.agent
```

**Step 3. Run the exact same task.** Use the same six prompts from Run A, in the same order, one per turn. Do not vary the wording — the goal is a controlled comparison.

**Step 4. Observe as it runs.** Same as Run A, but this time you should see:

- `[Offloaded N tokens from web_search output → .tool_outputs/...]` notices after most searches
- Possibly `[Context compacted at X tokens → ~Y tokens]` notices if the session crosses 60k (may or may not, depending on how much your specific run accumulates)
- Context size growing more slowly than in Run A

**Step 5. Save the final synthesis** the same way you did for Run A. You'll compare the two shortly.

**Step 6. Record your observations** — same list as Run A.

---

## 📊 Compare the two runs

You now have two evaluation documents plus two sets of observations. Compare them along four dimensions.

### 1. Context growth

Look at the `[Context: X / M tokens]` display across your notes. Chart the growth:

| Turn | Run A context | Run B context |
| --- | --- | --- |
| After turn 1 |  |  |
| After turn 2 |  |  |
| After turn 3 |  |  |
| After turn 4 |  |  |
| After turn 5 |  |  |
| After turn 6 |  |  |

Run A's numbers should climb steadily (each turn's tool outputs pile up in full). Run B's should climb more slowly (offloading takes each `web_search` output down from ~1-3k to a few hundred tokens in context). If Run B crossed 60k at some point, you'll see a drop where compaction fired.

**What to notice:** the *rate* of growth. Run A is roughly linear in tool calls; Run B is much flatter. Over a longer session, Run A would hit context limits; Run B has headroom.

### 2. Task completion — did the mechanisms hurt the result?

Open both saved evaluations side by side. For each of the five sections:

- **Team readiness** — both cover it? Same level of specificity?
- **Migration complexity** — both cover it? Any contradictions from earlier research?
- **Cost** — both give specific numbers? Same numbers?
- **Operational overhead** — both mention the same categories of work?
- **Alternatives** — both name the same alternatives, with similar reasoning?

**Two possible outcomes:**

- **Run B is comparable to Run A.** The mechanisms shrunk context substantially without hurting the final output. This is the win — you paid nothing for the memory savings.
- **Run B is measurably worse than Run A.** Specific facts got lost in compaction, or the synthesis is thinner because offloaded content wasn't retrieved when needed. This tells you the mechanisms have real costs.

Both outcomes are informative. Which did you see?

### 3. Cost — rough token accounting

For each run, sum the `[Context: X]` readings across all turns. That's roughly the prompt tokens per turn (approximate — doesn't account for output tokens, tool result additions mid-turn, or ReAct loop iterations). Total: prompt tokens across turns × cost per 1M input tokens (gpt-4o-mini: ~$0.15/1M).

Not exact, but gives you the shape. Run A's total should be visibly higher than Run B's, because every turn's context in Run A is fatter than the corresponding Run B turn.

**What to notice:** the ratio. If Run B is 2-3x cheaper for equivalent output quality, that's real money on production workloads.

### 4. Failure modes — what broke in either run?

- **In Run A**, did the agent contradict itself between sections? Give thinner detail in later sections than earlier ones? Struggle to remember what it had already researched?
- **In Run B**, did the agent try to retrieve an offloaded file and fail? Answer from head/tail with plausible-but-wrong details? Route to `recall()` when it should have called `read()`?

Both mechanisms have failure modes. Seeing them in your own runs is more valuable than reading about them.

---

## 🤔 Reflection prompts

Take 10 minutes to think through these. Answers vary run-to-run, and there aren't universal "right" answers — the point is to develop your own judgment.

**1. Given what you observed, when would you enable each mechanism?**

Under what conditions is compaction worth the cost of the summarization call, and when is it not? Under what conditions is offloading worth the retrieval friction, and when is it not? Your answers here will depend on how the mechanisms behaved in your specific runs.

**2. If you had to pick one mechanism for a session budget-constrained to 30k tokens, which would you keep — compaction or offloading?**

There isn't a universal answer. Argue it either way based on your data.

**3. Offloading has a subtle failure mode you may have seen in Run B: the agent produces confident-sounding output built on head/tail snippets rather than retrieved content.**

If you saw this, what would you change about the mechanism to reduce it? (Options: lower threshold, prompt-tuning, force retrieval on certain tools, something else.) If you didn't see it, would you *expect* to see it on a task that was more source-sensitive than technical research (e.g., legal citations, medical references)?

**4. The `[Context: X / M tokens]` display was added in 7.2 partly for debuggability.**

What other metrics would you want visible during a session to make context management decisions in real time?

**5. Both mechanisms are configurable via thresholds in `config.py`.**

What's the shape of a workload where you'd want dynamic thresholds — where the harness itself adjusts them based on session state — rather than fixed values?

**6. Neither mechanism addresses the size of the tool schemas themselves.**

You may have ~11 tool definitions loaded in every turn's context. On a very long session, is that meaningful? On a short session? What would you want to do about it?

Write down your answers — even briefly. The act of committing to a position sharpens the thinking. If you're stuck on any question, the mechanism itself is teaching you something — noticing what's hard to answer clearly is part of the point.

---

## 📌 What you should take away

**The mechanisms have real costs.** Compaction burns a model call each time it fires. Offloading introduces retrieval friction that isn't always paid back in accuracy. Both are worth their cost on genuinely long sessions; both are pure overhead on short ones. Turning them on always is not the answer.

**The mechanisms compose but don't overlap.** Compaction shrinks accumulated history. Offloading shrinks individual tool outputs. Neither replaces the other. A session that hits both compaction and offloading has both mechanisms firing at different points against different problems.

**Failure modes are visible if you look for them.** Run A's failure (context bloat, degradation across turns) is legible in real time — the `[Context: X]` numbers climb, the responses get thinner. Run B's failure (misattribution from head/tail) is more insidious — the output looks correct. That asymmetry matters: mechanism failures that produce silent-wrong outputs are more dangerous than failures that produce loud-broken ones.

**Threshold tuning is the main knob.** Both mechanisms are governed by threshold values in `config.py`. The right values depend on workload. Production systems tune per-model-context-window (e.g., compact at 60% of window) and per-tool (e.g., offload web_search at 500 tokens but bash at 2000). The teaching harness uses fixed values for simplicity.

**Context management is a *design problem*, not a solved one.** Every harness makes tradeoffs. Compaction summaries lose fidelity. Offloading loses immediacy. Skills-style progressive disclosure (which we didn't build) reduces attention dilution but adds workflow friction. Choosing which failures to accept is the design work; the mechanisms are just how you enact the choices.

---

## 🎯 Restoring your setup

Make sure your `config.py` is back to defaults before moving on:

```python
COMPACTION_THRESHOLD: int = 60_000
OFFLOAD_THRESHOLD_TOKENS: int = 1_000
```

Clean up the offload directory if it accumulated a lot during Run B:

```bash
rm -rf workspace/.tool_outputs
```

You keep the `.harness/context_log.jsonl` — it's your audit trail across compactions.

---