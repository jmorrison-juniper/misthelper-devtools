"""Rebuild data/compliance_backlog.tsv from the current full-repo compliance report."""

from __future__ import annotations

import logging
from pathlib import Path

logger = logging.getLogger(__name__)

REPORT = Path("data/full_repo_compliance_current.md")
OUT = Path("data/compliance_backlog.tsv")


def main() -> None:
    rows: list[tuple[int, float, int, int, int, int, str, str]] = []
    prefix = "| src\\"
    for line in REPORT.read_text(encoding="utf-8").splitlines():
        if not line.startswith(prefix):
            continue
        parts = [p.strip() for p in line.strip().strip("|").split("|")]
        if len(parts) != 8:
            continue
        path, score_s, grade, crit, high, med, low, total = parts
        score = float(score_s)
        if grade in ("A+", "A"):
            continue
        rows.append((int(total), score, int(crit), int(high), int(med), int(low), grade, path))
    rows.sort(key=lambda r: (-r[0], r[1]))
    with OUT.open("w", encoding="utf-8", newline="\n") as fh:
        fh.write("rank\ttotal\tcritical\thigh\tmedium\tlow\tscore\tgrade\tpath\n")
        for idx, (
            total_count,
            row_score,
            crit_count,
            high_count,
            med_count,
            low_count,
            row_grade,
            row_path,
        ) in enumerate(
            rows,
            1,
        ):
            fh.write(
                f"{idx}\t{total_count}\t{crit_count}\t{high_count}\t{med_count}\t{low_count}\t"
                f"{row_score}\t{row_grade}\t{row_path}\n"
            )
    logger.info("wrote %s sub-A rows -> %s", len(rows), OUT)


if __name__ == "__main__":
    main()
