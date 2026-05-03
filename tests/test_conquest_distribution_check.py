"""Pack #83 Phase 4.5 — distribution check + synthetic fixture tests."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent

from plugins.conquest.distribution_fixtures import all_scenarios
from plugins.conquest.multi_persona_inference import _spatial_canonical_id
from plugins.conquest.pipeline_v3 import run_pipeline_from_persona_results
from plugins.conquest.accuracy_benchmark import run_suite


# ─── distribution_fixtures structural tests ─────────────────────

def test_all_scenarios_have_5_distinct():
    scenarios = all_scenarios()
    assert len(scenarios) == 5
    names = [s[0] for s in scenarios]
    assert len(set(names)) == 5


def test_no_canonical_id_collisions_in_ground_truth():
    """各 GT model 内で同 canonical_id の element が 2 つ以上あると
    multi_persona_inference._aggregate_votes が衝突する."""
    for name, gt, _ext in all_scenarios():
        cids = [_spatial_canonical_id(e) for e in gt.elements]
        unique = set(cids)
        assert len(cids) == len(unique), (
            f"{name}: GT 内で canonical_id 衝突 — "
            f"{len(cids) - len(unique)} 個の重複"
        )


def test_each_scenario_has_load_path_complete():
    """slab + beam + column + foundation の 4 種を全 GT が含む
    (relationship_validator load_path_break 回避)."""
    from plugins.conquest.schemas import ElementType

    required = {
        ElementType.SLAB, ElementType.BEAM,
        ElementType.COLUMN, ElementType.FOUNDATION,
    }
    for name, gt, _ext in all_scenarios():
        types = {e.element_type for e in gt.elements}
        missing = required - types
        assert not missing, f"{name}: 不足 element types = {missing}"


def test_each_beam_endpoints_have_columns():
    """unsupported_beam を起こさないため、各梁の両端 1000mm 以内に
    柱が存在する."""
    import math

    for name, gt, _ext in all_scenarios():
        beams = [e for e in gt.elements if e.element_type.value == "beam"]
        cols = [e for e in gt.elements if e.element_type.value == "column"]
        for beam in beams:
            def near(pt):
                return any(
                    math.hypot(pt.x - c.start.x, pt.y - c.start.y) < 1000
                    and c.storey == beam.storey
                    for c in cols
                )
            assert near(beam.start), f"{name}/{beam.id}: start に柱なし"
            assert near(beam.end), f"{name}/{beam.id}: end に柱なし"


# ─── pipeline integration tests ─────────────────────────────────

@pytest.mark.parametrize("scenario_idx", list(range(5)))
def test_each_scenario_pipeline_distribution_ready(scenario_idx):
    """各 scenario 単体で pipeline_v3 が distribution_ready=True を返す."""
    name, _gt, extracted = all_scenarios()[scenario_idx]
    persona_results = {
        pid: extracted
        for pid in ["archi", "structure", "qs", "code"]
    }
    result = run_pipeline_from_persona_results(
        persona_results, confidence_threshold=0.85,
    )
    assert result.physics.critical_count == 0, (
        f"{name}: physics CRITICAL = {result.physics.critical_count}"
    )
    assert result.relationships.critical_count == 0, (
        f"{name}: relationship CRITICAL = {result.relationships.critical_count} "
        f"({[a.description for a in result.relationships.anomalies if a.severity.value == 'critical']})"
    )
    assert result.distribution_ready, (
        f"{name}: not ready — confidence={result.overall_confidence:.2f}, "
        f"blockers={result.blocking_issues[:3]}"
    )


def test_aggregate_suite_passes_threshold():
    """5 scenario aggregate avg_f1 ≥ 0.95."""
    scenarios = all_scenarios()
    suite = run_suite(scenarios, distribution_threshold=0.95)
    assert suite.passed, (
        f"avg_f1={suite.avg_f1:.3f} < 0.95"
    )


# ─── CLI script tests ───────────────────────────────────────────

def test_distribution_check_script_synthetic_passes():
    """scripts/conquest_distribution_check.py --synthetic が exit 0 を返す."""
    script = REPO_ROOT / "scripts" / "conquest_distribution_check.py"
    proc = subprocess.run(
        [sys.executable, str(script), "--synthetic", "--quiet"],
        capture_output=True, text=True, cwd=str(REPO_ROOT), timeout=60,
    )
    assert proc.returncode == 0, (
        f"exit {proc.returncode}\nstdout: {proc.stdout}\nstderr: {proc.stderr}"
    )


def test_distribution_check_script_json_output(tmp_path):
    """--json で結果を JSON file に出力できる."""
    script = REPO_ROOT / "scripts" / "conquest_distribution_check.py"
    out = tmp_path / "result.json"
    proc = subprocess.run(
        [sys.executable, str(script), "--synthetic", "--quiet", "--json", str(out)],
        capture_output=True, text=True, cwd=str(REPO_ROOT), timeout=60,
    )
    assert proc.returncode == 0
    data = json.loads(out.read_text())
    assert data["mode"] == "synthetic"
    assert data["passed"] is True
    assert data["n_scenarios"] == 5
    assert "suite" in data
    assert data["suite"]["avg_f1"] >= 0.95


def test_distribution_check_script_no_args_errors():
    """--synthetic も --pdf-dir も無いと exit 2."""
    script = REPO_ROOT / "scripts" / "conquest_distribution_check.py"
    proc = subprocess.run(
        [sys.executable, str(script)],
        capture_output=True, text=True, cwd=str(REPO_ROOT), timeout=10,
    )
    assert proc.returncode == 2


def test_distribution_check_script_pdf_dir_not_implemented(tmp_path):
    """--pdf-dir は Phase 5 — 現状 exit 2."""
    script = REPO_ROOT / "scripts" / "conquest_distribution_check.py"
    proc = subprocess.run(
        [sys.executable, str(script), "--pdf-dir", str(tmp_path)],
        capture_output=True, text=True, cwd=str(REPO_ROOT), timeout=10,
    )
    assert proc.returncode == 2


# ─── canonical_id storey fix regression ─────────────────────────

def test_canonical_id_includes_storey():
    """柱 (storey=1) と footing (storey=0) が同 xy にあっても別 canonical_id."""
    from plugins.conquest.schemas import (
        BuildingElement, ElementType, ExtractionMethod,
        InferenceMetadata, MaterialType, Point2D, StructuralRole,
    )

    col = BuildingElement(
        id="c", element_type=ElementType.COLUMN, name="c",
        start=Point2D(0, 0), end=Point2D(0, 0),
        thickness=600.0, height=2700.0, storey=1,
        material=MaterialType.RC, structural_role=StructuralRole.LOAD_BEARING,
        inference=InferenceMetadata(extraction_method=ExtractionMethod.VISUAL),
    )
    foot = BuildingElement(
        id="f", element_type=ElementType.FOUNDATION, name="f",
        start=Point2D(0, 0), end=Point2D(0, 0),
        thickness=1500.0, height=400.0, storey=0,
        material=MaterialType.RC, structural_role=StructuralRole.FOUNDATION_FOOTING,
        inference=InferenceMetadata(extraction_method=ExtractionMethod.VISUAL),
    )
    assert _spatial_canonical_id(col) != _spatial_canonical_id(foot), (
        "storey 違いで canonical_id が同じ → multi-persona 投票で footing が消失"
    )
