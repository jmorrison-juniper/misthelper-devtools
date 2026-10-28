# Research: Prompt Audit Call Resolution

## Scope and Evidence

The source is `src/misthelper_devtools/prompt_audit.py`. It currently indexes functions by qualified and plain names, traverses each callable with `ast.walk`, and uses a shared set of plain names to stop recursion.

The feature spec requires receiver-aware resolution, source-order reporting, repeated occurrences, and a four-edge limit. The repository instructions specify Python 3.13 or newer and pytest. No new dependency or external service is needed.

The Python 3.13 [`ast` documentation](https://docs.python.org/3.13/library/ast.html) describes AST nodes and visitor traversal. The Python 3.13 [expression reference](https://docs.python.org/3.13/reference/expressions.html#evaluation-order) documents evaluation order. The design uses these standard-library facilities and does not need a third-party parser.

## Decisions

### Decision: Keep `FunctionIndex` public dictionaries and lookup API unchanged

**Rationale**: Existing callers can rely on the current dictionary shapes and lookup behavior. New receiver information can remain private.

**Alternatives considered**: Replace name indexes with a new symbol table. Rejected because it expands scope and risks breaking the stated public API.

### Decision: Add private class identity metadata

**Rationale**: A method's owning `ClassDef` distinguishes same-named methods without selecting an arbitrary global match. A private map from function-node identity to its class node provides that context. A private class-name index can require a unique constructor match.

**Alternatives considered**: Resolve methods by plain name, or add class data to public dictionaries. The first can select the wrong class. The second changes the public interface.

### Decision: Support unique dotted classes and simple local receiver bindings

**Rationale**: A direct or dotted constructor, a unique trailing class name in a dotted class expression, a single local name, and a name-to-name alias cover the specified cases. Clearing unknown reassignments prevents stale class guesses. Requiring a unique class name prevents arbitrary selection.

**Alternatives considered**: Track arbitrary expressions, factories, assignments across every branch, and inferred inheritance. Rejected because they require a broader dataflow model and weaken the conservative boundary. Resolve a class by a non-unique suffix. Rejected because it can select the wrong class.

### Decision: Use a source-ordered visitor for call sites and arguments

**Rationale**: The current `ast.walk` order does not encode call evaluation order. Visit a call's function expression first. Visit positional and starred argument expressions in order, then keyword expressions in order, matching Python call evaluation. Visit them before handling the call. Walk resolved callees at the call site before continuing to later statements. This preserves nested call evaluation order and repeated occurrences.

**Alternatives considered**: Sort all calls only by line and column. Rejected because locations alone do not represent nested evaluation order. Visit the AST `args` list followed by the `keywords` list. Rejected because Python stores these argument groups separately, so that traversal can misorder mixed positional and keyword expressions. Keep `ast.walk`. Rejected because its traversal order does not provide the required sequence.

### Decision: Keep an active-path set of callable node identities

**Rationale**: The set stops a direct or mutual cycle while it is active. Removing nodes on return allows later distinct call sites to revisit the same callable.

**Alternatives considered**: Keep one global visited set. Rejected because it suppresses later call-site occurrences. Track plain function names. Rejected because names can collide across classes.

### Decision: Keep the existing four-edge boundary

**Rationale**: The spec requires `MAX_DEPTH` to stay four, with the starting handler at depth zero. A callable at depth four is analyzed. A target at depth five is not entered.

**Alternatives considered**: Count the starting handler as the first edge or stop before analyzing depth four. Rejected because either choice conflicts with the acceptance criteria.

## Remaining Unknowns

None block this design. The spec leaves dynamic dispatch, factory-result inference, and branch outcome prediction out of scope. The design leaves these cases unresolved rather than guessing.
