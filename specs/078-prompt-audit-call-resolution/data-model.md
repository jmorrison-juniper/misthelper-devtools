# Data Model: Prompt Audit Call Resolution

## Runtime Records

The audit stores no persistent records. It reads parsed Python ASTs and builds in-memory indexes.

| Record | Fields | Meaning |
|---|---|---|
| Function index | `by_qualname`, `by_name` | Existing public function dictionaries. Keep their shapes and lookup API unchanged. |
| Class ownership metadata | `dict[int, ast.ClassDef]` | Private context used to resolve `self` and `cls`. The key is the method node identity. |
| Class candidates | `dict[str, list[ast.ClassDef]]` | Private index. Use a constructor name only when it maps to one class definition. |
| Local receiver map | `dict[str, ast.ClassDef]` | Per-callable evidence from a direct construction or simple local alias. |
| Active call path | `set[int]` | Recursion guard for the current descent only. The set holds callable node identities. Remove each identity after returning. |
| Prompt result | ordered list of prompt-kind strings | Existing report value. Preserve each recognized call occurrence in traversal order. |

## Resolution Rules

1. A direct `ClassName()` or dotted `module.ClassName()` construction establishes a receiver class only when the trailing class name maps to one indexed definition.
2. A simple assignment such as `receiver = ClassName()` or `alias = receiver` records the class identity.
3. An unsupported or unknown reassignment removes the receiver name from the local map.
4. Resolve `self.method()` and `cls.method()` against the exact containing class. Resolve inline and local receivers only when the source index proves one class. Resolve `Class.method()` and dotted class expressions such as `mh.Class.method()` only when the trailing class name is unique.
5. Do not infer a target from an unknown receiver, factory result, dynamic dispatch, or ambiguous class name.
6. Visit a call's function expression first. Visit positional and starred argument expressions in order, then keyword expressions in order, matching Python call evaluation. Visit them before handling the enclosing call. Descend into a resolved callable before visiting the next call site.
7. Treat the requested handler as depth zero. Analyze targets through `MAX_DEPTH == 4`, but do not enter depth five.
8. A callable already on the active path is not entered again. A later call site may enter it after the earlier descent returns.

## State and Validation

The local receiver map belongs to one callable visit. A call to a target receives a fresh local map for that target. Branch bodies receive copies of their incoming map, and statements within one suite run in order. Branch-specific assignments do not flow back to the enclosing scope. Invalidate receiver names written under compound control flow after that statement. Do not visit nested function bodies unless a call reaches them.

The prompt list keeps duplicates. It does not record runtime branch choices. These rules preserve static, conservative reporting.
