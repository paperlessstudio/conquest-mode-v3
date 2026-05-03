"""Pack #83 Phase 3 — Physics self-validator tests."""
from __future__ import annotations

import pytest

from plugins.conquest.physics_self_validator import (
    PhysicsIssue,
    PhysicsIssueType,
    PhysicsSeverity,
    PhysicsValidationResult,
    _check_negative_dimensions,
    _check_column_slenderness,
    _check_unreasonable_spans,
    _check_load_path_continuity,
    _check_equilibrium,
    validate_with_physics,
    format_physics_report,
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
    GravityLoad,
)


# ─── helpers ─────────────────────────────────────────────────────

def _wall(eid="w", thickness=200.0, height=2700.0, start=(0, 0), end=(1000, 0),
          mat=MaterialType.RC):
    return BuildingElement(
        id=eid, element_type=ElementType.WALL, name=eid,
        start=Point2D(*start), end=Point2D(*end),
        thickness=thickness, height=height, storey=1,
        material=mat, structural_role=StructuralRole.LOAD_BEARING,
        inference=InferenceMetadata(extraction_method=ExtractionMethod.VISUAL, confidence=1.0),
    )


def _column(eid="c", thickness=600.0, height=2700.0, pos=(0, 0), mat=MaterialType.RC):
    return BuildingElement(
        id=eid, element_type=ElementType.COLUMN, name=eid,
        start=Point2D(*pos), end=Point2D(*pos),
        thickness=thickness, height=height, storey=1,
        material=mat, structural_role=StructuralRole.LOAD_BEARING,
        inference=InferenceMetadata(extraction_method=ExtractionMethod.VISUAL, confidence=1.0),
    )


def _beam(eid="b", thickness=400.0, start=(0, 0), end=(8000, 0), mat=MaterialType.RC):
    return BuildingElement(
        id=eid, element_type=ElementType.BEAM, name=eid,
        start=Point2D(*start), end=Point2D(*end),
        thickness=thickness, height=600.0, storey=1,
        material=mat, structural_role=StructuralRole.LOAD_BEARING,
        inference=InferenceMetadata(extraction_method=ExtractionMethod.VISUAL, confidence=1.0),
    )


def _foundation(eid="f", thickness=800.0, start=(0, 0), end=(10000, 0)):
    return BuildingElement(
        id=eid, element_type=ElementType.FOUNDATION, name=eid,
        start=Point2D(*start), end=Point2D(*end),
        thickness=thickness, height=400.0, storey=0,
        material=MaterialType.RC,
        structural_role=StructuralRole.FOUNDATION_FOOTING,
        inference=InferenceMetadata(extraction_method=ExtractionMethod.VISUAL, confidence=1.0),
    )


def _model(elements):
    return BuildingModel(metadata=DrawingMetadata(), elements=tuple(elements), spaces=())


# ─── _check_negative_dimensions ─────────────────────────────────

def test_negative_thickness_critical():
    e = _wall(thickness=-100.0)
    issues = _check_negative_dimensions(_model([e]))
    assert len(issues) == 1
    assert issues[0].severity == PhysicsSeverity.CRITICAL
    assert issues[0].issue_type == PhysicsIssueType.NEGATIVE_DIMENSION


def test_zero_height_column_critical():
    e = _column(height=0)
    issues = _check_negative_dimensions(_model([e]))
    # thickness=600 OK だが height=0 でも CRITICAL
    assert any(i.severity == PhysicsSeverity.CRITICAL for i in issues)


def test_normal_dimensions_no_issue():
    e = _wall()
    issues = _check_negative_dimensions(_model([e]))
    assert issues == []


# ─── _check_column_slenderness ──────────────────────────────────

def test_slender_rc_column_critical():
    """RC 200mm 柱で 6000mm = λ ≈ 104 → 限界 100 超 → 微超 = WARNING."""
    e = _column(thickness=200.0, height=6000.0, mat=MaterialType.RC)
    issues = _check_column_slenderness(_model([e]))
    assert len(issues) == 1
    # λ ≈ 104, 限界 100 → 4% 超 → WARNING
    assert issues[0].severity == PhysicsSeverity.WARNING


def test_extremely_slender_column_critical():
    """RC 100mm 柱で 8000mm = λ ≈ 277 > 1.5 × 限界 → CRITICAL."""
    e = _column(thickness=100.0, height=8000.0, mat=MaterialType.RC)
    issues = _check_column_slenderness(_model([e]))
    assert issues[0].severity == PhysicsSeverity.CRITICAL


def test_normal_column_passes():
    e = _column(thickness=600.0, height=2700.0)  # λ ≈ 16
    issues = _check_column_slenderness(_model([e]))
    assert issues == []


def test_zero_thickness_skipped_safely():
    """thickness=0 は negative_dimensions で catch される — slenderness は無視."""
    e = _column(thickness=0.0)
    issues = _check_column_slenderness(_model([e]))
    assert issues == []


# ─── _check_unreasonable_spans ──────────────────────────────────

def test_short_rc_beam_passes():
    e = _beam(start=(0, 0), end=(8000, 0))
    issues = _check_unreasonable_spans(_model([e]))
    assert issues == []


def test_long_rc_beam_warning():
    e = _beam(start=(0, 0), end=(15000, 0))  # 15m > 12m
    issues = _check_unreasonable_spans(_model([e]))
    assert len(issues) == 1
    assert issues[0].severity == PhysicsSeverity.WARNING


def test_steel_long_beam_within_limit():
    e = _beam(start=(0, 0), end=(15000, 0), mat=MaterialType.S)
    issues = _check_unreasonable_spans(_model([e]))
    assert issues == []  # S 限界 20m


# ─── _check_load_path_continuity ────────────────────────────────

def test_columns_without_foundation_critical():
    issues = _check_load_path_continuity(_model([_column()]))
    assert any(
        i.severity == PhysicsSeverity.CRITICAL
        and i.issue_type == PhysicsIssueType.LOAD_PATH_DISCONTINUITY
        for i in issues
    )


def test_columns_with_foundation_passes():
    issues = _check_load_path_continuity(_model([_column(), _foundation()]))
    assert issues == []


# ─── validate_with_physics orchestrator ─────────────────────────

def test_complete_valid_model_passes():
    """壁 + 柱 + 基礎を含む最小妥当モデル → CRITICAL 0."""
    elements = [
        _wall(eid="w1", start=(0, 0), end=(8000, 0)),
        _column(eid="c1", pos=(0, 0)),
        _column(eid="c2", pos=(8000, 0)),
        _foundation(eid="f1", start=(-500, 0), end=(8500, 0)),
    ]
    _, result = validate_with_physics(_model(elements))
    assert result.critical_count == 0


def test_floating_column_model_critical():
    """柱だけ (基礎なし) → CRITICAL ≥ 1."""
    _, result = validate_with_physics(_model([_column()]))
    assert result.critical_count >= 1
    assert not result.overall_passed


def test_format_physics_report_contains_issue_descriptions():
    elements = [_column()]
    _, result = validate_with_physics(_model(elements))
    report = format_physics_report(result)
    assert "Conquest Physics" in report
    assert "❌" in report  # CRITICAL marker


def test_empty_model_overall_passes():
    """空モデルは vacuously true (検証対象なし)."""
    _, result = validate_with_physics(_model([]))
    assert result.overall_passed
    assert result.critical_count == 0


def test_physical_impossibility_count_alias():
    _, result = validate_with_physics(_model([_column()]))
    assert result.physical_impossibility_count == result.critical_count
