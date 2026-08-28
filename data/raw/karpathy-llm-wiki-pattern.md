---
title: Karpathy LLM Wiki 패턴 (조사 메모)
sources:
  - https://gist.github.com/karpathy/442a6bf555914893e9891c11519de94f
  - https://venturebeat.com/data/karpathy-shares-llm-knowledge-base-architecture-that-bypasses-rag-with-an
added: 2026-06-02
---

# Karpathy LLM Wiki — 조사 메모

(이번 세션에서 1차 출처 gist + 기사들로 조사한 거친 메모. Claude가 wiki로 정리한다.)

- Karpathy 제안: 사람이 KB를 유지하고 가끔 LLM에 묻는 게 아니라, **LLM이 KB 전체를 짓고 유지**한다. 사람은 소스/질문만.
- 3계층: `raw/`(sources, immutable) → `wiki/`(LLM 소유, concept별 .md + [[backlink]]) → `index.md` + `log.md`. schema = CLAUDE.md.
- 운영 루프: Ingest(소스 던지면 wiki 10~15페이지 갱신 + index 갱신) / Query(관련 .md 컨텍스트로 읽어 인용 답변, 좋은 답은 새 페이지로) / Lint(모순·stale·고아 페이지 점검).
- **Beyond RAG**: 임베딩/청킹/벡터DB 명시적 거부. 큰 컨텍스트 윈도우 + 에이전트 파일탐색으로 관련 .md를 통째 읽음.
- 스케일 한계: 수백 페이지 넘으면 컨텍스트 한계 → index 기반 선택 로딩 → 300+에서 벡터 재검토.
- Obsidian: vault = .md 폴더 그 자체. Web Clipper(수집) + 그래프뷰/백링크(탐색). 실제 지식 처리는 에이전트(Claude Code).
- 한국 커뮤니티: Claude Code × Obsidian × Karpathy 조합으로 구축 유행. 153파일 → 146요약 + 48엔티티 + 29컨셉 후기 사례.
