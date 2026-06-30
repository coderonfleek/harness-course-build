- Lesson Aim: What we will be Verifying
    - **Saves intermediate work** — the agent uses `write` to persist notes, drafts, and other artifacts that survive between turns
    - **Updates its memory file** — the agent uses `write` on `AGENTS.md` to record what it learned about the project and what's still in progress
    - **Resumes correctly** — the second session, with nothing more than what's on disk, picks up the task where session one left off
    - 
- The Task we will be giving our harness
    - Task Description
        
        > "Help me write a one-page document about the history of git — its origins, the problem it was created to solve, and why it became dominant. Take research notes as you go in a `notes/` directory and produce the final summary as `git-history.md`."
        > 
    - A few things worth noticing about this task:
        - **It's open-ended.** The agent has to plan its own decomposition. No turn-by-turn instructions from the user.
        - **It has natural intermediate artifacts.** Research notes are a real reason to call `write` mid-task, separate from the final deliverable.
        - **It has a natural pause point.** After taking notes, the user can quit and resume later. That's where AGENTS.md earns its keep.
        - **It doesn't require code execution or web search.** Both arrive later in the course. For 3.5, the model uses its own training knowledge as the source. The point isn't testing what the model knows about git; the point is testing whether the harness machinery (filesystem, git, AGENTS.md) holds together under realistic work.
        - **It's self-referential.** The agent uses `git_commit` to commit progress on a task whose subject matter is git itself. That coincidence is harmless but pleasant.