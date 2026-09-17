"""Test the virtual environment package install health check."""

from __future__ import annotations  # Keep annotations stable across supported Python versions.

from pathlib import Path  # Build fake site-packages directories without string path separators.

from tools.venv_health import VirtualEnvironmentHealthCheck  # Exercise the same module that bootstrap imports.


def test_inspect_site_packages_reports_one_corrupt_package(tmp_path: Path) -> None:
    """Verify that the health check counts clean and corrupt install records."""
    site_packages = tmp_path / "site-packages"  # Build a fake package root isolated to this test.
    site_packages.mkdir()  # Create the root so the health check can scan it.
    healthy_record = site_packages / "healthy-1.0.dist-info"  # Name a complete install record.
    corrupt_record = site_packages / "broken-2.0.dist-info"  # Name an incomplete install record.
    healthy_record.mkdir()  # Create the healthy record directory.
    corrupt_record.mkdir()  # Create the corrupt record directory.
    (healthy_record / "METADATA").write_text("Name: healthy\n", encoding="utf-8")  # Add required package metadata.
    (healthy_record / "RECORD").write_text("", encoding="utf-8")  # Add the required installed-file manifest.
    (corrupt_record / "METADATA").write_text("Name: broken\n", encoding="utf-8")  # Leave RECORD absent on purpose.

    report = VirtualEnvironmentHealthCheck.inspect_site_packages(site_packages)  # Scan the fake package records.

    assert report.checked_count == 2  # Prove the guard measured every dist-info directory.
    assert report.corrupt_count == 1  # Prove the guard detects the corrupt install record.
    assert report.package_names() == ("broken",)  # Prove the repair command names only the corrupt package.
    assert report.corrupt_packages[0].missing_files == ("RECORD",)  # Prove the finding names the missing file.
