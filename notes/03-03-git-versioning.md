- There are 3 major `git` capabilities that we want our agent to possess
    - 
    
    | Behavior | What git operation(s) | Tools we'll register |
    | --- | --- | --- |
    | Commit | Save current state as a discrete checkpoint | `git_status`, `git_diff`, `git_commit` |
    | Rollback | Return to a prior committed state | `git_log`, `git_checkout` |
    | Branching | Try something experimental without affecting main | `git_branch`, `git_checkout` |
    - This produces 6 tools in total
- Tools we are not adding
    - `git push`, `git pull`, `git fetch` : Not adding any remote operations for now
    - `git merge` : Difficult to handle through a tool interface
    - `git stash`, `git rebase` : Involves interacting with the user
    - These operations can be added later if a real need appears.