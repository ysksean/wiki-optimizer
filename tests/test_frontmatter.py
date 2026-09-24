"""frontmatter 최소 파서 + 진단 라우터 색인 (실제 독자가 보는 설명으로). LLM 호출 없음."""

import audit
import frontmatter
import structure


def test_split_reads_scalars_and_both_list_styles():
    text = ("---\ntitle: \"Claude Code 개요\"\ncategory: Claude Code\nsources:\n  - raw/a.md\n  - raw/b.md\n"
            "tags: [x, y]\nupdated: 2026-06-03\n---\n\n# Claude Code\n본문")
    meta, body = frontmatter.split(text)
    assert meta == {"title": "Claude Code 개요", "category": "Claude Code",
                    "sources": ["raw/a.md", "raw/b.md"], "tags": ["x", "y"], "updated": "2026-06-03"}
    assert body.lstrip().startswith("# Claude Code")


def test_split_without_or_with_broken_frontmatter_keeps_text():
    assert frontmatter.split("# 제목\n본문") == ({}, "# 제목\n본문")
    broken = "---\ntitle: 닫히지 않음\n본문"
    assert frontmatter.split(broken) == ({}, broken)
    assert frontmatter.split("") == ({}, "")
    assert frontmatter.split("---\r\ntitle: 윈도\r\n---\r\n본문")[0] == {"title": "윈도"}


def test_first_line_skips_frontmatter_and_heading_marks():
    assert frontmatter.first_line("---\ntitle: t\n---\n\n## 섹션 제목\n본문") == "섹션 제목"
    assert frontmatter.first_line("본문만") == "본문만"


def _wiki(tmp_path):
    wiki = tmp_path / "wiki"
    (wiki / "topic").mkdir(parents=True)
    (wiki / "projects" / "p").mkdir(parents=True)
    (wiki / "index.md").write_text(
        "---\ntitle: 색인\n---\n# 색인\n## A\n- [[claude-code]] — **개요** (설정, `단축키`)\n"
        "## B\n- [[topic/rag|RAG 평가]] — 판정형 RAG\n## C\n- [[rag]] — reference-free 평가\n"
        "- 설명 없는 줄 [[claude-code]]\n")
    (wiki / "log.md").write_text("# 로그\n- 2026-08-20 ingest")
    (wiki / "claude-code.md").write_text("---\ntitle: Claude Code 개요\n---\n# Claude Code\n본문")
    (wiki / "topic" / "rag.md").write_text("---\ntitle: RAG\n---\n본문")
    (wiki / "untitled.md").write_text("---\ncategory: x\n---\n\n# 첫 헤딩\n본문")
    (wiki / "plain.md").write_text("그냥 첫 줄\n본문")
    (wiki / "projects" / "p" / "overview.md").write_text("비공개")
    return tmp_path


def test_router_index_uses_what_the_real_reader_sees(tmp_path):
    base = _wiki(tmp_path)
    pages = {p["name"]: p for p in audit.wiki_pages(str(base))}
    assert set(pages) == {"claude-code", "topic/rag", "untitled", "plain"}   # 색인·로그·projects 제외
    assert pages["claude-code"]["desc"] == "개요 (설정, 단축키)"                # index.md 설명 우선
    assert pages["topic/rag"]["desc"] == "판정형 RAG / reference-free 평가"      # 여러 섹션 설명 결합
    assert pages["untitled"]["desc"] == "첫 헤딩"                              # title 없으면 본문 첫 줄
    assert pages["plain"]["desc"] == "그냥 첫 줄"
    assert all(p["desc"] != "---" for p in pages.values())


def test_structure_one_line_prefers_frontmatter_title():
    assert structure._one_line("---\ntitle: 제목\nsources: []\n---\n본문") == "제목"
    assert structure._one_line("# 헤딩\n본문") == "헤딩"
