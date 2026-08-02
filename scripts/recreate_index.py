"""인덱스를 지우고 다시 만듭니다.

    python scripts/recreate_index.py --project-id 1
"""

from __future__ import annotations

import argparse


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-id", type=int, required=True)
    parser.parse_args()
    raise NotImplementedError


if __name__ == "__main__":
    raise SystemExit(main())
