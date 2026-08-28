---
title: Transformer 아키텍처 총정리 (Attention · Multi-Head · 학습 vs 추론 · Superposition)
sources:
  - "claude-code session: study/model — 모델 아키텍처 학습 노트"
  - https://transformer-circuits.pub/2022/toy_model/index.html
  - https://www.anthropic.com/research/emotion-concepts-function
  - https://transformer-circuits.pub/2026/emotions/index.html
added: 2026-06-08
---

# Transformer 아키텍처 총정리

## 1. 등장 배경 — RNN/LSTM의 한계

기존 RNN·LSTM은 이전 은닉층(hidden state) 값을 다음 은닉층으로 순차 전달한다. 이 구조의 한계:

- **순차 처리만 가능** → 병렬화 불가
- **장거리 의존성 학습 어려움** → 전달이 거듭될수록 먼저 들어온 정보가 희석/소실
- → "Recurrence를 버리고 **Attention만 쓰자**"

### 의존성 구조 비교
- **RNN**: 수평 의존성 (`h_{t-1}` 이 `h_t` 의 입력) → 직렬
- **Transformer**: 수직 의존성 (모든 토큰이 이전 layer만 봄) → 같은 layer 내 병렬 처리 가능

---

## 2. Attention 메커니즘

핵심 아이디어: 지금까지의 맥락에서 **중요한 단어에 더 높은 가중치**를 두어 문맥을 기억한다.

### Q, K, V
- 입력 벡터에 학습된 가중치 `W^Q`, `W^K`, `W^V` 를 곱해 생성
- **Query = 질문**, **Key = 라벨/색인**, **Value = 실제 정보**
- 데이터베이스의 **soft lookup** 개념

### Scaled Dot-Product Attention
수식: `softmax(QK^T / √d_k) · V`

1. 내적(`QK^T`)으로 Query–Key 유사도 점수 계산 (병렬)
2. `√d_k` 로 나눠 softmax saturation(포화) 방지
3. Softmax로 확률 분포화 (합 = 1)
4. 그 가중치로 모든 Value를 가중합 → 하나의 벡터 생성

### Multi-Head Attention
- `h = 8` 개 head 병렬 수행
- `d_k = d_v = d_model / h = 64`
- 각 head가 **다른 관점** 학습 (문법, 대명사, 의미 등)
- 각 head 출력을 Concat 후 `W^O` 로 projection
- 총 계산량은 single-head full-dim과 비슷

### Attention 3가지 사용처
1. **Encoder Self-Attention**: Q, K, V 모두 encoder에서
2. **Decoder Masked Self-Attention**: 미래 토큰 마스킹
3. **Encoder-Decoder Cross-Attention**: Q는 decoder, K/V는 encoder

---

## 3. 병렬 학습의 비결

### Teacher Forcing
- 학습 시 정답 시퀀스 전체를 디코더 입력으로 **한 번에** 투입
- 추론은 순차적이지만, 학습량이 압도적으로 많으므로 **학습 병렬화가 핵심 이득**

### Causal Mask
- Attention 점수 행렬에서 미래 위치를 `-∞` 로
- Softmax 후 가중치 0이 되어 미래를 못 봄
- 한 번의 forward pass로 모든 위치 예측 가능

### Output Shift
- 디코더 입력은 정답을 한 칸 오른쪽으로 shift
- 위치 i 예측이 위치 i 미만에만 의존하도록 보장

### 행렬 곱셈 병렬성
- 토큰들을 행렬로 묶어 한 번의 행렬 곱셈으로 처리
- GPU 코어가 모든 원소를 동시 계산 → O(1) 순차 연산

---

## 4. Transformer 블록 구조

### Encoder Layer (6개 쌓음)
- Sub-layer 1: Multi-Head Self-Attention
- Sub-layer 2: Position-wise FFN
- 각 sub-layer 주위: **Residual + LayerNorm**

### Decoder Layer (6개 쌓음)
- Sub-layer 1: Masked Multi-Head Self-Attention
- Sub-layer 2: Encoder-Decoder Cross-Attention
- Sub-layer 3: Position-wise FFN
- 각 sub-layer 주위: **Residual + LayerNorm**

### Residual Connection (잔차 연결)
- 수식: `y = x + F(x)` (ResNet에서 유래)
- "가만히 있기" 옵션 제공 → 깊게 쌓아도 안전
- 변경분(`F(x) = y - x`)만 학습하면 됨
- Gradient flow 보존 (+1 효과)
- 학습 초기: `F(x) ≈ 0` 이라 안전한 출발점

### Layer Normalization
- **Feature 차원** 정규화 (각 토큰 벡터 내부)
- BatchNorm과 달리 시퀀스 길이/batch 크기와 무관
- 학습 안정성 확보

### Position-wise FFN
- 2-layer MLP: `512 → 2048 → 512`, ReLU 활성화
- 각 위치 독립적, 동일하게 적용 (위치 간 가중치 공유, layer 간에는 다름)
- 역할 분담: **Attention(정보 수집) → FFN(위치별 변환)**

### Positional Encoding
- Transformer는 순서를 모르므로 위치 정보를 주입해야 함
- Sin/Cos 함수: `PE_(pos,2i) = sin(pos / 10000^(2i/d_model))`
- 차원마다 다른 주파수 (멀티 스케일)
- 상대적 위치를 선형 변환으로 표현 가능

---

## 5. 학습 vs 추론

### 학습 시
- Teacher forcing + causal mask로 모든 위치 **병렬 처리**
- 1 forward pass에 N개 예측 동시 계산
- Loss = 각 위치 cross-entropy의 평균

### 추론 시
- 토큰 하나씩 순차 생성 (auto-regressive)
- Encoder는 한 번만 처리
- **KV cache**로 이전 계산 재활용

### 출력 → 토큰 선택
1. 마지막 layer의 출력 벡터
2. Vocabulary 가중치와 행렬 곱 → logits
3. Softmax → 확률 분포
4. Greedy 또는 Sampling으로 토큰 선택

---

## 6. 가중치 vs 활성값

- **가중치 (학습 후 고정)**: `W^Q`, `W^K`, `W^V`, `W^O`, FFN, 임베딩
- **활성값 (입력마다 계산)**: Q, K, V, attention 점수, 출력 벡터
- 추론 시 가중치는 고정, 입력에 따라 활성값만 달라짐

---

## 7. Layer 깊이 쌓기

### N=6의 의미
- 구조는 동일하지만 가중치는 layer마다 다름
- **점진적 추상화**: layer 1(직접 관계) → layer 6(추상적 의미)
- 잔차 연결 덕분에 깊게 쌓아도 안전

### 현대 모델은 더 깊음
- BERT: 12–24
- GPT-3: 96
- 잔차 + LayerNorm + Pre-Norm 등이 깊이 학습을 가능케 함

> Transformer 블록(동일 구조)을 겹겹이 쌓으면 성능이 올라간다. 이유는 모델의 **Shaping**(개념·능력을 구현하는 성능)이 높아지기 때문. 모델 내부는 방향(direction)이나 영역(region)으로 분석 가능해서, 데이터의 방향/영역을 보면 예측이 가능하다.

---

## 8. Superposition — Toy Models of Superposition

참고: <https://transformer-circuits.pub/2022/toy_model/index.html>

### Superposition 개념
- 서로 다른 특징(feature)을 **하나의 뉴런에 겹쳐 저장**하는 것
- 더 쉽게: 같이 켜질 일이 거의 없는 특징들을 뉴런 한 개에 저장 (이 성질이 **희소성, sparsity**)
- 만약 희소성이 없는 특징들을 겹쳐놓으면, 무엇 때문에 켜졌는지 모델이 헷갈려 **서로 간섭(interference)** 발생

### ReLU vs 선형 모델
- **선형 모델**: PCA(차원 축소)처럼 동작. 뉴런이 5개면 중요한 특징 5개만 저장하고 나머지는 버림
- **ReLU**: 특징이 희소해지면 음수를 0으로 자르기 때문에, 겹쳐 저장해도 간섭이 줄어 더 많은 특징을 표현 가능

### 관련 Anthropic 리서치
- 감정 개념의 기능적 표현: <https://www.anthropic.com/research/emotion-concepts-function>
- Emotions (circuits): <https://transformer-circuits.pub/2026/emotions/index.html>
