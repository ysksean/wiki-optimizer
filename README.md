# wiki-optimizer

로컬 Qwen(Ollama)으로 도는 **self-evolving 요약 최적화** 실험 도구.

`llm_wiki` 패턴(raw 원본 → wiki 요약)에서 "요약을 어떻게 하는 게 효과적인가"를
자동으로 최적화한다. 사람이 프롬프트를 손보는 대신, 시스템이 스스로
**생성 → 검증 → 반성 → 개선** 루프를 돌며 요약 전략(프롬프트)을 진화시킨다.

## 핵심 아이디어

요약은 코딩처럼 "테스트 통과"라는 명확한 정답이 없다. 대신 **검증 가능한 프록시 지표**로 품질을 자동 채점한다:

- **정보 보존율(coverage)** — raw의 핵심 사실(Q&A로 추출)이 요약에 얼마나 남았나
- **재구성 정확도(faithfulness)** — 요약만 보고 원본 질문에 정확히 답할 수 있나
- **압축 효율(compression)** — 짧으면서도 정보를 담았나 (너무 길거나 짧으면 감점)

## 구조

```
src/
  llm.py         로컬 Qwen 클라이언트 (Ollama HTTP API, thinking off)
  verifier.py    요약 품질 자동 채점
  evolve.py      self-evolving 루프 (생성→검증→반성→개선)
data/raw/        샘플 raw 문서 (실제 llm_wiki에서 복사, 원본은 안 건드림)
runs/            세대별 실행 결과/로그
```

## 실행

```bash
python3 src/evolve.py data/raw/karpathy-llm-wiki-pattern.md --generations 4
```

## 요구사항

- Ollama + `qwen38-local` 모델 (localhost:11434)
- Python 3 (표준 라이브러리만 사용, 추가 설치 없음)
