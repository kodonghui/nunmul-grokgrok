"""Manage a cumulative, per-medicine list with explicit calendar work."""

from datetime import datetime
from pathlib import Path

if __package__:
    from .common import JsonArgumentParser, SEOUL, parse_instant, read_json, run_cli
    from .family_store import DEFAULT_STORE, acknowledge, calendar_plan, medicine_action, transact
else:
    from common import JsonArgumentParser, SEOUL, parse_instant, read_json, run_cli
    from family_store import DEFAULT_STORE, acknowledge, calendar_plan, medicine_action, transact


def main(argv=None):
    parser = JsonArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    for command in ("list", "add", "set-schedule", "stop", "clear-schedule", "delete", "calendar-plan", "calendar-ack"):
        sub = commands.add_parser(command)
        sub.add_argument("--store", type=Path, default=DEFAULT_STORE)
        sub.add_argument("--at")
        sub.add_argument("--profile")
        sub.add_argument("--if-revision", type=int)
        if command == "list":
            sub.add_argument("--all", action="store_true")
        if command in ("add", "set-schedule"):
            sub.add_argument("--in", dest="input", required=True, type=Path)
            sub.add_argument("--slots", type=Path)
        if command == "add":
            sub.add_argument("--request-id")
        if command in ("set-schedule", "stop", "clear-schedule", "delete"):
            sub.add_argument("medicine_id")
        if command == "calendar-ack":
            sub.add_argument("key")
            sub.add_argument("--fingerprint", required=True)
            sub.add_argument("--event-id")
    args = parser.parse_args(argv)
    at = parse_instant(args.at, "at") if args.at is not None else datetime.now(SEOUL)
    slots = None
    if args.command in ("add", "set-schedule"):
        default_slots = args.store.parent / "slots.json"
        slots_path = args.slots or (default_slots if default_slots.is_file() else None)
        slots = read_json(slots_path) if slots_path is not None else None
        payload = read_json(args.input)
    def action(store):
        if args.command == "calendar-plan":
            return calendar_plan(store, args.profile)
        if args.command == "calendar-ack":
            return acknowledge(store, args.key, args.fingerprint, args.event_id, args.profile)
        operation = {"set-schedule": "schedule", "clear-schedule": "clear"}.get(args.command, args.command)
        return medicine_action(store, args.profile, operation, at,
                               payload=payload if args.command in ("add", "set-schedule") else {"all": args.all} if args.command == "list" else None,
                               medicine_id=getattr(args, "medicine_id", None), request_id=getattr(args, "request_id", None), slots=slots)
    return transact(args.store, action, at, args.if_revision, include_changes=args.command not in ("list", "calendar-plan"))


if __name__ == "__main__":
    raise SystemExit(run_cli(main))
