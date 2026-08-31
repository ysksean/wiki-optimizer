"""index.html 차트 접근성 회귀 테스트 — SEA-6.

chart()가 만드는 SVG(role="img")에 aria-label이 붙어 있고,
chart_aria i18n 키가 지원 언어(ko/en/zh) 전부에 있는지 확인한다.
"""

import re
from pathlib import Path

INDEX = Path(__file__).resolve().parent.parent / "src" / "static" / "index.html"


def test_chart_svg_has_aria_label():
    html = INDEX.read_text(encoding="utf-8")
    svg_tags = re.findall(r'<svg[^>]*role="img"[^>]*>', html)
    assert svg_tags, 'role="img" SVG 템플릿이 없다'
    for tag in svg_tags:
        assert "aria-label=" in tag, f"aria-label 없는 이미지 SVG: {tag}"


def test_chart_aria_key_in_all_languages():
    html = INDEX.read_text(encoding="utf-8")
    langs = re.findall(r"^  (\w+): \{", html, flags=re.MULTILINE)
    assert html.count("chart_aria:") == len(langs) == 3
    assert 't("chart_aria"' in html
