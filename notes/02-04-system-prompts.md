- **Where the System Prompt lives**
    - Mechanically, a system prompt is just another entry in the `messages` list, with `"role": "system"` instead of `"user"` or `"assistant"`.
    - It goes first, before any user message.
    - Because the harness resends the full `messages` list on every model call, the system message is present on every turn — the model sees it again and again, every time it's invoked.
    - This makes a useful instruction becomes a durable behavior pattern. A vague instruction becomes durable noise.
    - 
- **Four jobs of a System Prompt**
    - Here, we will focus all its purpose into 4 verticals
    
    | Job | What it does | Example |
    | --- | --- | --- |
    | **Identity** | Tells the model who it is and what context it's operating in | *"You are a coding assistant running in a terminal."* |
    | **Capabilities** | Names the tools, resources, and information available to the model | *"You have access to a `read_file` tool and a `bash` tool."* |
    | **Constraints** | States what the model should not do, or should be cautious about | *"Never run destructive commands without explicit confirmation."* |
    | **Output conventions** | Specifies how responses should be formatted | *"When returning code, use fenced code blocks with the language tag."* |
    - Not every harness needs all four jobs in its prompt.
    - A simple chatbot might only need identity. A coding agent needs all four.
    - The discipline is to ask, for every sentence in the prompt, *which job is this doing?* — and remove the sentences that don't answer that question.
    - We'll add more sophisticated context-engineering moves in Chapter 6 (`AGENTS.md`, retrieval), but the four jobs are the starting taxonomy.
    - 
- **Soft constraints vs Hard constraints**
    - Soft Constraints:
    - A **soft constraint** is enforced by what you say in the prompt.
    - *"Never delete files without confirmation"* is a soft constraint.
    - The model will usually obey it, but:
        - The user might use words that make it forget the constraint
        - The model might silently slip on a particular phrasing
        - A new model version might interpret it differently
        - Adversarial input could override it directly
    - 
    - Hard Constraints
    - A **hard constraint** is enforced by the harness's code.
    - If the harness doesn't expose a `delete_file` tool, the model literally cannot delete files — no amount of clever phrasing changes that.
    - The constraint is *structural*, not behavioral.
    - 
    - The rule of thumb: **soft constraints shape the agent's default behavior; hard constraints determine what the agent is even capable of doing.**
    - Most of this course's later work is about converting soft constraints into hard ones — replacing "the prompt asks the model nicely" with "the harness makes it impossible to do otherwise.”