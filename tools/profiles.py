"""Create, list and update family aliases in one account."""

from datetime import datetime
from pathlib import Path

if __package__:
    from .common import JsonArgumentParser, SEOUL, parse_instant, read_json, run_cli
    from .family_store import DEFAULT_STORE, create_profile, list_profiles, transact, update_profile
else:
    from common import JsonArgumentParser, SEOUL, parse_instant, read_json, run_cli
    from family_store import DEFAULT_STORE, create_profile, list_profiles, transact, update_profile


def main(argv=None):
    parser = JsonArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    for command in ("create", "list", "update"):
        sub = commands.add_parser(command)
        sub.add_argument("--store", type=Path, default=DEFAULT_STORE)
        sub.add_argument("--at")
        sub.add_argument("--if-revision", type=int)
        if command == "create":
            sub.add_argument("--alias", required=True)
            sub.add_argument("--relationship", default="FAMILY")
            sub.add_argument("--clinical", type=Path)
        if command == "update":
            sub.add_argument("profile")
            sub.add_argument("--in", dest="input", type=Path, required=True)
    args = parser.parse_args(argv)
    at = parse_instant(args.at) if args.at else datetime.now(SEOUL)
    def action(family):
        if args.command == "list":
            return list_profiles(family)
        if args.command == "create":
            return create_profile(family, args.alias, at, args.relationship,
                                  read_json(args.clinical) if args.clinical else None)
        return update_profile(family, args.profile, read_json(args.input), at)
    return transact(args.store, action, at, args.if_revision, include_changes=args.command != "list")


if __name__ == "__main__":
    raise SystemExit(run_cli(main))
