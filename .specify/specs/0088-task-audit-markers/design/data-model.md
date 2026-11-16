# Data Model: Task Audit Marker Classification

## TaskCounts

| Field | Type | Meaning |
| - | - | - |
| `checked` | `int` | Count of `- [x]` and `- [X]` task boxes. |
| `unchecked` | `int` | Count of `- [ ]` task boxes. |
| `malformed` | `int` | Count of real malformed markers. Equals the length of `malformed_lines`. |
| `malformed_lines` | `tuple[int, ...]` | One-based line numbers of malformed markers. The default is an empty tuple. |

## Classification of one bullet

| Bullet shape | Result |
| - | - |
| `- [ ] text` or `- [x] text` | Task box. |
| `- [x](url)`, `- [ref][id]`, `- [Label]: text` | Prose. |
| `- [P] text` with no task ID right after the label | Legend, prose. |
| `- [P] T001 text` | Malformed. |
| `- [] text`, `- [z] text`, `- [-] text` | Malformed. |
| `- [todo] T001 text` | Malformed. |
| `- [todo] text` | Prose. |
| `- [Docs] [T001]` | Prose. |

## Report line

`specs/<relative path>/tasks.md:<line>: malformed task marker`
