import argparse
from pathlib import Path

from app.core.config import get_settings
from evals.real_strategic import (
    GOAL,
    RESTORE_ONLY_GOAL,
    run_real_strategic_evaluations,
    write_real_strategic_report,
)
from evals.runner import run_evaluations, write_reports


def main() -> None:
    parser = argparse.ArgumentParser(prog="journey-agent")
    subcommands = parser.add_subparsers(dest="command", required=True)
    eval_parser = subcommands.add_parser("eval")
    eval_subcommands = eval_parser.add_subparsers(dest="eval_command", required=True)
    run_parser = eval_subcommands.add_parser("run")
    run_parser.add_argument("--output", type=Path, default=Path("eval-results"))
    strategic_parser = eval_subcommands.add_parser("real-strategic")
    strategic_parser.add_argument(
        "--output", type=Path, default=Path("eval-results-real-strategic")
    )
    strategic_parser.add_argument("--attempts", type=int, default=1)
    strategic_parser.add_argument(
        "--profile",
        choices=("full", "restore-only"),
        default="full",
    )
    args = parser.parse_args()
    if args.command == "eval" and args.eval_command == "run":
        report = run_evaluations()
        write_reports(report, args.output)
        summary = report["summary"]
        print(f"Evaluation complete: {summary}")
    if args.command == "eval" and args.eval_command == "real-strategic":
        report = run_real_strategic_evaluations(
            get_settings(),
            attempts=args.attempts,
            goal=RESTORE_ONLY_GOAL if args.profile == "restore-only" else GOAL,
        )
        write_real_strategic_report(report, args.output)
        summary = report["summary"]
        print(f"Real strategic evaluation complete: {summary}")


if __name__ == "__main__":
    main()
