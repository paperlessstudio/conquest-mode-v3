"""Pack #83 Phase 1 — Multi-persona inference + Neo4j constraint solver tests.

CEO directive 「品質を上げる」「客観的に厳しく」軸でのテスト.
- 7 ペルソナ定義の整合性
- 投票・合意形成の決定論性 (同一入力 → 同一出力)
- 信頼度 score の単調性 (合意度 ↑ → confidence ↑)
- _spatial_canonical_id の冪等性 (浮動小数点誤差吸収)
- Neo4j env 未設定時の graceful fallback
"""
from __future__ import annotations

import pytest

from plugins.conquest.multi_persona_inference import (
    PERSONAS,
    PersonaSpec,
    ElementVote,
    _spatial_canonical_id,
    _aggregate_votes,
)
from plugins.conquest.neo4j_constraint_solver import (
    PHYSICS_CONSTRAINTS,
    seed_physics_constraints,
    persist_validation_result,
    get_anomaly_history,
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
from plugins.conquest.relationship_validator import (
    AnomalyType,
    RelationshipAnomaly,
    RepairAction,
    Severity,
    ValidationResult,
)


# ─── PERSONAS 定義整合性 ─────────────────────────────────────────

def test_personas_count_is_7():
    """Pack #5/#7 の 7 建築ペルソナと 1:1."""
    assert len(PERSONAS) == 7


def test_personas_unique_ids():
    """重複 id がない."""
    ids = [p.id for p in PERSONAS]
    assert len(set(ids)) == len(ids)


def test_personas_have_required_fields():
    for p in PERSONAS:
        assert isinstance(p, PersonaSpec)
        assert p.id and p.name and p.specialty and p.focus
        assert 0.0 < p.weight <= 1.0


# ─── _spatial_canonical_id 冪等性 ────────────────────────────────

def _make_elem(start: tuple[float, float], end: tuple[float, float],
               etype: ElementType = ElementType.WALL,
               eid: str = "e1", confidence: float = 0.5) -> BuildingElement:
    return BuildingElement(
        id=eid,
        element_type=etype,
        name="test",
        start=Point2D(*start),
        end=Point2D(*end),
        thickness=200.0,
        height=2700.0,
        storey=1,
        material=MaterialType.RC,
        structural_role=StructuralRole.LOAD_BEARING,
        inference=InferenceMetadata(
            extraction_method=ExtractionMethod.VISUAL,
            confidence=confidence,
        ),
    )


def test_canonical_id_idempotent():
    e = _make_elem((100.0, 200.0), (500.0, 200.0))
    cid1 = _spatial_canonical_id(e)
    cid2 = _spatial_canonical_id(e)
    assert cid1 == cid2


def test_canonical_id_grid_tolerant():
    """100mm grid で丸め — 数 mm の誤差は同一 id."""
    e1 = _make_elem((100.0, 200.0), (500.0, 200.0))
    e2 = _make_elem((103.0, 198.0), (502.0, 201.0))
    assert _spatial_canonical_id(e1) == _spatial_canonical_id(e2)


def test_canonical_id_direction_independent():
    """同一線分は逆方向でも同一 id."""
    e1 = _make_elem((100.0, 0.0), (500.0, 0.0))
    e2 = _make_elem((500.0, 0.0), (100.0, 0.0))
    assert _spatial_canonical_id(e1) == _spatial_canonical_id(e2)


# ─── ElementVote 投票ロジック ────────────────────────────────────

def test_element_vote_unanimous_confidence_1():
    v = ElementVote(canonical_id="x", element_type=ElementType.WALL)
    for pid in ["archi", "structure", "mep", "qs", "constmgmt", "code", "bim"]:
        v.votes.append((pid, ElementType.WALL, 0.8))
    assert v.confidence == 1.0
    assert v.majority_type == ElementType.WALL


def test_element_vote_split_4_3():
    v = ElementVote(canonical_id="x", element_type=ElementType.WALL)
    for pid in ["archi", "structure", "mep", "qs"]:
        v.votes.append((pid, ElementType.WALL, 0.8))
    for pid in ["constmgmt", "code", "bim"]:
        v.votes.append((pid, ElementType.BEAM, 0.8))
    # majority = WALL (4 票)
    assert v.majority_type == ElementType.WALL
    assert pytest.approx(v.confidence, abs=0.01) == 4 / 7


def test_element_vote_empty():
    v = ElementVote(canonical_id="x", element_type=ElementType.WALL)
    assert v.confidence == 0.0


# ─── _aggregate_votes 合意形成 ───────────────────────────────────

def _make_model_with(elements: tuple[BuildingElement, ...]) -> BuildingModel:
    return BuildingModel(
        metadata=DrawingMetadata(source_file="test.pdf"),
        elements=elements,
        spaces=(),
    )


def test_aggregate_quorum_below_3_dropped():
    """3 票未満は noise として除外 (4/7 quorum 推奨だが下限 3)."""
    e = _make_elem((0, 0), (1000, 0))
    persona_results = {
        "archi": _make_model_with((e,)),
        "structure": _make_model_with((e,)),
    }
    consensus, conf = _aggregate_votes(persona_results)
    assert consensus == []


def test_aggregate_quorum_3_passes():
    e = _make_elem((0, 0), (1000, 0))
    persona_results = {
        "archi": _make_model_with((e,)),
        "structure": _make_model_with((e,)),
        "qs": _make_model_with((e,)),
    }
    consensus, conf = _aggregate_votes(persona_results)
    assert len(consensus) == 1
    # 3/3 全員 WALL → confidence 1.0
    assert conf[consensus[0].id] == pytest.approx(1.0)


def test_aggregate_with_disagreement():
    """同位置で異なる type の場合、majority のみ採用."""
    e_wall = _make_elem((0, 0), (1000, 0), etype=ElementType.WALL, eid="w")
    e_beam = _make_elem((0, 0), (1000, 0), etype=ElementType.BEAM, eid="b")
    # 4 ペルソナ: WALL, 3 ペルソナ: BEAM
    persona_results = {
        "archi": _make_model_with((e_wall,)),
        "structure": _make_model_with((e_wall,)),
        "qs": _make_model_with((e_wall,)),
        "code": _make_model_with((e_wall,)),
        "constmgmt": _make_model_with((e_beam,)),
        "mep": _make_model_with((e_beam,)),
        "bim": _make_model_with((e_beam,)),
    }
    consensus, conf = _aggregate_votes(persona_results)
    # 同 canonical_id の 1 vote object に統合 → 採用される代表 element は 1 つ
    assert len(consensus) == 1
    assert consensus[0].element_type == ElementType.WALL
    assert 0.5 < conf[consensus[0].id] < 1.0


# ─── PHYSICS_CONSTRAINTS schema 整合性 ──────────────────────────

def test_physics_constraints_have_all_fields():
    required = {"id", "name", "description", "anomaly_type", "severity", "physics_principle"}
    for c in PHYSICS_CONSTRAINTS:
        assert required.issubset(c.keys()), f"missing fields in {c['id']}"


def test_physics_constraints_anomaly_types_valid():
    """各 PhysicsConstraint の anomaly_type は AnomalyType enum と一致."""
    enum_values = {a.value for a in AnomalyType}
    for c in PHYSICS_CONSTRAINTS:
        assert c["anomaly_type"] in enum_values, f"unknown anomaly_type {c['anomaly_type']}"


def test_physics_constraints_severity_values_valid():
    enum_values = {s.value for s in Severity}
    for c in PHYSICS_CONSTRAINTS:
        assert c["severity"] in enum_values, f"unknown severity {c['severity']}"


# ─── Neo4j fallback (offline) ───────────────────────────────────

def test_seed_physics_constraints_offline_returns_zero(monkeypatch):
    monkeypatch.delenv("NEO4J_URI", raising=False)
    monkeypatch.delenv("NEO4J_PASSWORD", raising=False)
    n = seed_physics_constraints()
    assert n == 0  # offline


def test_persist_validation_result_offline(monkeypatch):
    monkeypatch.delenv("NEO4J_URI", raising=False)
    monkeypatch.delenv("NEO4J_PASSWORD", raising=False)
    model = _make_model_with(())
    result = ValidationResult(anomalies=(), repairs=())
    counts = persist_validation_result(model, result)
    assert counts["anomalies"] == 0


def test_get_anomaly_history_offline_returns_empty(monkeypatch):
    monkeypatch.delenv("NEO4J_URI", raising=False)
    monkeypatch.delenv("NEO4J_PASSWORD", raising=False)
    h = get_anomaly_history()
    assert h == []
