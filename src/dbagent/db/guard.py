"""SQL safety guard: only a single read-only SELECT ever reaches the database.

Strategy (defense in depth — the connection is also opened read-only):
1. Parse with sqlglot (sqlite dialect). Unparseable or empty SQL is rejected.
2. Exactly one statement.
3. The root must be a plain query (SELECT or UNION/EXCEPT/INTERSECT of them).
4. Walk the whole tree and reject any write/DDL/meta node wherever it hides
   (catches `WITH x AS (...) DELETE ...`, subquery smuggling, PRAGMA, ATTACH).
5. Reject dangerous functions (load_extension, ...).
6. Inject a LIMIT if missing; clamp an existing literal LIMIT to the cap.

Returns normalized SQL to execute. Raises GuardError with a machine-readable
`error_type` so the agent loop can hand the model a useful hint.
"""

from __future__ import annotations

import sqlglot
from sqlglot import exp

# Node types that must never appear anywhere in the tree.
FORBIDDEN_NODES: tuple[type[exp.Expression], ...] = (
    exp.Insert,
    exp.Update,
    exp.Delete,
    exp.Merge,
    exp.Drop,
    exp.Create,
    exp.Alter,
    exp.TruncateTable,
    exp.Pragma,
    exp.Attach,
    exp.Detach,
    exp.Into,
    exp.Transaction,
    exp.Commit,
    exp.Rollback,
    exp.Grant,
    exp.Command,  # anything sqlglot couldn't classify (VACUUM, REINDEX, ...)
)

# SQLite functions that can touch the filesystem or load code.
FORBIDDEN_FUNCTIONS = frozenset(
    {"load_extension", "readfile", "writefile", "edit", "fts3_tokenizer"}
)


class GuardError(ValueError):
    """SQL rejected by the guard. `error_type` is machine-readable for hints."""

    def __init__(self, error_type: str, message: str) -> None:
        super().__init__(message)
        self.error_type = error_type
        self.message = message


def guard_sql(sql: str, *, row_limit: int = 200) -> str:
    """Validate `sql` and return the normalized, LIMIT-capped statement to run."""
    if not sql or not sql.strip():
        raise GuardError("empty", "No SQL was provided.")

    try:
        statements = sqlglot.parse(sql, read="sqlite")
    except sqlglot.errors.ParseError as error:
        raise GuardError("parse_error", f"SQL could not be parsed: {error}") from error

    statements = [s for s in statements if s is not None]
    if not statements:
        raise GuardError("empty", "No SQL statement found.")
    if len(statements) > 1:
        raise GuardError(
            "multiple_statements",
            f"Got {len(statements)} statements; send exactly one SELECT.",
        )

    root = statements[0]

    if not isinstance(root, exp.Select | exp.SetOperation):
        raise GuardError(
            "not_read_only",
            f"Only SELECT queries are allowed, got: {type(root).__name__.upper()}.",
        )

    for node in root.walk():
        if isinstance(node, FORBIDDEN_NODES):
            raise GuardError(
                "not_read_only",
                f"Forbidden operation in query: {type(node).__name__.upper()}.",
            )
        if isinstance(node, exp.Func):
            name = (node.sql_name() or node.name or "").lower()
            if name in FORBIDDEN_FUNCTIONS:
                raise GuardError("forbidden_function", f"Function '{name}' is not allowed.")
        if isinstance(node, exp.Anonymous):
            name = (node.this or "").lower() if isinstance(node.this, str) else ""
            if name in FORBIDDEN_FUNCTIONS:
                raise GuardError("forbidden_function", f"Function '{name}' is not allowed.")

    _apply_row_limit(root, row_limit)
    return root.sql(dialect="sqlite")


def _apply_row_limit(root: exp.Expression, row_limit: int) -> None:
    """Inject LIMIT if absent; clamp an existing literal LIMIT to `row_limit`."""
    limit_node = root.args.get("limit")
    if limit_node is not None and isinstance(limit_node, exp.Limit):
        literal = limit_node.expression
        if isinstance(literal, exp.Literal) and literal.is_int and int(literal.name) <= row_limit:
            return  # existing limit is fine
    # Missing, non-literal, or over the cap: enforce ours.
    assert isinstance(root, exp.Select | exp.SetOperation)
    root.limit(row_limit, copy=False)
