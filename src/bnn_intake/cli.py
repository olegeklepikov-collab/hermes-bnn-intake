"""Isolated operator CLI. It explicitly refuses live paths outside this repository."""
import argparse
import json
import sys
from pathlib import Path
from .engine import Intake
from .storage import canonical, install


def main(argv=None):
    parser = argparse.ArgumentParser(description="Intake A1: isolated development operator, not a production activation command")
    parser.add_argument("--config", required=True, type=Path)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("migrate")
    mode = sub.add_parser("mode")
    mode.add_argument("--sid", required=True)
    mode.add_argument("--mode", required=True, choices=["off", "auto", "on", "status"])
    mode.add_argument("--expected-revision", type=int, default=0)
    call = sub.add_parser("call")
    call.add_argument("--sid", required=True)
    call.add_argument("--request", required=True, help="JSON file or - for standard input")
    questions = sub.add_parser("questions")
    questions.add_argument("--sid", required=True)
    questions.add_argument("--request", required=True)
    documents = sub.add_parser("documents")
    documents.add_argument("--sid", required=True)
    documents.add_argument("--request", required=True)
    analysis = sub.add_parser("analysis")
    analysis.add_argument("--sid", required=True)
    analysis.add_argument("--request", required=True)
    control = sub.add_parser("interview")
    control.add_argument("--sid", required=True)
    control.add_argument("--request", required=True)
    args = parser.parse_args(argv)
    # Keep the active installation outside this development command's write surface.
    root = Path(__file__).resolve().parents[2] / ".test-state"
    config_path = args.config.resolve()
    if not config_path.is_relative_to(root.resolve()):
        parser.error("isolated config must be under this repository's .test-state")
    config = json.loads(config_path.read_text())
    for key in ["state_root", "profile_home"]:
        if not Path(config[key]).resolve().is_relative_to(root.resolve()):
            parser.error("isolated state/profile must remain under .test-state")
    from bn_memory.store import Store
    store = Store(config["state_root"], config)
    engine = Intake(store)
    if args.command == "migrate":
        value = install(store)
    elif args.command == "mode":
        value = engine.mode(args.sid) if args.mode == "status" else engine.set_mode(args.sid, args.mode, args.expected_revision, "local_development_operator")
    else:
        text = sys.stdin.read() if args.request == "-" else Path(args.request).read_text()
        if args.command in {"documents", "analysis"}:
            from types import SimpleNamespace
            from .documents import DocumentBridge
            engine.documents = DocumentBridge(engine, SimpleNamespace(store=store, config=config))
            if args.command == "analysis":
                from .analysis import Analysis
                engine.analysis = Analysis(engine)
                value = engine.analysis.call(args.sid, json.loads(text))
            else:
                value = engine.documents.call(args.sid, json.loads(text))
        elif args.command == "questions":
            from .interview import Interview
            value = Interview(engine).call(args.sid, json.loads(text))
        elif args.command == "interview":
            from .interview import Interview
            value = Interview(engine).control(args.sid, json.loads(text), "local_development_operator")
        else:
            value = engine.call(args.sid, json.loads(text))
    print(canonical(value))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
