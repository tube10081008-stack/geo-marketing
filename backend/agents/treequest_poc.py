"""
TreeQuest(AB-MCTS-A) × 지오 페르소나 이식 가능성 PoC (오프라인·결정적).

가설: TreeQuest의 '행동(action)' = 우리 페르소나(획득/전환/유지).
      AB-MCTS가 점수 피드백으로 예산을 페르소나별로 적응 배분하면,
      임계값 하드코딩(라우팅 휴리스틱 v1)을 탐색 기반 라우팅으로 대체 가능한지 검증.

실행: PYTHONPATH=. python agents/treequest_poc.py   (LLM 불필요 — 모의 채점기)
설치: pip install treequest  (ABMCTS-A는 scipy/joblib 수준의 경량 의존성)

운영 설계 노트(RESEARCH-treequest-marlin.md 참조):
- generate = 저가 모델(Flash)로 페르소나 액션 생성/정제
- score    = LLM-judge(루브릭: 베이스라인 적합·측정가능성·[M#] 근거정합, 0~1)
- 예산     = 크레딧과 직결(1 스텝 = 생성 1 + 채점 1 호출) → 프리미엄 기능에만 사용
"""
import random
from collections import Counter

import treequest as tq

# ── 시나리오: 유지(ret)가 실제 최약 노드인 가게 ─────────────────────
# (평점 4.1: 개선 여지 / 객단가 정상 / 재방문 30%·단골 20%: 최약)
BASELINE = {"avg_rating": 4.1, "aov": 27273, "revisit": 30, "regular": 20}

# 페르소나별 '진짜 효용'(모의) — 운영에선 LLM-judge 루브릭 점수가 이 자리를 대체.
# 라우팅이 이 값을 사전에 모른 채 탐색만으로 찾아내는지가 관건.
TRUE_UTILITY = {"acq": 0.55, "cvr": 0.45, "ret": 0.74}

rng = random.Random(42)  # 재현성


def make_generate(persona: str):
    """페르소나 액션 생성기. parent=None이면 초안, 아니면 정제(깊이 보너스)."""

    def generate(parent_state):
        if parent_state is None:
            depth, text = 1, f"[{persona}] 초안 액션(베이스라인 {BASELINE} 기반)"
        else:
            depth = parent_state["depth"] + 1
            text = parent_state["text"] + f" → 정제#{depth}"
        # 모의 채점: 진짜 효용 + 정제 보너스(수확체감) + 소음
        score = min(1.0, TRUE_UTILITY[persona] + 0.05 * (depth - 1) * (0.7 ** (depth - 1))
                    + rng.gauss(0, 0.05))
        return {"persona": persona, "depth": depth, "text": text}, max(0.0, score)

    return generate


def main():
    budget = 30
    algo = tq.ABMCTSA()
    tree = algo.init_tree()
    actions = {p: make_generate(p) for p in ("acq", "cvr", "ret")}

    for _ in range(budget):
        tree = algo.step(tree, actions)

    # 예산 배분 관찰: 노드가 어느 페르소나에 얼마나 쓰였나
    states = [s for s, _ in tq.top_k(tree, algo, k=budget)]
    alloc = Counter(s["persona"] for s in states)
    best_state, best_score = tq.top_k(tree, algo, k=1)[0]

    print(f"예산 {budget}스텝의 페르소나별 배분: {dict(alloc)}")
    print(f"최고 액션: persona={best_state['persona']}, depth={best_state['depth']}, score={best_score:.3f}")
    assert alloc["ret"] == max(alloc.values()), "최약 KPI(ret)에 최다 예산이 가야 함"
    assert best_state["persona"] == "ret"
    print("✅ AB-MCTS가 사전 규칙 없이 최약 노드(ret)를 찾아 예산을 몰아줌 — 라우팅 대체 가능성 확인")


if __name__ == "__main__":
    main()
