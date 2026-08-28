---
title: "Redis 기반 BE·AI 실시간 통신 정리"
sources:
  - "claude-code session: aipmo-cc"
added: 2026-06-05
tags: [redis, architecture, messaging, realtime, backend, ai]
aliases: ["BE-AI 통신", "Redis 메시징 정리"]
---

> AI 서버와 백엔드를 분리했을 때 둘을 어떻게 통신시키고 실시간성을 확보하는가에 대한 개념 정리.

## 1. 배경 — AI·BE 서버 분리

- AI 작업(LLM 추론, 에이전트 실행)은 무겁고 오래 걸림. BE가 동기 HTTP로 끝까지 기다리면 커넥션 점유·타임아웃·스케일링 문제가 생김.
- 그래서 서버를 분리하고 **요청을 던져놓고 비동기로 결과를 받는** 구조로 간다.

## 2. 서버 간 통신 — 큐 + 브로커 2개

- 직접 동기 호출 대신 중간에 **큐(메시지 브로커)** 를 둔다.
    - BE는 작업을 큐에 넣고 즉시 리턴(논블로킹)
    - AI 서버는 큐에서 꺼내 처리(워커 패턴)
    - AI가 죽어도 메시지가 큐에 남아 유실 방지, 워커 추가로 수평 확장
- 큐(특히 Redis List)는 **단방향 FIFO** → 양방향 통신엔 큐를 **2개로 분리**한다.
    - **요청 큐**: BE → AI
    - **응답 큐**: AI → BE
- 이게 메시지 큐로 구현하는 **Request-Reply 패턴**.
- cf. Celery의 `broker`(작업 전달) + `result backend`(결과 저장)도 같은 발상.

## 3. 커넥션 풀 (Connection Pool)

- DB 등 외부 연결을 **미리 여러 개 열어두고 재사용**하는 저장소.
- 이유: 연결 1개 맺는 비용(TCP·TLS·인증·세션 초기화)이 큼.
- 동작: 앱 시작 시 N개 연결 생성 → 요청이 **빌림(borrow)** → 사용 → **반납(return, 닫지 않음)** → 다음 요청이 재사용.
- 주요 설정: `pool_size`, `max_overflow`, `pool_timeout`, `pool_recycle`.
- 주의:
    - **pool exhaustion** — 모든 연결이 사용 중이면 대기하다 타임아웃
    - **connection leak** — 반납 누락 시 풀이 점점 비어 멈춤 → `async with`로 반납 보장
- 스택: SQLAlchemy async / `asyncpg` / `redis-py` 모두 내부적으로 풀 사용. 서버 인스턴스가 많아지면 DB `max_connections` 초과 위험 → 앞단에 **PgBouncer** 같은 풀러를 한 번 더 둠.

## 4. DB 테이블과 실시간성

- **핵심: 테이블이 다른 것 자체는 실시간성 문제가 아니다.** 실시간성은 "데이터를 어디 두느냐"가 아니라 **"변경을 어떻게 알리느냐"** 에서 결정된다.
- 역할 분리: **DB = 영속 상태(source of truth), Redis = 실시간 신호.**
- 같은 테이블 vs 다른 테이블 → 실시간성이 아니라 **일관성·경합** 이슈:
    - 공유 테이블: 락 경합·race condition 위험
    - 분리 테이블: 소유권 깔끔, 경합↓, 대신 변경 신호가 필요
    - → "누가 어느 행/컬럼의 주인인가"를 명확히 정해두는 게 핵심
- 실시간 버그 주의: **커밋 먼저 → 그다음 알림.** 커밋 전에 알리면 BE가 빈/오래된 데이터를 읽음(stale read). 또는 신호 메시지에 데이터를 같이 실어 보내 DB 재조회를 생략.

## 5. 폴링 vs Push

- **폴링(polling)**: "바뀐 거 있어?"를 주기적으로 계속 조회.
    - 장점: 단순, 느슨한 결합
    - 단점: 폴링 주기만큼 지연 + 대부분 빈 응답으로 자원 낭비. 주기를 줄이면 부하↑, 늘리면 지연↑ (trade-off)
- **long polling**: 요청을 바로 끊지 않고 열어둔 채 변경이 생기면 그때 응답.
- **push**: SSE / WebSocket / Redis Pub/Sub — 변경 발생 즉시 전달. 폴링의 지연·낭비를 없앰.

## 6. Redis Pub/Sub

- 발행/구독 패턴. 발행자가 **채널(channel)** 에 PUBLISH → 그 채널 구독자 모두가 동시에 수신(**fan-out**).
- 발행자와 구독자는 서로를 모르고 **채널 이름만** 공유 → 느슨한 결합.
- 명령: `SUBSCRIBE events`, `PUBLISH events "msg"`, 패턴 구독 `PSUBSCRIBE chat.*`
- **중요 — 저장하지 않는다.** 발행하는 순간 듣고 있는 구독자에게만 1회 전달되고 끝(fire-and-forget, **at-most-once**). ACK·재전송·이력 조회 없음. 끊겨 있던 구독자는 그사이 메시지를 영영 못 받음.
- 용도: 실시간 브로드캐스트/신호 — **놓쳐도 괜찮은 것**(상태 변경 알림, 스트리밍 토큰 청크).
- 부적합: **유실되면 안 되는 작업 전달**.

## 7. Redis List / Stream

> List, Stream, Pub/Sub은 모두 **Redis가 제공하는 자료구조/기능** (형제 관계). 보장 수준이 다를 뿐.

- **List**: 단순 큐. `LPUSH`로 넣고 `BRPOP`으로 꺼냄(FIFO). 누가 꺼낼 때까지 유실 안 됨. 한 메시지 = 한 소비자가 가져감(소비하면 사라짐). → **작업 큐**에 적합.
- **Stream** (Redis 5.0+): 메시지가 로그처럼 **저장**되고 재조회·**ACK**·**consumer group**(여러 소비자 분산 처리)까지 지원. "미니 Kafka". → **실시간 + 유실 방지**가 둘 다 필요할 때.

## 8. 선택 가이드

| 상황 | 선택 |
| --- | --- |
| 유실되면 안 되는 단순 작업 큐 | **List** |
| 유실 방지 + 재조회 + 그룹 처리 | **Stream** |
| 놓쳐도 되는 실시간 신호/브로드캐스트 | **Pub/Sub** |

- 적용 예시: BE→AI **요청**은 List/Stream(유실 방지), AI→BE **실시간 스트리밍**은 Pub/Sub 또는 Stream으로 받아 **SSE**로 클라이언트에 전달.

## 관련 개념

[[Redis]] · [[Redis Pub-Sub]] · [[Redis Stream]] · [[Redis List]] · [[커넥션 풀]] · [[폴링 vs Push]] · [[메시지 큐]] · [[Request-Reply 패턴]] · [[SSE]]
