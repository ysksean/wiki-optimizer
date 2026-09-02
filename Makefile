# wiki-optimizer 개발 명령 모음. `make` 또는 `make help`로 목록 확인.
.PHONY: help dev test lint check batch

PORT ?= 8765

help: ## 사용 가능한 명령 목록
	@grep -E '^[a-z]+:.*##' $(MAKEFILE_LIST) | awk -F':.*## ' '{printf "  make %-8s %s\n", $$1, $$2}'

dev: ## 대시보드 서버 실행 (PORT=8765 변경 가능)
	python3 src/web.py --port $(PORT)

test: ## 테스트 (CI와 동일)
	pytest tests -q
	bash tests/test_harness.sh

lint: ## 린트 + 컴파일 검사 (CI와 동일)
	ruff check --select E9,F src tests
	python3 -m py_compile src/*.py

check: lint test ## lint + test 한 번에

batch: ## 소형 대조 배치 예시 (문서 3개 x run 1)
	python3 src/batch.py --docs 3 --runs 1 --with-control
