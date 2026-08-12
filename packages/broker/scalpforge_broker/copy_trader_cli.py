import argparse
import json
from pathlib import Path

from scalpforge_broker.copy_trader import audit_copy_history, write_audit


def main() -> None:
    parser = argparse.ArgumentParser(description="Audit an exported copy-trader history")
    parser.add_argument("history", type=Path)
    parser.add_argument("--starting-equity", type=float, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    args = parser.parse_args()
    report = audit_copy_history(args.history, starting_equity=args.starting_equity)
    destination = write_audit(report, args.output_root)
    print(json.dumps({**report, "report_path": str(destination)}))


if __name__ == "__main__":
    main()
