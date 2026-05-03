"""Pack #83 Phase 3 — 物理シミュレーション自己検証 (revolution ④).

CEO 直接指示「圧倒的な精度」「今までになかった視点」革新 ④ 実装:

  「抽出した 3D を物理計算 (重力負荷) で検証. 不安定 = 構造的に不可能 =
   抽出ミス候補. 物理法則を ground truth に.」

設計:
  既存 gravity_engine.py が core (自重 / 累積荷重 / 柱容量) を提供.
  本モジュールは 4 つの **物理的不可能性 check** を orchestrate:

  1. 荷重経路連続性 (load path continuity)
     屋根 → 梁 → 柱 → 基礎 が途切れない (relationship_validator と連動)
  2. 柱軸耐力 (column axial capacity)
     gravity_engine.verify_column_capacity を呼出
  3. 細長比 (column slenderness)
     座屈危険 = 柱高 / 断面 > λ_max
  4. 静的釣合 (static equilibrium)
     各支持点で 総鉛直荷重 = 反力 (簡易: 全建物重量を基礎で受ける)

  これらが NG = 「抽出した 3D は物理的に不可能」 = VLM が誤推論
  → 候補仮説を再評価 (Phase 2 probabilistic_bim と連動可能)

将来 (Phase 4):
  anaStruct (2D frame) / COMPAS FEA2 (3D FEA) で実 FEM 解析.
  本フェーズは pure-python heuristic で十分検証可能 (依存追加なし).
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from enum import Enum
from typing import Optional

from plugins.conquest.gravity_engine import (
    compute_self_weight,
    accumulate_gravity_loads,
    run_gravity_analysis,
    verify_column_capacity,
)
from plugins.conquest.knowledge_base import get_material_properties
from plugins.conquest.schemas import (
    BuildingElement,
    BuildingModel,
    ElementType,
    GravityLoad,
    MaterialType,
    StructuralRole,
)

logger = logging.getLogger("conquest.physics_self_validator")


# ─── 物理不可能性 issue 表現 ──────────────────────────────────

class PhysicsIssueType(Enum):
    LOAD_PATH_DISCONTINUITY = "load_path_discontinuity"
    COLUMN_OVER_STRESSED = "column_over_stressed"
    COLUMN_SLENDERNESS_EXCEEDED = "column_slenderness_exceeded"
    EQUILIBRIUM_VIOLATION = "equilibrium_violation"
    NEGATIVE_DIMENSION = "negative_dimension"
    UNREASONABLE_SPAN = "unreasonable_span"


class PhysicsSeverity(Enum):
    """物理的重大度. CRITICAL = 物理的に不可能, WARNING = 設計疑義, INFO = 確認推奨."""
    CRITICAL = "critical"
    WARNING = "warning"
    INFO = "info"


@dataclass(frozen=True)
class PhysicsIssue:
    issue_type: PhysicsIssueType
    severity: PhysicsSeverity
    element_id: str
    description: str
    measured_value: float = 0.0
    threshold_value: float = 0.0
    recommendation: str = ""


@dataclass(frozen=True)
class PhysicsValidationResult:
    """物理 self-validation の総合結果."""
    issues: tuple[PhysicsIssue, ...] = ()
    total_weight_kn: float = 0.0
    overall_passed: bool = False
    critical_count: int = 0
    warning_count: int = 0
    info_count: int = 0

    @property
    def physical_impossibility_count(self) -> int:
        """CRITICAL = 物理的に不可能な抽出 (= VLM 誤推論候補)."""
        return self.critical_count


# ─── 4 つの check ────────────────────────────────────────────

def _check_negative_dimensions(model: BuildingModel) -> list[PhysicsIssue]:
    """負・ゼロ寸法の element を検出.

    VLM が異常な座標を生成するパターンの early detection.
    """
    out: list[PhysicsIssue] = []
    for e in model.elements:
        if e.thickness <= 0:
            out.append(PhysicsIssue(
                issue_type=PhysicsIssueType.NEGATIVE_DIMENSION,
                severity=PhysicsSeverity.CRITICAL,
                element_id=e.id,
                description=f"thickness が非正値: {e.thickness} mm",
                measured_value=e.thickness,
                threshold_value=1.0,  # > 0
                recommendation="VLM 抽出を再実行 (寸法欠落 / OCR 誤読の可能性)",
            ))
        if e.height <= 0 and e.element_type in {ElementType.COLUMN, ElementType.WALL}:
            out.append(PhysicsIssue(
                issue_type=PhysicsIssueType.NEGATIVE_DIMENSION,
                severity=PhysicsSeverity.CRITICAL,
                element_id=e.id,
                description=f"height が非正値: {e.height} mm ({e.element_type.value})",
                measured_value=e.height,
                threshold_value=1.0,
                recommendation="階高情報の抽出を再確認",
            ))
    return out


def _check_column_slenderness(model: BuildingModel) -> list[PhysicsIssue]:
    """柱の細長比 (slenderness ratio) λ = effective_length / radius_of_gyration.

    簡易: λ_eff = 階高 / (thickness/√12)
    RC λ_max = 100, S λ_max = 200, W λ_max = 150
    超過 = 座屈の危険 = 物理的に不可能設計.
    """
    out: list[PhysicsIssue] = []
    LIMITS: dict[MaterialType, float] = {
        MaterialType.RC: 100.0,
        MaterialType.SRC: 100.0,
        MaterialType.S: 200.0,
        MaterialType.W: 150.0,
        MaterialType.CB: 80.0,
    }
    for e in model.elements:
        if e.element_type != ElementType.COLUMN:
            continue
        if e.thickness <= 0 or e.height <= 0:
            continue
        radius_gyration = e.thickness / (12 ** 0.5)  # 矩形断面の i = b/√12
        if radius_gyration <= 0:
            continue
        slenderness = e.height / radius_gyration
        limit = LIMITS.get(e.material, 100.0)
        if slenderness > limit:
            severity = (
                PhysicsSeverity.CRITICAL if slenderness > limit * 1.5
                else PhysicsSeverity.WARNING
            )
            out.append(PhysicsIssue(
                issue_type=PhysicsIssueType.COLUMN_SLENDERNESS_EXCEEDED,
                severity=severity,
                element_id=e.id,
                description=(
                    f"柱 {e.id} 細長比 λ={slenderness:.0f} が "
                    f"許容値 {limit:.0f} ({e.material.value}) を超過"
                ),
                measured_value=slenderness,
                threshold_value=limit,
                recommendation=(
                    "柱断面拡大 or 中間支持 (梁) 追加. "
                    "極度な超過は VLM が thickness を過小推定した可能性"
                ),
            ))
    return out


def _check_unreasonable_spans(model: BuildingModel) -> list[PhysicsIssue]:
    """梁スパンが現実的範囲を超える検出.

    RC: ≤ 12 m, S: ≤ 20 m (一般), W: ≤ 6 m
    超過 = transfer beam / 特殊構造の可能性 → 確認推奨.
    """
    out: list[PhysicsIssue] = []
    SPAN_LIMITS: dict[MaterialType, float] = {
        MaterialType.RC: 12_000.0,
        MaterialType.SRC: 15_000.0,
        MaterialType.S: 20_000.0,
        MaterialType.W: 6_000.0,
        MaterialType.CB: 4_000.0,
    }
    for e in model.elements:
        if e.element_type != ElementType.BEAM:
            continue
        span = ((e.end.x - e.start.x) ** 2 + (e.end.y - e.start.y) ** 2) ** 0.5
        limit = SPAN_LIMITS.get(e.material, 12_000.0)
        if span > limit:
            out.append(PhysicsIssue(
                issue_type=PhysicsIssueType.UNREASONABLE_SPAN,
                severity=PhysicsSeverity.WARNING,
                element_id=e.id,
                description=(
                    f"梁 {e.id} スパン {span / 1000:.1f} m が "
                    f"標準値 {limit / 1000:.0f} m ({e.material.value}) を超過"
                ),
                measured_value=span,
                threshold_value=limit,
                recommendation=(
                    "transfer beam / トラス / 特殊構造でないか確認. "
                    "VLM がスパン途中の中間柱を見落とした可能性"
                ),
            ))
    return out


def _check_columns_via_gravity_engine(
    analyzed_model: BuildingModel,
) -> list[PhysicsIssue]:
    """gravity_engine が解析後のモデルから柱過大荷重を検出.

    accumulate_gravity_loads + verify_column_capacity の結果を解釈.
    """
    out: list[PhysicsIssue] = []
    for e in analyzed_model.elements:
        if e.element_type != ElementType.COLUMN:
            continue
        cumulative = e.gravity_load.cumulative_load_kn
        ok = verify_column_capacity(e, cumulative)
        if not ok:
            out.append(PhysicsIssue(
                issue_type=PhysicsIssueType.COLUMN_OVER_STRESSED,
                severity=PhysicsSeverity.CRITICAL,
                element_id=e.id,
                description=(
                    f"柱 {e.id} 累積荷重 {cumulative:.1f} kN が軸耐力を超過"
                ),
                measured_value=cumulative,
                threshold_value=0.0,
                recommendation="断面拡大 or 荷重経路再評価. VLM 誤抽出の可能性",
            ))
    return out


def _check_load_path_continuity(model: BuildingModel) -> list[PhysicsIssue]:
    """屋根 → 梁 → 柱 → 基礎 までのチェーンを階レベルで確認.

    relationship_validator.LOAD_PATH_BREAK と独立に物理視点で再検証.
    """
    out: list[PhysicsIssue] = []
    by_storey_type: dict[int, dict[ElementType, int]] = {}
    for e in model.elements:
        s = e.storey or 0
        by_storey_type.setdefault(s, {})
        by_storey_type[s][e.element_type] = by_storey_type[s].get(e.element_type, 0) + 1

    has_foundation = any(
        ElementType.FOUNDATION in d for d in by_storey_type.values()
    )
    has_columns = any(
        ElementType.COLUMN in d for d in by_storey_type.values()
    )
    has_roof = any(
        ElementType.ROOF in d for d in by_storey_type.values()
    )
    has_beam_or_slab = any(
        (ElementType.BEAM in d or ElementType.SLAB in d)
        for d in by_storey_type.values()
    )

    if has_columns and not has_foundation:
        out.append(PhysicsIssue(
            issue_type=PhysicsIssueType.LOAD_PATH_DISCONTINUITY,
            severity=PhysicsSeverity.CRITICAL,
            element_id="GLOBAL",
            description="柱は存在するが基礎が抽出されていない — 重力経路が地盤に届かない",
            recommendation="基礎 / フーチングの抽出を VLM 再実行",
        ))
    if has_roof and not has_beam_or_slab:
        out.append(PhysicsIssue(
            issue_type=PhysicsIssueType.LOAD_PATH_DISCONTINUITY,
            severity=PhysicsSeverity.WARNING,
            element_id="GLOBAL",
            description="屋根は存在するが梁/スラブが不在 — 屋根荷重が柱に伝達されない",
            recommendation="水平構造 (梁/スラブ) の抽出を確認",
        ))
    return out


def _check_equilibrium(analyzed_model: BuildingModel) -> list[PhysicsIssue]:
    """簡易静的釣合: 全建物重量 = 基礎反力合計 (仮定).

    基礎が存在しない場合 = 反力ゼロ → 釣合違反 (CRITICAL).
    """
    out: list[PhysicsIssue] = []
    total_weight = sum(
        e.gravity_load.self_weight_kn for e in analyzed_model.elements
    )
    foundations = [
        e for e in analyzed_model.elements
        if e.element_type == ElementType.FOUNDATION
    ]
    if total_weight > 0 and not foundations:
        out.append(PhysicsIssue(
            issue_type=PhysicsIssueType.EQUILIBRIUM_VIOLATION,
            severity=PhysicsSeverity.CRITICAL,
            element_id="GLOBAL",
            description=(
                f"建物自重 {total_weight:.1f} kN を支持する基礎が抽出されていない — "
                f"静的釣合不成立"
            ),
            measured_value=total_weight,
            threshold_value=0.0,
            recommendation="基礎抽出を再実行. 図面に基礎が描かれていない場合は地中梁/フーチング推論を促す prompt 強化",
        ))
    return out


# ─── orchestrator ──────────────────────────────────────────

def validate_with_physics(model: BuildingModel) -> tuple[BuildingModel, PhysicsValidationResult]:
    """全 6 種の物理 check を実行.

    Args:
        model: 抽出された BuildingModel (Phase 1 / Phase 2 のいずれかの出力)

    Returns:
        (gravity_analyzed_model, PhysicsValidationResult)
    """
    issues: list[PhysicsIssue] = []

    # 1. 寸法チェック (gravity_engine 不要)
    issues.extend(_check_negative_dimensions(model))
    issues.extend(_check_column_slenderness(model))
    issues.extend(_check_unreasonable_spans(model))
    issues.extend(_check_load_path_continuity(model))

    # 2. gravity_engine で重力解析 (自重・累積荷重・柱容量)
    try:
        analyzed = run_gravity_analysis(model)
    except Exception as e:
        logger.error("gravity_analysis failed: %s", e)
        analyzed = model

    # 3. 解析結果から物理 check
    issues.extend(_check_columns_via_gravity_engine(analyzed))
    issues.extend(_check_equilibrium(analyzed))

    crit = sum(1 for i in issues if i.severity == PhysicsSeverity.CRITICAL)
    warn = sum(1 for i in issues if i.severity == PhysicsSeverity.WARNING)
    info = sum(1 for i in issues if i.severity == PhysicsSeverity.INFO)

    total_weight = (
        analyzed.total_building_weight_kn
        if hasattr(analyzed, "total_building_weight_kn") and analyzed.total_building_weight_kn
        else sum(e.gravity_load.self_weight_kn for e in analyzed.elements)
    )

    result = PhysicsValidationResult(
        issues=tuple(issues),
        total_weight_kn=total_weight,
        overall_passed=(crit == 0),
        critical_count=crit,
        warning_count=warn,
        info_count=info,
    )

    logger.info(
        "physics validation: weight=%.1f kN, critical=%d warning=%d info=%d, passed=%s",
        total_weight, crit, warn, info, result.overall_passed,
    )
    return analyzed, result


def format_physics_report(result: PhysicsValidationResult) -> str:
    """人が読める物理 report (CTO の自己視覚 review 用)."""
    lines: list[str] = []
    lines.append("=" * 60)
    lines.append("Conquest Physics Self-Validation Report")
    lines.append("=" * 60)
    lines.append(f"建物自重: {result.total_weight_kn:.1f} kN")
    lines.append(
        f"判定: {'✅ 物理的に妥当' if result.overall_passed else '❌ 物理的に不可能 (CRITICAL あり)'}"
    )
    lines.append(
        f"内訳: CRITICAL {result.critical_count} / WARNING {result.warning_count} "
        f"/ INFO {result.info_count}"
    )
    lines.append("")
    if not result.issues:
        lines.append("(問題なし)")
        return "\n".join(lines)
    lines.append("--- Issues (severity 順) ---")
    severity_order = {
        PhysicsSeverity.CRITICAL: 0, PhysicsSeverity.WARNING: 1, PhysicsSeverity.INFO: 2,
    }
    for issue in sorted(result.issues, key=lambda i: severity_order[i.severity]):
        marker = {"critical": "❌", "warning": "⚠️ ", "info": "ℹ️ "}[issue.severity.value]
        lines.append(f"{marker} [{issue.issue_type.value}] {issue.element_id}: {issue.description}")
        if issue.recommendation:
            lines.append(f"   → {issue.recommendation}")
    return "\n".join(lines)
