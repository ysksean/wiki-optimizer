"""pytest 공통 설정 — src/를 import 경로에 추가."""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(__file__)), "src"))
