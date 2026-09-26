"""Find corrupt package installation records in a virtual environment."""

from __future__ import annotations  # Keep annotations stable across supported Python versions.

import logging  # Record each health check step for operator diagnostics.
import sys  # Read the current Python version for a virtual environment path.
import sysconfig  # Locate the active environment site-packages directory.
from dataclasses import dataclass  # Store health check results in explicit records.
from pathlib import Path  # Keep path handling portable across Windows and Linux.

LOGGER = logging.getLogger(__name__)  # Use a module logger so callers control output and level.
DIST_INFO_SUFFIX = ".dist-info"  # Match package install records without reading package imports.
REQUIRED_RECORD_FILES = ("METADATA", "RECORD")  # Missing files make pip and imports misreport the package state.


@dataclass(frozen=True)
class PackageInstallProblem:
    """Store one package install record that lacks required metadata."""

    package: str  # Name the package so the repair command can reinstall it.
    path: Path  # Keep the bad record path for tests and future diagnostics.
    missing_files: tuple[str, ...]  # Name the absent files so a reader sees why the package is corrupt.


@dataclass(frozen=True)
class VirtualEnvironmentHealthReport:
    """Store the measured package install state of one site-packages directory."""

    checked_count: int  # Prove that the health check read install records.
    corrupt_packages: tuple[PackageInstallProblem, ...]  # Store corrupt records in a stable order.

    @property
    def corrupt_count(self) -> int:
        """Return the number of corrupt package install records."""
        return len(self.corrupt_packages)  # Count the immutable record so callers cannot drift from the data.

    def package_names(self) -> tuple[str, ...]:
        """Return the corrupt package names in report order."""
        return tuple(problem.package for problem in self.corrupt_packages)  # Build the repair list from findings.

    def messages(self) -> tuple[str, ...]:
        """Return the console lines that describe this health check."""
        if not self.corrupt_packages:  # A clean environment still must state the measured count.
            return (self._healthy_message(),)  # Report the read count so a silent pass cannot hide a skipped scan.
        package_list = ", ".join(self.package_names())  # Join package names once for the warning and repair line.
        return (self._corrupt_message(package_list), self._repair_message(package_list))  # Name the fault and repair.

    def _healthy_message(self) -> str:
        """Return the message for a healthy virtual environment."""
        return (
            "Virtual environment health check read "
            f"{self.checked_count} dist-info directories and found 0 corrupt package installs."
        )  # State both counts to prove the guard measured the environment.

    def _corrupt_message(self, package_list: str) -> str:
        """Return the message for corrupt package installation records."""
        return (
            f"Found {self.corrupt_count} corrupt package installs out of " f"{self.checked_count} read: {package_list}"
        )  # Match the repair report that issue #2887 requested.

    @staticmethod
    def _repair_message(package_list: str) -> str:
        """Return the pip command that repairs the corrupt package records."""
        return f"Repair: python -m pip install --no-cache-dir --force-reinstall {package_list}"  # Name the repair.


class VirtualEnvironmentHealthCheck:
    """Inspect package install records without importing the packages."""

    @staticmethod
    def inspect_active_environment() -> VirtualEnvironmentHealthReport:
        """Inspect the site-packages directory of the running interpreter."""
        site_packages = Path(sysconfig.get_paths()["purelib"])  # Ask Python for the active environment path.
        return VirtualEnvironmentHealthCheck.inspect_site_packages(site_packages)  # Reuse the tested scan path.

    @staticmethod
    def inspect_venv(venv_dir: Path) -> VirtualEnvironmentHealthReport:
        """Inspect the site-packages directory inside a virtual environment."""
        site_packages = VirtualEnvironmentHealthCheck.site_packages_for_venv(venv_dir)  # Build the venv package path.
        return VirtualEnvironmentHealthCheck.inspect_site_packages(site_packages)  # Scan the install records there.

    @staticmethod
    def site_packages_for_venv(venv_dir: Path) -> Path:
        """Return the site-packages path inside a virtual environment."""
        if sys.platform == "win32":  # Windows stores virtual environment packages under Lib.
            return venv_dir / "Lib" / "site-packages"  # Return the Windows package directory.
        python_dir = f"python{sys.version_info.major}.{sys.version_info.minor}"  # Match the interpreter version.
        return venv_dir / "lib" / python_dir / "site-packages"  # Return the POSIX package directory.

    @staticmethod
    def inspect_site_packages(site_packages: Path) -> VirtualEnvironmentHealthReport:
        """Inspect all dist-info directories under one site-packages directory."""
        LOGGER.info("Reading package install records under %s", site_packages)  # Log before the filesystem scan.
        if not site_packages.is_dir():  # A missing directory means the guard measured no install records.
            raise FileNotFoundError(f"The site-packages directory does not exist: {site_packages}")
        dist_infos = tuple(sorted(site_packages.glob(f"*{DIST_INFO_SUFFIX}")))  # Read the install record directories.
        LOGGER.debug("Read %d dist-info directorie(s)", len(dist_infos))  # Report the measured input size.
        problems = tuple(  # Store findings so the report can count and list them.
            problem for dist_info in dist_infos if (problem := VirtualEnvironmentHealthCheck._problem_for(dist_info))
        )
        LOGGER.debug("Found %d corrupt package install record(s)", len(problems))  # Report the measured output size.
        return VirtualEnvironmentHealthReport(len(dist_infos), problems)  # Return one immutable health report.

    @staticmethod
    def _problem_for(dist_info: Path) -> PackageInstallProblem | None:
        """Return a problem when a dist-info directory lacks required files."""
        missing_files = tuple(  # Build the missing file list from the required package install record files.
            name for name in REQUIRED_RECORD_FILES if not (dist_info / name).is_file()
        )
        if not missing_files:  # A complete record lets pip and imports report the package correctly.
            return None  # Keep healthy packages out of the corrupt package list.
        package = VirtualEnvironmentHealthCheck._package_name(dist_info)  # Name the package for the repair command.
        return PackageInstallProblem(package, dist_info, missing_files)  # Report the package as corrupt.

    @staticmethod
    def _package_name(dist_info: Path) -> str:
        """Return the package name from a dist-info directory name."""
        record_name = dist_info.name.removesuffix(DIST_INFO_SUFFIX)  # Remove only the exact install record suffix.
        package, separator, _version = record_name.rpartition("-")  # Split the package name from the version.
        return package if separator else record_name  # Fall back to the full name when no version delimiter exists.


def main() -> int:
    """Run the health check for the active interpreter."""
    logging.basicConfig(level=logging.INFO, format="%(message)s")  # Print one plain line for each result message.
    report = VirtualEnvironmentHealthCheck.inspect_active_environment()  # Inspect the environment that runs this file.
    for message in report.messages():  # Print each line that names the measured state and possible repair.
        if report.corrupt_count:  # Corrupt package records need warning severity.
            LOGGER.warning("%s", message)  # Print the corrupt package count or the repair command.
            continue  # Keep each corrupt-environment line at warning severity.
        LOGGER.info("%s", message)  # Print the healthy count as normal status output.
    return 1 if report.corrupt_count else 0  # Make the standalone command fail when corruption exists.


if __name__ == "__main__":  # Run the health check only when the module is executed as a program.
    raise SystemExit(main())  # Return the health status to the shell.
