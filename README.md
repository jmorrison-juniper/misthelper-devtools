# What

MistHelper development tooling holds Python command-line tools and shared GitHub
Actions for [MistHelper](https://github.com/jmorrison-juniper/MistHelper) and the
related Mist repositories. The tools check source code, tests, prose, diagrams,
and local links. They write reports for engineers.

Program interface screenshots: **N/A**. User screen screenshots: **N/A**.
This repository delivers developer CLI tooling, not a visual UI.
The [tooling guide](docs/tooling-guide.md#run-a-tool) shows the real command interface.

# How

Use Python 3.13 or newer. From the repository root, create an environment and
install the development tools:

```sh
python -m venv .venv
# macOS or Linux:
. .venv/bin/activate
# Windows PowerShell: .venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]"
ste-linter README.md
python -m pytest
```

See the [install guide](docs/tooling-guide.md#install), the
[commands](docs/tooling-guide.md#run-a-tool), and the
[quality gates](docs/tooling-guide.md#quality-gates).

# Where

The package is in `src/misthelper_devtools/`. The tests are in `tests/`.
Shared workflows are in `.github/workflows/`.
The canonical [agent instructions](docs/tooling-guide.md#agent-instructions)
are in `templates/agent-instructions/`.
Detailed guidance is in [docs/tooling-guide.md](docs/tooling-guide.md).
The [tooling inventory](documentation/mist-repository-tooling-inventory.md)
lists each tool and its consumers.

# When

Run the tools during development and before a pull request merge.
CI runs the quality gates on each pull request and each push to `main`.
Consumers must [pin a full commit SHA](docs/tooling-guide.md#use-a-tool-from-another-repository).
Read the [upgrade notes](docs/tooling-guide.md#upgrade-from-release-060-to-release-061)
before changing a pin.

# Why

Shared tools keep the quality gates consistent across Mist repositories.
Development tooling stays out of the shipped product.
This package must not import product code.
See the [dependency rules](docs/tooling-guide.md#relationship-to-the-mist-repositories).

# Who

Jeremy Morrison maintains this repository. Engineers who work on Mist
repositories use these tools. Customers do not need to run them.
Report defects through [GitHub issues](https://github.com/jmorrison-juniper/misthelper-devtools/issues).
Contributions must pass the [quality gates](docs/tooling-guide.md#quality-gates)
and use [Simplified Technical English](docs/tooling-guide.md#writing-style).
The license is [MIT](LICENSE).
