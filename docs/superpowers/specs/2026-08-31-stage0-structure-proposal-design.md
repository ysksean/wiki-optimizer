# Stage 0 — 백지 상태 wiki 폴더구조 제안

날짜: 2026-08-31
상태: 설계 승인 대기

## 문제

wiki-optimizer의 기존 세 흐름(audit / Stage A 요약 진화 / Stage B 구조 진화)은
모두 **이미 존재하는 문서**가 입력이다. "wiki가 아직 없고, 레포·참고문서·태스크
설명만 있을 때 폴더구조를 제안"하는 흐름이 없다.

핵심 재해석 두 가지:

1. **cold start가 아니다.** raw는 있다 — 레포가 raw다. 바뀌는 것은 **질문의
   출처**다. 기존은 문서에서 질문을 뽑고("문서에 뭐가 적혀 있나"), Stage 0는
   태스크에서 뽑는다("이 일을 하려면 위키가 무엇에 답해야 하나"). 이 축 전환
   덕에 "레포에 근거가 없는 것"(gap)이 드러나고, 그것이 제안의 절반이다.
2. **빈 골격 = 레포 위의 라우팅 테이블.** 각 페이지가 purpose(답해야 할 질문)와
   sources(근거 원본)를 가지면 내용 없이도 Query 기반 채점이 성립한다:
   질문 → Router가 페이지 선택 → 그 페이지의 sources 실물을 읽고 답 →
   원본 근거 정답과 대조.

## 범위

- 기존 audit / Stage A / Stage B / apply는 **변경하지 않는다.**
- Stage 0는 백지 전용. 기존 wiki와의 diff 모드는 범위 밖 (추후 판단).
- 산출물: 빈 골격(.md stub 트리, frontmatter만) + 리포트. 내용 채우기는 Stage A
  또는 사람의 몫.

## 입력

| 이름 | 필수 | 설명 |
|---|---|---|
| `sources[]` | ✓ | 레포/참고문서 디렉터리 경로 목록 (예: `~/aipmop`) |
| `task` | ✓ | 자유 텍스트 태스크 설명. 질문 세트의 유일한 출처 |
| `out_dir` | ✓(write 시) | 골격을 쓸 위치 |

## 데이터 표현

제안(proposal)은 flat 리스트 하나다. 트리 자료구조를 별도로 만들지 않는다 —
`path`의 슬래시에서 폴더가 파생된다.

```json
{"pages": [{
  "path": "prerm/scoring-criteria.md",
  "title": "사전RM 평가항목 채점 기준",
  "purpose": "항목별 판단 기준·배점·산출 근거는 무엇인가",
  "sources": ["aipmop/docs/domain/prerm-scoring.md#채점-기준",
               "aipmop/api/.../scoring.py"],
  "status": "grounded"   // grounded | gap
}]}
```

### sources 해상도 — 하이브리드 (결정)

- 항목은 `경로` 또는 `경로#헤딩` 둘 다 허용.
- `kind="doc"`(.md/.txt)는 섹션 앵커 허용, `kind="code"`는 파일 단위만.
- **앵커 검증은 결정론적**: repo_map이 뽑아둔 헤딩 목록에 없는 앵커는 거부하고
  파일 전체로 폴백, `anchor_miss` 카운트를 리포트에 노출한다. LLM의 헤딩 환각이
  빈 컨텍스트로 조용히 새어 "구조가 나쁘다"로 오인되는 경로를 차단한다.
- 근거 실측(~/skax-docs): 파일 10~27k자, h2 섹션 평균 1.8~2.7k자 — 파일:섹션
  ≈ 8:1. 파일 단위면 효율 점수가 구조 품질이 아니라 원본 파일 크기를 반영해
  진화 신호가 죽는다. 해상도 선택 자체가 진화 대상이 된다.

## 모듈 (신규 5개, 기존 수정 없음 — web.py 배선은 3단계)

### 1. `src/repo_map.py` — 증거 지도 (LLM 호출 없음, 순수 로직)

```python
def build_map(roots, max_files=400, head_chars=600):
    # → [{"rel","path","size","kind","headings":[{"text","offset"}...],"head":"..."}]
def read_source(entry_or_path, anchor=None, cap=12000):
    # 경로(+앵커)의 실물 텍스트. 앵커 미검증 시 파일 전체 폴백
```

- 제외: `.git`, `node_modules`, `__pycache__`, `dist`, `.venv`, lockfile, 바이너리.
- `kind="doc"`: `^#{1,3}` 헤딩(+offset) + 앞 600자.
- `kind="code"`: 모듈 docstring + top-level `def`/`class`/`export` 이름만. 본문 없음.
- `max_files` 초과 시 최상위 디렉터리별로 고르게 남긴다(폭 우선) — 한 폴더가
  지도를 독식하면 제안이 쏠린다.
- 캐시: `runs/repomap/<roots+mtime 해시>.json`.

### 2. `src/task_questions.py` — 태스크 기반 질문 세트

2단계. 정답 근거는 항상 raw(레포 실물)라는 기존 원칙 유지.

1. **질문 생성**: task + 지도 digest → "이 일을 하려면 위키가 무엇에 답해야
   하나" N개. 답 존재 여부와 무관하게 생성.
2. **정답 확보(oracle)**: 질문마다 지도에서 관련 파일 선택(structure.route
   재사용) → 실물을 읽어 정답 생성. 실패 시 `a=None` = **gap 질문**.

gap 질문은 채점에서 제외하되 버리지 않는다 — 제안에 `status:gap` 페이지로
반영된다("이 축은 필요한데 레포에 근거가 없다" = 외부에서 받아야 할 목록).
캐시: `runs/qcache/` (기존 관례).

### 3. `src/proposal.py` — 제안 생성 + 채점

```python
def propose(task, repo_map, strategy):        # → {"pages":[...]} (+파싱 재시도/폴백, structure._parse_struct 관례)
def score_proposal(pages, question_set, ...): # → {"total","accuracy","efficiency","avg_read","anchor_miss","details",...}
```

채점 루프 (기존 부품 재사용):
- Router: index(title + purpose)에서 페이지 선택 — `structure.route()` 그대로.
- 읽기: 선택된 페이지의 sources 실물(`repo_map.read_source`, source당 cap 12k).
- 정확도: `scoring.judge_all` — parse_failed 규약 그대로 전파.
- 효율: `1 - avg_read / denom`, **denom = 제안이 참조하는 source 텍스트 총합**
  (레포 전체가 아님 — 자기조정: 큰 파일을 물면 denom도 커지지만 avg_read가
  더 빨리 커진다).

### 4. `src/evolve_proposal.py` — 진화 루프 (2단계)

`evolve_structure.py`와 같은 뼈대: seed 전략, 세대 루프, best는 결과와 짝으로
저장, control arm(`--control` = seed 재샘플링), provenance, judge 파싱 실패
세대 best 제외, `runs/proposal-{arm}-{stamp}/` + progress.json + report.json.

Reflector 근거만 다르다:
- 틀린 질문 — **페이지 선택 오류(purpose 모호) vs sources에 답 없음(매핑
  오류)를 구분**해 넘긴다.
- 많이 읽은 질문 — 페이지가 과대한 source를 물고 있음 (섹션 앵커 유도).
- gap 목록 + anchor_miss.

### 5. `src/skeleton.py` — 골격 쓰기

stub .md: frontmatter(title/purpose/sources/status/generated_by) + 빈 본문.
기본 `--dry-run`(트리 출력만). `--write` 시에도 **기존 파일은 절대 덮어쓰지
않고 skip + 보고**.

## CLI (1단계 진입점)

```
python3 src/evolve_proposal.py --source ~/aipmop --task-file task.txt --n-qa 8 --generations 1 --out-dir runs
# --write <dir> 로 골격 실제 생성, 기본은 dry-run
```

## 실행 순서 (결정)

1. **1단계**: repo_map + task_questions + 1회 제안 + skeleton(dry-run) →
   **~/aipmop 레포 하나 + 사전RM 태스크로 실제 실행, 결과 보고.** 판정 포인트: 태스크에서
   뽑은 질문이 쓸 만한가. 아니면 2단계 설계를 조정한다.
2. **2단계**: score_proposal + evolve_proposal + control arm.
3. **3단계**: web.py에 mode="propose" 탭.

## 테스트

기존 관례(오프라인, LLM 호출 없음) 유지:
- repo_map: 제외 규칙, kind 판별, 헤딩+offset 추출, max_files 균등 컷, 캐시 키.
- read_source: 앵커 적중/미스 폴백, cap.
- proposal 파싱: 정상/재시도/폴백, path 정규화(절대경로·`..` 거부).
- skeleton: dry-run 무변경, 기존 파일 skip, frontmatter 형식.
- score_proposal: 가짜 llm.generate 주입으로 denom/anchor_miss/gap 제외 검증
  (기존 test_core.py 방식).

## 범위 밖 (명시)

- 기존 wiki와의 diff/병합 제안.
- 읽기 시점 동적 retrieval(부분 읽기) — 구조 문제와 retrieval 문제가 섞여
  진단 불가라 채택하지 않음.
- Stage 0 출력 → Stage B/A 자동 연계 파이프라인 (수동으로는 가능).
