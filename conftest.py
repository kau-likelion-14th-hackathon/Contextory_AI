"""pytest 공통 설정 — 저장소 루트를 import 경로에 넣어 `core/`, `services/`, `eval/` 를 절대 import 한다."""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
