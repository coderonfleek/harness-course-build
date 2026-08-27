"""Memory subpackage. Re-exports the public memory API so the rest of the
harness can import without knowing the internal module layout.

Future memory modules (retrieval, search, etc.) get added to this file
as re-exports too.
"""

from harness.memory.agents_md import (
    load_agents_md,
    save_agents_md,
    validate_agents_md_structure,
    remember,  # imported for side effect — @tool decorator registers it
)