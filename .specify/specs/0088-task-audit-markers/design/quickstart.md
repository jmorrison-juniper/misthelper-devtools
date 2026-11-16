# Quickstart and Review Evidence

## Run the focused tests

```sh
python -m pytest tests/unit/tools/test_speckit_task_audit.py -q
```

## Run the tool

```sh
speckit-task-audit --root path/to/repository
```

The report lists each real malformed marker as `specs/<path>/tasks.md:<line>: malformed task marker`.

## Review evidence

The coordinator review recorded this result. The counterfactual ran in memory. No file changed.

- Against exact base `0fdcf610`, six of the seven selected cases failed: the six new regression tests. One existing control, `test_malformed_boxes_do_not_count_as_tasks`, passed.
- Against the reviewed source, all seven selected cases passed.

The source candidate predates these records. The review evidence comes from the coordinator. The author of these records did not rerun the counterfactual.
