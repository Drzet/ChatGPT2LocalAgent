#!/usr/bin/env python3
"""Pure Python Standard Library Test Runner and Code Coverage Analyzer.

Discovers and executes the entire test suite under `tests/`, tracks line-level execution
across all modules in `bridge/`, formats a coverage table, and enforces a >90% coverage gate.
Requires zero external dependencies.
"""

from __future__ import annotations

import argparse
import io
import os
import sys
import threading
import time
import trace
import unittest
from pathlib import Path
from typing import Any, Dict, List, Set, Tuple

# Project root directory
PROJECT_ROOT = Path(__file__).resolve().parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


def format_line_ranges(lines: Set[int]) -> str:
    """Format a set of line numbers into compact comma-separated ranges (e.g. '12-15, 20, 33-35')."""
    if not lines:
        return ""
    sorted_lines = sorted(lines)
    ranges: List[str] = []
    start = sorted_lines[0]
    end = start

    for line in sorted_lines[1:]:
        if line == end + 1:
            end = line
        else:
            if start == end:
                ranges.append(str(start))
            else:
                ranges.append(f"{start}-{end}")
            start = line
            end = line

    if start == end:
        ranges.append(str(start))
    else:
        ranges.append(f"{start}-{end}")

    return ", ".join(ranges)


def get_executable_lines(file_path: Path) -> Set[int]:
    """Inspect Python bytecode to identify executable statement line numbers."""
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            src = f.read()
        code_obj = compile(src, str(file_path), "exec")
    except Exception:
        return set()

    exec_lines: Set[int] = set()

    def scan_code(co):
        for start, end, line in co.co_lines():
            if line is not None and line > 0:
                exec_lines.add(line)
        for const in co.co_consts:
            if hasattr(const, "co_lines"):
                scan_code(const)

    scan_code(code_obj)
    return exec_lines


def run_tests_with_coverage(
    test_dir: str = "tests",
    target_dir: str = "bridge",
    min_coverage: float = 90.0,
    verbosity: int = 2,
) -> int:
    """Run tests with line-level trace coverage across all threads and enforce threshold."""
    target_path = (PROJECT_ROOT / target_dir).resolve()
    test_path = (PROJECT_ROOT / test_dir).resolve()

    print("=" * 80)
    print(" ChatGPT Web to Local Agent Research — Automated Test & Coverage Runner")
    print(f" Python: {sys.version.split()[0]} | Platform: {sys.platform}")
    print(f" Target Package: {target_path} (Threshold: >={min_coverage:.1f}%)")
    print("=" * 80)

    # Initialize tracer
    tracer = trace.Trace(count=1, trace=0, countfuncs=0, countcallers=0)
    threading.settrace(tracer.globaltrace)

    def execute_suite():
        # Ensure fresh module load under tracer so top-level declarations are traced
        for mod_name in list(sys.modules.keys()):
            if mod_name == "bridge" or mod_name.startswith("bridge.") or mod_name.startswith("tests."):
                del sys.modules[mod_name]

        loader = unittest.TestLoader()
        suite = loader.discover(
            start_dir=str(test_path),
            pattern="test*.py",
            top_level_dir=str(PROJECT_ROOT),
        )
        runner = unittest.TextTestRunner(verbosity=verbosity)
        return runner.run(suite)

    start_time = time.time()
    test_result = tracer.runfunc(execute_suite)
    duration = time.time() - start_time

    # Collect trace counts
    counts = tracer.results().counts

    # Collect module files in target_dir
    target_files = sorted(target_path.glob("*.py"))

    total_statements = 0
    total_covered = 0
    total_missed = 0

    table_rows: List[Dict[str, Any]] = []

    for file_p in target_files:
        canonical_p = file_p.resolve()
        exec_lines = get_executable_lines(canonical_p)
        if not exec_lines:
            continue

        covered_lines: Set[int] = set()
        for (fn, lineno), cnt in counts.items():
            try:
                if Path(fn).resolve() == canonical_p and cnt > 0 and lineno in exec_lines:
                    covered_lines.add(lineno)
            except Exception:
                continue

        missed_lines = exec_lines - covered_lines
        n_exec = len(exec_lines)
        n_cov = len(covered_lines)
        n_miss = len(missed_lines)
        cov_pct = (n_cov / n_exec * 100.0) if n_exec > 0 else 100.0

        total_statements += n_exec
        total_covered += n_cov
        total_missed += n_miss

        rel_name = file_p.relative_to(PROJECT_ROOT)
        table_rows.append(
            {
                "file": str(rel_name),
                "stmts": n_exec,
                "miss": n_miss,
                "cover": cov_pct,
                "missing_str": format_line_ranges(missed_lines),
            }
        )

    overall_pct = (total_covered / total_statements * 100.0) if total_statements > 0 else 0.0

    print("\n" + "=" * 80)
    print(" CODE COVERAGE REPORT")
    print("=" * 80)
    print(f"{'Name':<32} {'Stmts':>8} {'Miss':>8} {'Cover':>8}   {'Missing Lines'}")
    print("-" * 80)

    for row in table_rows:
        missing_preview = row["missing_str"]
        if len(missing_preview) > 30:
            missing_preview = missing_preview[:27] + "..."
        print(
            f"{row['file']:<32} {row['stmts']:>8} {row['miss']:>8} {row['cover']:>7.1f}%   {missing_preview}"
        )

    print("-" * 80)
    print(f"{'TOTAL':<32} {total_statements:>8} {total_missed:>8} {overall_pct:>7.1f}%")
    print("=" * 80)

    tests_run = test_result.testsRun
    errors = len(test_result.errors)
    failures = len(test_result.failures)
    skipped = len(test_result.skipped)

    print(
        f"\nTest Summary: {tests_run} tests executed in {duration:.2f}s "
        f"({errors} errors, {failures} failures, {skipped} skipped)"
    )

    passed_all_tests = test_result.wasSuccessful()
    passed_coverage = overall_pct >= min_coverage

    if not passed_all_tests:
        print(f"\n❌ FAILED: One or more tests failed.")
        return 1

    if not passed_coverage:
        print(
            f"\n❌ FAILED: Code coverage {overall_pct:.1f}% is below required threshold {min_coverage:.1f}%"
        )
        return 1

    print(
        f"\n✅ PASSED: All {tests_run} tests passed with {overall_pct:.1f}% coverage (target >= {min_coverage:.1f}%).\n"
    )
    return 0


def main():
    parser = argparse.ArgumentParser(description="Pure Python stdlib Test & Coverage Runner")
    parser.add_argument(
        "--threshold",
        "--min-coverage",
        "-t",
        type=float,
        default=90.0,
        help="Minimum required code coverage percentage (default: 90.0)",
    )
    parser.add_argument(
        "--verbose",
        "-v",
        action="count",
        default=2,
        help="Test output verbosity level (default: 2)",
    )
    parser.add_argument(
        "--test-dir",
        default="tests",
        help="Directory containing unittest suites (default: tests)",
    )
    parser.add_argument(
        "--target-dir",
        default="bridge",
        help="Target package directory for coverage calculation (default: bridge)",
    )

    args = parser.parse_args()
    sys.exit(
        run_tests_with_coverage(
            test_dir=args.test_dir,
            target_dir=args.target_dir,
            min_coverage=args.threshold,
            verbosity=args.verbose,
        )
    )


if __name__ == "__main__":
    main()
