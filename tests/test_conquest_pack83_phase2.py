"""Pack #83 Phase 2 — Probabilistic BIM + Bayesian feedback tests."""
from __future__ import annotations

import pytest

from plugins.conquest.probabilistic_bim import (
    Hypothesis,
    ProbabilisticElement,
    ProbabilisticBuildingModel,
    UserFeedback,
    _compute_top3_from_vote,
    _bayesian_update_hypothesis,
    merge_persona_results_to_probabilistic,
    update_with_feedback,
    apply_feedback_batch,
)
from plugins.conquest.multi_persona_inference import ElementVote, _spatial_canonical_id
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


# ─── helpers ─────────────────────────────────────────────────────

def _make_elem(start, end, etype=ElementType.WALL, eid="e1", confidence=0.5):
    return BuildingElement(
        id=eid,
        element_type=etype,
        name="t",
        start=Point2D(*start),
        end=Point2D(*end),
        thickness=200.0,
        height=2700.0,
        storey=1,
        material=MaterialType.RC,
        structural_role=StructuralRole.LOAD_BEARING,
        inference=InferenceMetadata(
            extraction_method=ExtractionMethod.VISUAL, confidence=confidence,
        ),
    )


def _model(elements):
    return BuildingModel(metadata=DrawingMetadata(), elements=tuple(elements), spaces=())


# ─── _compute_top3_from_vote ─────────────────────────────────────

def test_top3_unanimous_returns_one_hypothesis_at_1_0():
    v = ElementVote(canonical_id="x", element_type=ElementType.WALL)
    for pid in ["archi", "structure", "qs", "code", "constmgmt", "mep", "bim"]:
        v.votes.append((pid, ElementType.WALL, 1.0))
    h = _compute_top3_from_vote(v)
    assert len(h) == 1
    assert h[0].element_type == ElementType.WALL
    assert pytest.approx(h[0].probability, abs=0.001) == 1.0


def test_top3_split_4_3_returns_two_hypotheses():
    v = ElementVote(canonical_id="x", element_type=ElementType.WALL)
    for pid in ["archi", "structure", "qs", "code"]:
        v.votes.append((pid, ElementType.WALL, 0.8))
    for pid in ["constmgmt", "mep", "bim"]:
        v.votes.append((pid, ElementType.BEAM, 0.8))
    h = _compute_top3_from_vote(v)
    assert len(h) == 2
    assert h[0].element_type == ElementType.WALL
    assert h[1].element_type == ElementType.BEAM
    assert h[0].probability > h[1].probability
    # 確率合計 = 1.0
    assert pytest.approx(sum(x.probability for x in h), abs=0.001) == 1.0


def test_top3_three_way_split_caps_at_3():
    v = ElementVote(canonical_id="x", element_type=ElementType.WALL)
    v.votes.append(("archi", ElementType.WALL, 1.0))
    v.votes.append(("structure", ElementType.BEAM, 1.0))
    v.votes.append(("qs", ElementType.COLUMN, 1.0))
    v.votes.append(("code", ElementType.SLAB, 1.0))
    v.votes.append(("mep", ElementType.DOOR, 1.0))
    h = _compute_top3_from_vote(v)
    assert len(h) == 3  # cap at 3


def test_top3_empty_vote_returns_empty():
    v = ElementVote(canonical_id="x", element_type=ElementType.WALL)
    h = _compute_top3_from_vote(v)
    assert h == ()


# ─── ProbabilisticElement helpers ────────────────────────────────

def test_probabilistic_element_confidence_is_top1_prob():
    pe = ProbabilisticElement(
        canonical_id="x",
        representative=_make_elem((0, 0), (100, 0)),
        hypotheses=(
            Hypothesis(ElementType.WALL, 0.7),
            Hypothesis(ElementType.BEAM, 0.3),
        ),
    )
    assert pe.confidence == 0.7


def test_probabilistic_element_ambiguous_when_close():
    pe = ProbabilisticElement(
        canonical_id="x",
        representative=_make_elem((0, 0), (100, 0)),
        hypotheses=(
            Hypothesis(ElementType.WALL, 0.55),
            Hypothesis(ElementType.BEAM, 0.45),
        ),
    )
    assert pe.is_ambiguous is True  # 差 0.10 < 0.20


def test_probabilistic_element_not_ambiguous_when_clear():
    pe = ProbabilisticElement(
        canonical_id="x",
        representative=_make_elem((0, 0), (100, 0)),
        hypotheses=(
            Hypothesis(ElementType.WALL, 0.85),
            Hypothesis(ElementType.BEAM, 0.15),
        ),
    )
    assert pe.is_ambiguous is False


# ─── merge_persona_results_to_probabilistic ──────────────────────

def test_merge_unanimous_4_personas_top1_at_1_0():
    e = _make_elem((0, 0), (1000, 0))
    persona_results = {
        "archi": _model([e]),
        "structure": _model([e]),
        "qs": _model([e]),
        "code": _model([e]),
    }
    pmodel = merge_persona_results_to_probabilistic(persona_results)
    assert len(pmodel.elements) == 1
    pe = pmodel.elements[0]
    assert pe.top1.element_type == ElementType.WALL
    assert pytest.approx(pe.top1.probability, abs=0.01) == 1.0


def test_merge_split_creates_top2():
    e_wall = _make_elem((0, 0), (1000, 0), etype=ElementType.WALL, eid="w")
    e_beam = _make_elem((0, 0), (1000, 0), etype=ElementType.BEAM, eid="b")
    persona_results = {
        "archi": _model([e_wall]),
        "structure": _model([e_wall]),
        "qs": _model([e_wall]),
        "code": _model([e_wall]),
        "constmgmt": _model([e_beam]),
        "mep": _model([e_beam]),
        "bim": _model([e_beam]),
    }
    pmodel = merge_persona_results_to_probabilistic(persona_results)
    assert len(pmodel.elements) == 1
    pe = pmodel.elements[0]
    assert len(pe.hypotheses) == 2
    assert pe.top1.element_type == ElementType.WALL
    assert pe.hypotheses[1].element_type == ElementType.BEAM


def test_merge_quorum_below_3_skips():
    e = _make_elem((0, 0), (1000, 0))
    pmodel = merge_persona_results_to_probabilistic({
        "archi": _model([e]),
        "structure": _model([e]),
    })
    assert pmodel.elements == ()


# ─── ベイズ更新 ──────────────────────────────────────────────

def test_bayesian_update_strong_feedback_promotes_observed_to_top1():
    """top-2 だった type が strong feedback で top-1 に."""
    hypos = (
        Hypothesis(ElementType.WALL, 0.7),
        Hypothesis(ElementType.BEAM, 0.3),
    )
    fb = UserFeedback(
        canonical_id="x",
        observed_type=ElementType.BEAM,
        strength=1.0,  # 「絶対 BEAM」
        user="ceo",
    )
    new = _bayesian_update_hypothesis(hypos, fb)
    assert new[0].element_type == ElementType.BEAM
    assert new[0].probability > new[1].probability


def test_bayesian_update_weak_feedback_minor_shift():
    """weak feedback (0.5) で順位入替に至らない."""
    hypos = (
        Hypothesis(ElementType.WALL, 0.7),
        Hypothesis(ElementType.BEAM, 0.3),
    )
    fb = UserFeedback(
        canonical_id="x",
        observed_type=ElementType.BEAM,
        strength=0.5,
        user="ceo",
    )
    new = _bayesian_update_hypothesis(hypos, fb)
    # weak feedback では top-1 が WALL のまま (確率 prior 0.7 が圧倒的に強い)
    # ただし BEAM の確率は微増している
    assert new[0].element_type == ElementType.WALL
    new_beam = next((h for h in new if h.element_type == ElementType.BEAM), None)
    assert new_beam is not None
    assert new_beam.probability >= 0.3 - 0.05  # 大幅減少していない


def test_bayesian_update_unknown_type_added_as_floor():
    """top-3 に存在しない type を observed として与えると新規追加される."""
    hypos = (
        Hypothesis(ElementType.WALL, 0.7),
        Hypothesis(ElementType.BEAM, 0.3),
    )
    fb = UserFeedback(
        canonical_id="x",
        observed_type=ElementType.COLUMN,
        strength=1.0,
        user="ceo",
    )
    new = _bayesian_update_hypothesis(hypos, fb)
    types = [h.element_type for h in new]
    assert ElementType.COLUMN in types


def test_bayesian_probabilities_sum_to_1():
    hypos = (
        Hypothesis(ElementType.WALL, 0.5),
        Hypothesis(ElementType.BEAM, 0.3),
        Hypothesis(ElementType.SLAB, 0.2),
    )
    fb = UserFeedback(canonical_id="x", observed_type=ElementType.BEAM, strength=0.7, user="u")
    new = _bayesian_update_hypothesis(hypos, fb)
    total = sum(h.probability for h in new)
    assert pytest.approx(total, abs=0.01) == 1.0


def test_apply_feedback_batch_idempotent():
    """同 feedback 2 回 → 確率は更に shift するが exception なし."""
    e = _make_elem((0, 0), (1000, 0))
    persona_results = {pid: _model([e]) for pid in ["archi", "structure", "qs"]}
    pmodel = merge_persona_results_to_probabilistic(persona_results)
    cid = pmodel.elements[0].canonical_id
    fb = UserFeedback(canonical_id=cid, observed_type=ElementType.BEAM, strength=0.8, user="ceo")
    after_1 = apply_feedback_batch(pmodel, (fb,))
    after_2 = apply_feedback_batch(pmodel, (fb, fb))
    pe1 = after_1.elements[0]
    pe2 = after_2.elements[0]
    # 2 回目の方が BEAM 確率が更に高い
    beam1 = next((h for h in pe1.hypotheses if h.element_type == ElementType.BEAM), None)
    beam2 = next((h for h in pe2.hypotheses if h.element_type == ElementType.BEAM), None)
    assert beam1 is not None and beam2 is not None
    assert beam2.probability >= beam1.probability


# ─── to_deterministic ────────────────────────────────────────────

def test_to_deterministic_uses_top1():
    e = _make_elem((0, 0), (1000, 0))
    pmodel = merge_persona_results_to_probabilistic(
        {pid: _model([e]) for pid in ["archi", "structure", "qs"]}
    )
    det = pmodel.to_deterministic()
    assert len(det.elements) == 1
    assert det.elements[0].element_type == ElementType.WALL
    assert det.elements[0].inference.confidence > 0.9
