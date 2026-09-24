"""pytest 공통 설정 — src/를 import 경로에 추가하고, 질문 거르기·기준선은 기본으로 끈다.

question_filter는 실제 LLM을 부른다(문서 없이 답하기·원본 전체로 답하기). 기존 테스트는 질문
생성·채점을 가짜로 바꿔 돌기 때문에, 켜 두면 가짜를 우회해 실제 claude를 부르게 된다.
거르기·기준선 테스트는 스스로 켜고 llm.generate를 가짜로 바꾼다."""

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(__file__)), "src"))


@pytest.fixture(autouse=True)
def _no_question_filter_llm(monkeypatch):
    import question_filter
    monkeypatch.setattr(question_filter, "FILTER", False)
    monkeypatch.setattr(question_filter, "BASELINES", False)
    question_filter._MEMO.clear()
