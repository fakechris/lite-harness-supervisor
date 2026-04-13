from __future__ import annotations

import argparse
import logging
import sys

from supervisor import app


def build_dev_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="thin-supervisor-dev",
        description="Development/operator CLI for thin-supervisor policy tuning",
    )
    sub = parser.add_subparsers(dest="command")
    app._add_oracle_parser(sub)
    app._add_learn_parser(sub)
    app._add_eval_parser(sub)
    return parser


def main() -> None:
    parser = build_dev_parser()
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )

    if args.command == "oracle":
        if args.oracle_action == "consult":
            sys.exit(app.cmd_oracle(args))
        print("Usage: thin-supervisor-dev oracle consult --question <text>")
        sys.exit(1)
    elif args.command == "learn":
        sys.exit(app.cmd_learn(args))
    elif args.command == "eval":
        sys.exit(app.cmd_eval(args))
    else:
        parser.print_help()
        sys.exit(0)


if __name__ == "__main__":
    main()
