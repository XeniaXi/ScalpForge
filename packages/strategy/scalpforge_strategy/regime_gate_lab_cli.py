import argparse
import json
from pathlib import Path

from scalpforge_strategy.regime_gate_lab import run_regime_gate_lab


def main() -> None:
    parser = argparse.ArgumentParser(description="Run causal session regime gates")
    parser.add_argument("--episode-manifest", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(run_regime_gate_lab(args.episode_manifest, args.output_root)))


if __name__ == "__main__":
    main()
