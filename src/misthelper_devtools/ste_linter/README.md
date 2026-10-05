# STE Writing Linter

The STE linter grades a Markdown or Python file against the Simplified Technical
English rules in `documentation/ASD-STE100_writing-guide.md`. It prints a score
from 0 to 100 and a list of violations. Each violation shows the line, the rule,
the problem, and a suggested fix.

## Run the linter

Grade one file:

```powershell
python -m misthelper_devtools.ste_linter documentation/ASD-STE100_writing-guide.md
```

Grade several files and set a pass threshold:

```powershell
python -m misthelper_devtools.ste_linter --min-score 80 README.md CHANGELOG.md
```

The exit code is zero when every file meets the threshold. The exit code is one
when a file scores below it. The exit code is two on a usage error.

## Options

| Option | Description |
| - | - |
| `--format text` or `--format json` | Choose the report format. |
| `--min-score N` | Set the pass threshold from 0 to 100. |
| `--dictionary PATH` | Set the dictionary file path. |
| `--select ID` | Run only these rules. |
| `--ignore ID` | Do not run these rules. |
| `--grade-logging-strings` | Grade configured Python logging message strings. |
| `--grade-user-facing-strings` | Grade configured Python print and prompt strings. |
| `--quiet` | Print only the score line. |

## Python string grading

By default, Python parsing grades docstrings and comments only. Enable the new
string surfaces when you are ready to repair operator-facing text:

```powershell
python -m misthelper_devtools.ste_linter --grade-logging-strings --grade-user-facing-strings src/example.py
```

Use `[tool.ste_linter]` to enable the surfaces for all runs:

```toml
grade_logging_strings = true
grade_user_facing_strings = true
logging_call_names = ["logging.info", "logging.warning"]
user_facing_call_names = ["print", "safe_input"]
```

## Backends

The linter runs with the standard library. To improve the grammar checks, install
spaCy and its small English model. The linter uses spaCy when it is present.

```powershell
uv pip install ".[ste-linter]"
python -m spacy download en_core_web_sm
```

## Dictionary (optional)

The controlled-vocabulary checks need a dictionary file. The ASD-STE100 dictionary
is copyrighted, so the repository does not ship it. Build it from your licensed
PDF:

```powershell
python -m misthelper_devtools.ste_linter.dictionary.extract path/to/ASD-STE100.pdf
```

The tool writes `data/ste_dictionary.json`, which git ignores. Without the file,
the linter searches for a user-level copy. Each lookup tests for a file in this
order:

1. `--dictionary PATH`: use this path only, with no fallback.
2. The `STE_DICTIONARY_PATH` environment variable.
3. The `dictionary` key in `[tool.ste_linter]`.
4. `data/ste_dictionary.json` in the current directory.
5. `%LOCALAPPDATA%/ste-linter/ste_dictionary.json`, when `LOCALAPPDATA` is set.
6. `~/.local/share/ste-linter/ste_dictionary.json`.
7. `~/.ste-linter/ste_dictionary.json`.

Steps 2 through 7 skip missing files and try the next path. Keep one licensed
copy in a user-level location so that each worktree can reach it.
If no file exists, the linter runs the structural checks only and reports
`dictionary: skipped`. A found dictionary enables `STE-S1-WORD` and `STE-S1-POS`
and reports `dictionary: used`. An invalid `--dictionary` path reports the skip;
it never falls back to another file.

## More information

The MistHelper repository holds the full guide and the specification of the
linter, in `specs/1026-ste-linter/quickstart.md` and
`specs/1026-ste-linter/spec.md`.

## Continuous integration

A Mist repository calls `.github/workflows/reusable-ste-lint.yml` of this
repository to grade its documentation on each pull request. The header of that
workflow holds a caller example.
