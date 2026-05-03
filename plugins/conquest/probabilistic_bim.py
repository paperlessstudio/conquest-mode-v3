"""Pack #83 Phase 2 — Probabilistic BIM (top-3 候補 + 確率分布).

CEO 直接指示「圧倒的な精度」「今までになかった視点」 — 革新アプローチ ③:

  単一決定論的 BuildingModel ではなく、**top-3 候補 BuildingModel** を保持.
  viewer でユーザフィードバック → ベイズ更新で top 候補を順位入替.
  「ここの解釈が違う」を学習データとして蓄積.

  決定論的 = 永久に超えられない壁
  確率分布 = ユーザ介在で漸近的に高精度化

設計:
  - ProbabilisticElement: 単一座標 + top-3 hypothesis (ElementType × prob × evidence)
  - ProbabilisticBuildingModel: 確定 model + 候補 list
  - merge_persona_votes_to_probabilistic() で multi_persona の生 votes から top-3 抽出
  - update_with_feedback() で user feedback をベイズ更新

不変性:
  - top-3 の確率合計 ≤ 1.0 (残りは "unknown" として暗黙保持)
  - feedback は immutable history、過去 feedback も保持して trend 分析可能
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field, replace
from typing import Optional

from plugins.conquest.schemas import (
    BuildingElement,
    BuildingModel,
    DrawingMetadata,
    ElementType,
    InferenceMetadata,
    MaterialType,
    Point2D,
    StructuralRole,
    ExtractionMethod,
)
from plugins.conquest.multi_persona_inference import (
    PERSONAS,
    ElementVote,
    _spatial_canonical_id,
    _aggregate_votes,
)

logger = logging.getLogger("conquest.probabilistic_bim")


# ─── 確率的 element 表現 ─────────────────────────────────────

@dataclass(frozen=True)
class Hypothesis:
    """単一 element に対する 1 つの仮説."""
    element_type: ElementType
    probability: float       # 0.0-1.0
    evidence: tuple[str, ...] = ()  # 投票したペルソナ id 等
    supporting_personas: tuple[str, ...] = ()


@dataclass(frozen=True)
class ProbabilisticElement:
    """top-3 候補 + 単一 representative geometry を保持.

    geometry は top-1 仮説のものを使用 (top-2/3 と座標差は 100mm grid 内で同一).
    """
    canonical_id: str               # spatial canonical id (multi_persona で生成)
    representative: BuildingElement  # top-1 仮説の element (geometry のみ参照)
    hypotheses: tuple[Hypothesis, ...] = ()  # top-3, 確率降順

    @property
    def top1(self) -> Optional[Hypothesis]:
        return self.hypotheses[0] if self.hypotheses else None

    @property
    def confidence(self) -> float:
        """top-1 の確率 = 信頼度."""
        return self.top1.probability if self.top1 else 0.0

    @property
    def is_ambiguous(self) -> bool:
        """top-1 と top-2 の確率差が 0.2 未満 = ユーザ確認推奨."""
        if len(self.hypotheses) < 2:
            return False
        return (self.hypotheses[0].probability - self.hypotheses[1].probability) < 0.2


@dataclass(frozen=True)
class ProbabilisticBuildingModel:
    """確率的 BuildingModel — top-3 候補保持.

    backward-compat: to_deterministic() で従来の BuildingModel を返す.
    """
    metadata: DrawingMetadata = field(default_factory=DrawingMetadata)
    elements: tuple[ProbabilisticElement, ...] = ()

    @property
    def ambiguous_count(self) -> int:
        """top-1/top-2 差 < 0.2 の element 数 = ユーザ確認推奨."""
        return sum(1 for e in self.elements if e.is_ambiguous)

    def to_deterministic(self) -> BuildingModel:
        """top-1 仮説のみを採用した従来 BuildingModel."""
        det_elements = []
        for pe in self.elements:
            if not pe.top1:
                continue
            top = pe.top1
            elem = pe.representative
            new_inf = InferenceMetadata(
                extraction_method=elem.inference.extraction_method if elem.inference else ExtractionMethod.VISUAL,
                confidence=top.probability,
                inferred_from=top.evidence,
                inference_rule=f"probabilistic_top1_{len(top.supporting_personas)}_personas",
                pass_number=elem.inference.pass_number if elem.inference else 1,
            )
            det_elements.append(BuildingElement(
                id=elem.id,
                element_type=top.element_type,
                name=elem.name,
                start=elem.start,
                end=elem.end,
                thickness=elem.thickness,
                height=elem.height,
                storey=elem.storey,
                material=elem.material,
                structural_role=elem.structural_role,
                inference=new_inf,
            ))
        return BuildingModel(
            metadata=self.metadata,
            elements=tuple(det_elements),
            spaces=(),
        )


# ─── 投票 → top-3 確率分布 ─────────────────────────────────

def _compute_top3_from_vote(vote: ElementVote) -> tuple[Hypothesis, ...]:
    """ElementVote (7 ペルソナ票) から top-3 仮説を抽出.

    各 ElementType に対する得票重み = sum(persona.weight × persona_confidence).
    上位 3 type を確率正規化 (合計 1.0 — 残り "unknown" は暗黙).
    """
    if not vote.votes:
        return ()
    weighted_counts: dict[ElementType, float] = {}
    evidence_by_type: dict[ElementType, list[str]] = {}
    for pid, etype, persona_conf in vote.votes:
        persona = next((p for p in PERSONAS if p.id == pid), None)
        weight = (persona.weight if persona else 1.0) * persona_conf
        weighted_counts[etype] = weighted_counts.get(etype, 0.0) + weight
        evidence_by_type.setdefault(etype, []).append(pid)

    # 降順
    sorted_types = sorted(weighted_counts.items(), key=lambda kv: kv[1], reverse=True)
    total = sum(weighted_counts.values())
    if total <= 0:
        return ()

    top3 = sorted_types[:3]
    out: list[Hypothesis] = []
    for etype, w in top3:
        prob = w / total
        evidence = tuple(evidence_by_type.get(etype, []))
        out.append(Hypothesis(
            element_type=etype,
            probability=prob,
            evidence=evidence,
            supporting_personas=evidence,
        ))
    return tuple(out)


def merge_persona_results_to_probabilistic(
    persona_results: dict[str, BuildingModel],
    *,
    metadata: Optional[DrawingMetadata] = None,
) -> ProbabilisticBuildingModel:
    """multi_persona の 7 results を top-3 確率分布に圧縮.

    multi_persona_inference._aggregate_votes は majority のみ採用するが、
    このフェーズでは top-3 全部を保持する (確率分布として).
    """
    # vote map を再構築
    votes: dict[str, ElementVote] = {}
    canonical_to_repr: dict[str, BuildingElement] = {}
    for pid, model in persona_results.items():
        if model is None:
            continue
        for elem in model.elements:
            cid = _spatial_canonical_id(elem)
            if cid not in votes:
                votes[cid] = ElementVote(canonical_id=cid, element_type=elem.element_type)
                canonical_to_repr[cid] = elem
            persona_conf = (
                elem.inference.confidence
                if elem.inference and elem.inference.confidence is not None
                else 0.5
            )
            votes[cid].votes.append((pid, elem.element_type, persona_conf))

    elements: list[ProbabilisticElement] = []
    for cid, v in votes.items():
        if v.n_votes < 3:  # quorum
            continue
        top3 = _compute_top3_from_vote(v)
        if not top3:
            continue
        elements.append(ProbabilisticElement(
            canonical_id=cid,
            representative=canonical_to_repr[cid],
            hypotheses=top3,
        ))

    seed_meta = metadata or (
        next(iter(persona_results.values())).metadata if persona_results else DrawingMetadata()
    )
    return ProbabilisticBuildingModel(metadata=seed_meta, elements=tuple(elements))


# ─── ベイズ更新 (user feedback driven) ──────────────────────

@dataclass(frozen=True)
class UserFeedback:
    """ユーザの修正フィードバック."""
    canonical_id: str
    observed_type: ElementType
    strength: float  # 0.0-1.0 — 「絶対こう」=1.0, 「多分こう」=0.5
    user: str = "unknown"
    timestamp: str = ""


def _bayesian_update_hypothesis(
    hypotheses: tuple[Hypothesis, ...],
    feedback: UserFeedback,
    *,
    likelihood_correct: float = 0.95,
    likelihood_wrong: float = 0.05,
) -> tuple[Hypothesis, ...]:
    """ユーザ feedback でベイズ更新.

    P(type | feedback) ∝ P(feedback | type) × P(type)
      P(feedback | observed_type) = likelihood_correct × strength + (1-strength) × likelihood_wrong
      P(feedback | other_type) = likelihood_wrong × strength + (1-strength) × likelihood_correct

    feedback strength 1.0 → 観察 type の確率を強く押上
    strength 0.5 → 観察 type 寄りに微調整
    """
    if not hypotheses:
        return hypotheses
    # prior probs
    priors = {h.element_type: h.probability for h in hypotheses}

    # 観察 type が hypotheses にない場合 → 新規 entry を 0 prior で追加
    if feedback.observed_type not in priors:
        priors[feedback.observed_type] = 0.0

    # likelihood
    likelihoods: dict[ElementType, float] = {}
    for etype in priors:
        if etype == feedback.observed_type:
            l = likelihood_correct * feedback.strength + (1 - feedback.strength) * likelihood_wrong
        else:
            l = likelihood_wrong * feedback.strength + (1 - feedback.strength) * likelihood_correct
        likelihoods[etype] = l

    # 確率を再正規化
    posteriors_unnorm = {et: priors[et] * likelihoods[et] for et in priors}
    # 0 prior の type は posterior も 0 になり消える → 観察 type が 0 prior の場合も
    # 確実に survive させるため、観察 type に小さな floor (0.01) を確保
    if posteriors_unnorm.get(feedback.observed_type, 0.0) <= 0.0:
        posteriors_unnorm[feedback.observed_type] = 0.01 * likelihood_correct
    total = sum(posteriors_unnorm.values())
    if total <= 0:
        return hypotheses
    posteriors = {et: v / total for et, v in posteriors_unnorm.items()}

    # 元 hypotheses の evidence を保持しつつ、新確率で 上書き / 追加
    evidence_by_type: dict[ElementType, tuple[str, ...]] = {
        h.element_type: h.evidence for h in hypotheses
    }
    supporting_by_type: dict[ElementType, tuple[str, ...]] = {
        h.element_type: h.supporting_personas for h in hypotheses
    }
    if feedback.observed_type not in evidence_by_type:
        evidence_by_type[feedback.observed_type] = ()
        supporting_by_type[feedback.observed_type] = ()
    # feedback evidence を観察 type に追加
    feedback_tag = f"user_feedback:{feedback.user}@{feedback.timestamp}"
    evidence_by_type[feedback.observed_type] = evidence_by_type[feedback.observed_type] + (feedback_tag,)

    # top-3 を確率順に再構築
    sorted_types = sorted(posteriors.items(), key=lambda kv: kv[1], reverse=True)
    out: list[Hypothesis] = []
    for etype, prob in sorted_types[:3]:
        out.append(Hypothesis(
            element_type=etype,
            probability=prob,
            evidence=evidence_by_type.get(etype, ()),
            supporting_personas=supporting_by_type.get(etype, ()),
        ))
    return tuple(out)


def update_with_feedback(
    model: ProbabilisticBuildingModel,
    feedback: UserFeedback,
) -> ProbabilisticBuildingModel:
    """単一 feedback でモデルを更新 (ベイズ posterior).

    target element が見つからなければ no-op.
    """
    new_elements: list[ProbabilisticElement] = []
    for pe in model.elements:
        if pe.canonical_id != feedback.canonical_id:
            new_elements.append(pe)
            continue
        new_hypotheses = _bayesian_update_hypothesis(pe.hypotheses, feedback)
        new_elements.append(replace(pe, hypotheses=new_hypotheses))
    return replace(model, elements=tuple(new_elements))


def apply_feedback_batch(
    model: ProbabilisticBuildingModel,
    feedbacks: tuple[UserFeedback, ...],
) -> ProbabilisticBuildingModel:
    """複数 feedback を順次適用 (順序依存に注意 — 通常は時系列順)."""
    current = model
    for fb in feedbacks:
        current = update_with_feedback(current, fb)
    return current
