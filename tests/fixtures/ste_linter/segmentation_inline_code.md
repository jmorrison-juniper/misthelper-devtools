# Segmentation regression fixture

The fixture holds the shapes from issue 52. Each sentence is short, and the linter must count each one alone.

Read the file (the config). `black` formats the code. The gate then stops.

Run the tests first. `ruff` checks the code.

The gates are:

- Use the tool (see the guide)
- `pytest` runs the tests
- The gate stops on a failure
- Use the lint tool on the source
  and read the `config` file.
