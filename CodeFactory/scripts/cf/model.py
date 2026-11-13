"""The vocabulary of the factory: states, transitions, columns, priorities, risks."""

PRIORITIES = ["P0", "P1", "P2", "P3"]
RISKS = ["LOW", "MEDIUM", "HIGH"]
MODES = {"single", "subtasks", "split"}
REVIEW_VERDICTS = {"PASS", "CHANGES", "BLOCK"}

ACTIVE = {"INTAKE", "SPEC", "BUILD", "REVIEW"}  # these take a parallel-job slot

COLUMNS = ["QUEUED", "INTAKE", "SPEC", "BUILD", "REVIEW", "SPLIT_REVIEW", "WAITING_CHILDREN",
           "NEEDS_INFO", "NEEDS_HUMAN", "MERGE_PENDING", "COMPLETE", "REJECTED", "FAILED"]

TRANSITIONS = {
    "QUEUED": {"INTAKE"},
    "INTAKE": {"SPEC", "NEEDS_INFO", "REJECTED", "NEEDS_HUMAN", "FAILED"},
    "NEEDS_INFO": {"INTAKE", "REJECTED"},
    "SPEC": {"BUILD", "NEEDS_HUMAN", "FAILED"},
    "BUILD": {"REVIEW", "SPLIT_REVIEW", "NEEDS_HUMAN", "FAILED"},
    "REVIEW": {"BUILD", "MERGE_PENDING", "NEEDS_HUMAN", "FAILED"},
    "SPLIT_REVIEW": {"WAITING_CHILDREN", "BUILD", "NEEDS_HUMAN", "REJECTED"},
    "WAITING_CHILDREN": {"COMPLETE", "NEEDS_HUMAN"},
    "MERGE_PENDING": {"COMPLETE", "NEEDS_HUMAN"},
    "NEEDS_HUMAN": {"BUILD", "SPEC", "INTAKE", "REVIEW", "MERGE_PENDING", "COMPLETE", "REJECTED"},
    "FAILED": {"QUEUED", "INTAKE", "SPEC", "BUILD"},
    "REJECTED": set(),
    "COMPLETE": set(),
}

PARKABLE = {"NEEDS_INFO", "NEEDS_HUMAN", "SPLIT_REVIEW", "MERGE_PENDING"}  # reject allowed from these
