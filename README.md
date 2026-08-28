# wiki-optimizer

**self-evolving 지식베이스 최적화** 실험 도구.

LLM 호출은 API 키가 아니라 **각자 로그인해둔 CLI 구독 세션**으로 돈다:
`LLM_BACKEND=claude|codex|ollama` (기본 claude).

- `claude` — Claude Code CLI (`claude -p`), Claude 구독 로그인
- `codex` — Codex CLI (`codex exec`), ChatGPT 구독 로그인
- `ollama` — 로컬 Qwen (`qwen38-local`), 오프라인/무과금용

`llm_wiki` 패턴(raw 원본 → wiki 요약/구조)에서 "요약과 폴더 구조를 어떻게 하는 게
효과적인가"를 자동으로 최적화한다. 사람이 프롬프트/구조를 손보는 대신, 시스템이 스스로
**생성 → 검증 → 반성 → 개선** 루프를 돌며 진화시킨다.

## 핵심 원칙 — Query 기반 평가

요약/구조의 좋고 나쁨을 그 자체로 판단하지 않는다. llm_wiki의 실제 용도는
**"질문하면 관련 내용을 읽어 답한다"**(Query)이므로, 그 실제 용도로 평가한다:

> **"이 요약/구조에 질문을 던졌을 때 제대로 답이 나오는가?"**

- **정답의 근거는 항상 원본(raw)** — 남의 요약본을 정답 삼지 않는다 (순환논리 회피).
- **두 축을 동시에** — 정확도(질문에 맞게 답하나) × 효율(적게 읽고 답하나).
- **곱셈 결합**으로 gaming 억제 — "다 때려넣기"(정확↑효율↓)도 "극단 압축"(효율↑정확↓)도
  종합점수가 낮아진다.

여기에 실험 설계 방어선 두 개를 더한다:

- **train / held-out 질문 분리** — Reflector는 train 질문의 오답만 보고 전략을
  고치고, best 판정·보고는 held-out 점수로만 한다. 고정 질문 세트에 대한
  암기(과적합)와 일반화된 개선을 구분하기 위함.
- **무진화 대조군(control arm)** — best는 노이즈 N개의 최댓값이라 진화가 없어도
  best-gen0이 양수로 치우친다. `batch.py --with-control`이 seed 전략 재샘플링
  arm을 함께 돌려, 진화의 진짜 효과 = evolve 향상폭 - control 향상폭으로 본다.

## 두 단계

**A단계 — 요약 전략 최적화** (`evolve.py`, `scoring.py`)
- 진화 손잡이 = 요약 프롬프트
- 문서 하나를 요약 → 요약만으로 질문 세트를 풀어 정확도 + 효율 채점 → 전략 진화

**B단계 — 폴더 구조 최적화** (`evolve_structure.py`, `structure.py`)
- 진화 손잡이 = 분할 전략(몇 개 파일로, 어떤 축으로)
- 여러 문서를 구조화 → 질문마다 읽을 파일 선택(Router) → 정확도 + 효율(읽은 글자수) 채점 → 구조 진화

## 구조

```
src/
  llm.py               LLM 클라이언트 (claude/codex CLI 구독 세션, Ollama 폴백, stdlib만)
  scoring.py           A단계: query 기반 요약 채점 (정확도 x 효율)
  evolve.py            A단계: self-evolving 요약 루프
  structure.py         B단계: Organizer + Router + 구조 채점
  evolve_structure.py  B단계: self-evolving 구조 루프
  batch.py             여러 문서 x 여러 run 배치 실행 + 집계(CSV/리포트)
data/raw/              샘플 raw 문서 (실제 llm_wiki에서 복사, 원본은 안 건드림)
data/questions/        (선택) 수동 질문 세트 <docname>.json
runs/                  세대별/배치 실행 결과 (git 제외)
```

## 실행

```bash
# A단계: 단일 문서 요약 진화 (--control이면 무진화 대조군)
python3 src/evolve.py data/raw/karpathy-llm-wiki-pattern.md --generations 3

# A단계 배치 + 집계 (evolve vs control 두 arm)
python3 src/batch.py --docs 5 --runs 2 --generations 3 --with-control

# B단계: 폴더 구조 진화
python3 src/evolve_structure.py --docs 3 --generations 2 --n-qa 4

# 백엔드 바꿔 돌리기
LLM_BACKEND=codex  python3 src/evolve.py ...   # Codex CLI (ChatGPT 구독)
LLM_BACKEND=ollama python3 src/evolve.py ...   # 로컬 Qwen
```

## 요구사항

- Python 3 (표준 라이브러리만 사용, 추가 설치 없음)
- 백엔드 중 하나:
  - `claude` (기본): Claude Code CLI 로그인 상태. 모델은 `CLAUDE_MODEL`로 변경 (기본 Haiku 4.5)
  - `codex`: Codex CLI 로그인 상태. 모델은 `CODEX_MODEL`로 변경 (기본은 codex 설정값)
  - `ollama`: `qwen38-local` 모델 (localhost:11434)

## 참고

같은 문제 공간의 오픈소스: llm_wiki 생성 도구(Karpathy 패턴 구현체들, WeKnora 등)와
RAG 자가개선 도구(Self-Improving-Agentic-RAG 등)는 많으나, "마크다운 wiki의 구조·요약을
query 성능으로 self-evolving 최적화"하는 조합은 이 둘 사이의 빈틈을 노린다.
