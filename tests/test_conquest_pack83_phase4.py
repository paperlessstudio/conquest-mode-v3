"""Pack #83 Phase 4 — Pipeline v3 + accuracy benchmark tests."""
from __future__ import annotations

import pytest

from plugins.conquest.pipeline_v3 import (
    ConquestPipelineResult,
    run_pipeline_from_persona_results,
    format_pipeline_summary,
    _compute_overall_confidence,
)
from plugins.conquest.accuracy_benchmark import (
    BenchmarkResult,
    BenchmarkSuite,
    benchmark,
    run_suite,
    format_suite_report,
)
from plugins.conquest.probabilistic_bim import (
    ProbabilisticBuildingModel,
    ProbabilisticElement,
    Hypothesis,
    UserFeedback,
)
from plugins.conquest.physics_self_validator import (
    PhysicsValidationResult,
    PhysicsIssue,
    PhysicsIssueType,
    PhysicsSeverity,
)
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


def _wall(eid="w1", start=(0, 0), end=(10000, 0)):
    return BuildingElement(
        id=eid, element_type=ElementType.WALL, name=eid,
        start=Point2D(*start), end=Point2D(*end),
        thickness=200.0, height=2700.0, storey=1,
        material=MaterialType.RC,
        structural_role=StructuralRole.LOAD_BEARING,
        inference=InferenceMetadata(extraction_method=ExtractionMethod.VISUAL, confidence=1.0),
    )


def _column(eid="c1", pos=(0, 0)):
    return BuildingElement(
        id=eid, element_type=ElementType.COLUMN, name=eid,
        start=Point2D(*pos), end=Point2D(*pos),
        thickness=600.0, height=2700.0, storey=1,
        material=MaterialType.RC,
        structural_role=StructuralRole.LOAD_BEARING,
        inference=InferenceMetadata(extraction_method=ExtractionMethod.VISUAL, confidence=1.0),
    )


def _foundation(eid="f1", start=(-500, 0), end=(10500, 0)):
    return BuildingElement(
        id=eid, element_type=ElementType.FOUNDATION, name=eid,
        start=Point2D(*start), end=Point2D(*end),
        thickness=800.0, height=400.0, storey=0,
        material=MaterialType.RC,
        structural_role=StructuralRole.FOUNDATION_FOOTING,
        inference=InferenceMetadata(extraction_method=ExtractionMethod.VISUAL, confidence=1.0),
    )


def _model(elements):
    return BuildingModel(metadata=DrawingMetadata(), elements=tuple(elements), spaces=())


# ─── Pipeline v3 ─────────────────────────────────────────────

def test_pipeline_clean_model_physics_passes():
    """壁 + 柱 + 基礎 の model → physics CRITICAL=0 (relationship は別軸).

    distribution_ready の合否は relationship_validator の load_path/floating
    threshold に依存する (柱-基礎の近接判定 etc) ため、本テストは physics 単独で判定.
    """
    elements = [
        _wall("w1", start=(0, 0), end=(10000, 0)),
        _column("c1", pos=(0, 0)),
        _column("c2", pos=(10000, 0)),
        _foundation("f1", start=(-500, 0), end=(10500, 0)),
    ]
    persona_results = {pid: _model(elements) for pid in ["archi", "structure", "qs", "code"]}
    result = run_pipeline_from_persona_results(persona_results, confidence_threshold=0.85)
    assert result.physics.critical_count == 0
    assert result.overall_confidence >= 0.85
    # rel critical は relationship_validator の load_path 厳格判定で付くことがあるが、
    # physics はクリーン
    assert result.physics.overall_passed is True


def test_pipeline_floating_column_blocks_distribution():
    """基礎なし柱 → distribution_ready=False, blocking_issues あり."""
    persona_results = {
        pid: _model([_column("c1")]) for pid in ["archi", "structure", "qs"]
    }
    result = run_pipeline_from_persona_results(persona_results)
    assert result.distribution_ready is False
    assert len(result.blocking_issues) > 0
    assert any("physics" in b for b in result.blocking_issues)


def test_pipeline_with_user_feedback_increases_observed_probability():
    """user feedback で観察 type の確率が上昇する.

    全員一致 (prior 1.0) からは 1 回の feedback で top-1 入替は起きないが、
    観察 type の確率は確実に増加する (ベイズ単調性).
    """
    persona_results = {
        pid: _model([_wall()]) for pid in ["archi", "structure", "qs"]
    }
    pmodel_before = run_pipeline_from_persona_results(persona_results)
    pe_before = pmodel_before.probabilistic.elements[0]
    cid_initial = pe_before.canonical_id
    # 元 hypotheses は WALL のみ (prior 1.0)
    fb = UserFeedback(
        canonical_id=cid_initial,
        observed_type=ElementType.BEAM,
        strength=1.0,
        user="tester",
    )
    result = run_pipeline_from_persona_results(persona_results, feedbacks=(fb,))
    pe_after = result.probabilistic.elements[0]
    beam_after = next(
        (h for h in pe_after.hypotheses if h.element_type == ElementType.BEAM), None
    )
    # BEAM hypothesis が新たに追加されている
    assert beam_after is not None
    assert beam_after.probability > 0.0


def test_format_pipeline_summary_contains_verdict():
    persona_results = {pid: _model([_column()]) for pid in ["archi", "structure", "qs"]}
    result = run_pipeline_from_persona_results(persona_results)
    text = format_pipeline_summary(result)
    assert "Conquest Mode v3" in text
    assert ("READY" in text) or ("NOT READY" in text)


def test_overall_confidence_drops_with_critical():
    """critical issues が多いほど overall_confidence は下がる."""
    pmodel_high = ProbabilisticBuildingModel(
        elements=(
            ProbabilisticElement(
                canonical_id="x",
                representative=_wall(),
                hypotheses=(Hypothesis(ElementType.WALL, 1.0),),
            ),
        ),
    )
    physics_clean = PhysicsValidationResult(
        issues=(), total_weight_kn=100.0, overall_passed=True,
        critical_count=0, warning_count=0, info_count=0,
    )
    physics_critical = PhysicsValidationResult(
        issues=(
            PhysicsIssue(
                issue_type=PhysicsIssueType.EQUILIBRIUM_VIOLATION,
                severity=PhysicsSeverity.CRITICAL,
                element_id="GLOBAL", description="x",
            ),
        ),
        total_weight_kn=100.0, overall_passed=False,
        critical_count=1, warning_count=0, info_count=0,
    )
    conf_clean = _compute_overall_confidence(pmodel_high, physics_clean)
    conf_critical = _compute_overall_confidence(pmodel_high, physics_critical)
    assert conf_clean > conf_critical


# ─── accuracy_benchmark ─────────────────────────────────────

def test_benchmark_perfect_match_f1_1():
    elements = [_wall(), _column()]
    gt = _model(elements)
    extracted = _model(elements)
    r = benchmark("perfect", gt, extracted)
    assert r.f1 == 1.0
    assert r.precision == 1.0
    assert r.recall == 1.0


def test_benchmark_missed_element_recall_low():
    gt = _model([_wall("w1"), _wall("w2", start=(0, 5000), end=(10000, 5000))])
    extracted = _model([_wall("w1")])  # w2 抽出失敗
    r = benchmark("missed", gt, extracted)
    assert r.recall == 0.5  # 1/2
    assert r.precision == 1.0
    assert pytest.approx(r.f1, abs=0.01) == 2 * 1.0 * 0.5 / 1.5


def test_benchmark_false_positive_precision_low():
    gt = _model([_wall("w1")])
    extracted = _model([
        _wall("w1"),
        _wall("ghost", start=(20000, 20000), end=(30000, 20000)),
    ])
    r = benchmark("ghost", gt, extracted)
    assert r.recall == 1.0
    assert r.precision == 0.5


def test_benchmark_type_mismatch_lowers_type_accuracy():
    """同じ位置で type が違う場合, type_accuracy < 1.0."""
    gt = _model([_wall("w1", start=(0, 0), end=(10000, 0))])
    # 同位置だが BEAM として抽出
    extracted_elem = BuildingElement(
        id="b1", element_type=ElementType.BEAM, name="b1",
        start=Point2D(0, 0), end=Point2D(10000, 0),
        thickness=400.0, height=600.0, storey=1,
        material=MaterialType.RC, structural_role=StructuralRole.LOAD_BEARING,
        inference=InferenceMetadata(extraction_method=ExtractionMethod.VISUAL, confidence=1.0),
    )
    extracted = _model([extracted_elem])
    r = benchmark("type_swap", gt, extracted)
    assert r.n_true_positive == 1  # spatial match OK
    assert r.type_accuracy == 0.0  # 但し type mismatch


def test_run_suite_aggregates_correctly():
    pairs = (
        ("p", _model([_wall()]), _model([_wall()])),
        ("q", _model([_column()]), _model([_column()])),
    )
    suite = run_suite(pairs, distribution_threshold=0.95)
    assert suite.passed is True
    assert suite.avg_f1 == 1.0


def test_format_suite_report_shape():
    pairs = (
        ("scenario_a", _model([_wall()]), _model([_wall()])),
    )
    suite = run_suite(pairs)
    report = format_suite_report(suite)
    assert "Conquest Accuracy Benchmark" in report
    assert "scenario_a" in report
