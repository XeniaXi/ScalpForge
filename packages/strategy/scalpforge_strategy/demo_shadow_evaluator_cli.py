from __future__ import annotations

import argparse
import json
from pathlib import Path

from .demo_shadow_evaluator import evaluate_demo_shadow


def main() -> None:
    parser = argparse.ArgumentParser(description="Evaluate frozen prospective demo-shadow evidence")
    parser.add_argument("--protocol", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(evaluate_demo_shadow(args.protocol)))


if __name__ == "__main__":
    main()
