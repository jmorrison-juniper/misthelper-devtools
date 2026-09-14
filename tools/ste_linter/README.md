# STE Writing Linter

The STE linter grades a Markdown or Python file against the Simplified Technical
English rules in `documentation/ASD-STE100_writing-guide.md`. It prints a score
from 0 to 100 and a list of violations. Each violation shows the line, the rule,
the problem, and a suggested fix.

## Run the linter

Grade one file:

```powershell
python -m tools.ste_linter documentation/ASD-STE100_writing-guide.md
```

Grade several files and set a pass threshold:

```powershell
python -m tools.ste_linter --min-score 80 README.md CHANGELOG.md
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
python -m tools.ste_linter --grade-logging-strings --grade-user-facing-strings src/example.py
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
python -m tools.ste_linter.dictionary.extract path/to/ASD-STE100.pdf
```

The tool writes `data/ste_dictionary.json`, which git ignores. Without the file,
the linter runs the structural checks only and says the dictionary checks were
skipped.

## More information

See `specs/1026-ste-linter/quickstart.md` for the full guide and
`specs/1026-ste-linter/spec.md` for the specification.
