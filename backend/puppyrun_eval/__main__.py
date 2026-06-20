from __future__ import annotations

import argparse
import sys

from puppyrun_eval.reports import write_reports
from puppyrun_eval.runner import FailureCategory, ResultStatus, run_suite, sanitize_failure_message


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    if args.command == "run":
        try:
            suite_result = run_suite(args.suite)
            report_paths = write_reports(suite_result, report_dir=args.report_dir)
        except ValueError as exc:
            print(f"Eval runner error: {sanitize_failure_message(str(exc))}", file=sys.stderr)
            return 1
        except Exception as exc:
            print(f"Eval runner error: {sanitize_failure_message(str(exc))}", file=sys.stderr)
            return 1

        print(
            f"{suite_result.suite_id}: {suite_result.status.value} "
            f"({len(suite_result.case_results)} cases)"
        )
        if suite_result.status == ResultStatus.BLOCKED:
            blocked_messages = [
                case.failure_message
                for case in suite_result.case_results
                if case.status == ResultStatus.BLOCKED and case.failure_message
            ]
            if blocked_messages:
                print(f"Blocked: {blocked_messages[0]}")
        elif suite_result.status == ResultStatus.FAIL:
            first_failure = next(
                case for case in suite_result.case_results if case.status == ResultStatus.FAIL
            )
            category = first_failure.failure_category or FailureCategory.RUNNER_ERROR
            print(f"Failed: {category.value}: {first_failure.failure_message}")
        print(f"Reports: {report_paths.json_path} {report_paths.markdown_path}")
        return suite_result.exit_code

    parser.print_help()
    return 1


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="puppyrun_eval")
    subparsers = parser.add_subparsers(dest="command", required=True)
    run_parser = subparsers.add_parser("run")
    run_parser.add_argument("--suite", required=True)
    run_parser.add_argument("--report-dir", default=None)
    return parser


if __name__ == "__main__":
    raise SystemExit(main())
