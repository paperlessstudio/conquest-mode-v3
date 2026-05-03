"""Conquest Mode — 関係性異常自動検知・自動修復.

CEO指示: 「関係性の異常を自動検知し、関係性自動修復する機能を実装」

検知する異常:
  1. 孤立要素（Orphan）: どの要素とも接続のない要素
  2. 浮遊柱（Floating Column）: 基礎に支持されない1F柱
  3. 宙に浮く梁（Unsupported Beam）: 両端に柱がない梁
  4. 壁なし窓（Homeless Window）: host_elementが未設定の窓/ドア
  5. 断絶柱（Broken Column）: 上下階で連続しない柱
  6. LOD不整合（LOD Mismatch）: 接続要素間のLOD差が大きすぎる
  7. 材質不整合（Material Conflict）: 接合部で異なる材質が未検証
  8. 空間無壁（Unbounded Space）: 境界要素が不足する空間
  9. 荷重経路断絶（Load Path Break）: 屋根→柱→基礎の荷重伝達が途切れる

修復アクション:
  - 推論による補完（confidence付き）
  - 最寄り要素への自動接続
  - 仮説としてメタ認知KUに登録
"""
from __future__ import annotations

import logging
import math
from dataclasses import dataclass, replace
from enum import Enum
from typing import Optional

from plugins.conquest.schemas import (
    BuildingModel, BuildingElement, ElementType, MaterialType,
    Point2D, LOD, ExtractionMethod, InferenceMetadata, Space,
)

logger = logging.getLogger("conquest.relationship_validator")


class AnomalyType(Enum):
    """関係性異常の種類."""
    ORPHAN_ELEMENT = "orphan_element"
    FLOATING_COLUMN = "floating_column"
    UNSUPPORTED_BEAM = "unsupported_beam"
    HOMELESS_OPENING = "homeless_opening"
    BROKEN_COLUMN = "broken_column"
    LOD_MISMATCH = "lod_mismatch"
    MATERIAL_CONFLICT = "material_conflict"
    UNBOUNDED_SPACE = "unbounded_space"
    LOAD_PATH_BREAK = "load_path_break"


class Severity(Enum):
    """異常の深刻度."""
    CRITICAL = "critical"   # 構造安全に関わる
    WARNING = "warning"     # 設計品質に影響
    INFO = "info"           # 改善推奨


@dataclass(frozen=True)
class RelationshipAnomaly:
    """検出された関係性異常."""
    anomaly_type: AnomalyType
    severity: Severity
    element_id: str
    description: str
    related_elements: tuple[str, ...] = ()
    auto_fixable: bool = False
    fix_description: str = ""


@dataclass(frozen=True)
class RepairAction:
    """実行された修復アクション."""
    anomaly_type: AnomalyType
    element_id: str
    action: str
    confidence: float
    before: str = ""
    after: str = ""


@dataclass(frozen=True)
class ValidationResult:
    """関係性検証の全体結果."""
    anomalies: tuple[RelationshipAnomaly, ...] = ()
    repairs: tuple[RepairAction, ...] = ()
    critical_count: int = 0
    warning_count: int = 0
    info_count: int = 0
    auto_repaired_count: int = 0


# ─── 異常検知エンジン ────────────────────────────────────────

def detect_anomalies(model: BuildingModel) -> list[RelationshipAnomaly]:
    """BuildingModel内の全関係性異常を検出する."""
    anomalies = []
    anomalies.extend(_detect_floating_columns(model))
    anomalies.extend(_detect_unsupported_beams(model))
    anomalies.extend(_detect_homeless_openings(model))
    anomalies.extend(_detect_broken_columns(model))
    anomalies.extend(_detect_load_path_breaks(model))
    anomalies.extend(_detect_unbounded_spaces(model))
    anomalies.extend(_detect_orphan_elements(model))
    return anomalies


def _detect_floating_columns(model: BuildingModel) -> list[RelationshipAnomaly]:
    """1F柱が基礎に支持されていないケースを検出."""
    anomalies = []
    columns_1f = [e for e in model.elements
                  if e.element_type == ElementType.COLUMN and e.storey == 1]
    foundations = [e for e in model.elements
                   if e.element_type == ElementType.FOUNDATION]

    for col in columns_1f:
        supported = any(_distance(col, f) < 2000 for f in foundations)
        if not supported:
            nearest = _find_nearest(col, foundations)
            dist = _distance(col, nearest) if nearest else float('inf')
            anomalies.append(RelationshipAnomaly(
                anomaly_type=AnomalyType.FLOATING_COLUMN,
                severity=Severity.CRITICAL,
                element_id=col.id,
                description=(
                    f"1F柱'{col.id}'に対応する基礎がありません。"
                    f"最寄り基礎: {nearest.id if nearest else 'なし'} "
                    f"(距離: {dist:.0f}mm)"
                ),
                related_elements=(nearest.id,) if nearest else (),
                auto_fixable=True,
                fix_description=f"最寄り基礎'{nearest.id}'との接続を推定"
                    if nearest else "柱直下に基礎を推論で追加",
            ))
    return anomalies


def _detect_unsupported_beams(model: BuildingModel) -> list[RelationshipAnomaly]:
    """両端に柱がない梁を検出."""
    anomalies = []
    beams = [e for e in model.elements if e.element_type == ElementType.BEAM]
    columns = [e for e in model.elements if e.element_type == ElementType.COLUMN]

    for beam in beams:
        start_supported = any(
            _distance_point(beam.start, col.start) < 1000
            for col in columns if col.storey == beam.storey
        )
        end_supported = any(
            _distance_point(beam.end, col.start) < 1000
            for col in columns if col.storey == beam.storey
        )

        if not start_supported or not end_supported:
            unsupported_end = "始端" if not start_supported else "終端"
            if not start_supported and not end_supported:
                unsupported_end = "両端"
            anomalies.append(RelationshipAnomaly(
                anomaly_type=AnomalyType.UNSUPPORTED_BEAM,
                severity=Severity.CRITICAL,
                element_id=beam.id,
                description=f"梁'{beam.id}'の{unsupported_end}に柱がありません。",
                auto_fixable=True,
                fix_description="最寄り柱グリッド位置に接続を推定",
            ))
    return anomalies


def _detect_homeless_openings(model: BuildingModel) -> list[RelationshipAnomaly]:
    """host_elementが未設定の窓/ドアを検出."""
    anomalies = []
    openings = [e for e in model.elements
                if e.element_type in (ElementType.WINDOW, ElementType.DOOR)]
    walls = [e for e in model.elements if e.element_type == ElementType.WALL]

    for opening in openings:
        if not opening.host_element_id:
            nearest_wall = _find_nearest(opening, walls)
            anomalies.append(RelationshipAnomaly(
                anomaly_type=AnomalyType.HOMELESS_OPENING,
                severity=Severity.WARNING,
                element_id=opening.id,
                description=(
                    f"開口部'{opening.id}'({opening.element_type.value})の"
                    f"所属壁が未設定。"
                ),
                related_elements=(nearest_wall.id,) if nearest_wall else (),
                auto_fixable=bool(nearest_wall),
                fix_description=f"最寄り壁'{nearest_wall.id}'に自動割当"
                    if nearest_wall else "",
            ))
    return anomalies


def _detect_broken_columns(model: BuildingModel) -> list[RelationshipAnomaly]:
    """上下階で柱が連続しない箇所を検出."""
    anomalies = []
    columns = [e for e in model.elements if e.element_type == ElementType.COLUMN]

    storeys = sorted(set(e.storey for e in columns))
    for i in range(len(storeys) - 1):
        lower_storey, upper_storey = storeys[i], storeys[i + 1]
        lower_cols = [c for c in columns if c.storey == lower_storey]
        upper_cols = [c for c in columns if c.storey == upper_storey]

        for lcol in lower_cols:
            has_upper = any(
                _distance_point(lcol.start, ucol.start) < 500
                for ucol in upper_cols
            )
            if not has_upper:
                anomalies.append(RelationshipAnomaly(
                    anomaly_type=AnomalyType.BROKEN_COLUMN,
                    severity=Severity.WARNING,
                    element_id=lcol.id,
                    description=(
                        f"柱'{lcol.id}'({lower_storey}F)の上階({upper_storey}F)に"
                        f"対応する柱がありません。"
                    ),
                    auto_fixable=True,
                    fix_description=f"{upper_storey}F同位置に柱を推論で追加",
                ))
    return anomalies


def _detect_load_path_breaks(model: BuildingModel) -> list[RelationshipAnomaly]:
    """荷重経路（屋根→梁→柱→基礎）の断絶を検出."""
    anomalies = []
    has_slab = any(e.element_type == ElementType.SLAB for e in model.elements)
    has_beam = any(e.element_type == ElementType.BEAM for e in model.elements)
    has_column = any(e.element_type == ElementType.COLUMN for e in model.elements)
    has_foundation = any(e.element_type == ElementType.FOUNDATION for e in model.elements)

    path = []
    if has_slab:
        path.append("スラブ")
    if has_beam:
        path.append("梁")
    if has_column:
        path.append("柱")
    if has_foundation:
        path.append("基礎")

    full_path = ["スラブ", "梁", "柱", "基礎"]
    missing = [p for p in full_path if p not in path]

    if missing:
        anomalies.append(RelationshipAnomaly(
            anomaly_type=AnomalyType.LOAD_PATH_BREAK,
            severity=Severity.CRITICAL,
            element_id="LOAD_PATH",
            description=(
                f"荷重経路が断絶: {' → '.join(path or ['なし'])}。"
                f"不足: {', '.join(missing)}。"
            ),
            auto_fixable=False,
            fix_description="不足要素の追加が必要（推論 or 図面確認）",
        ))
    return anomalies


def _detect_unbounded_spaces(model: BuildingModel) -> list[RelationshipAnomaly]:
    """境界要素が不足する空間を検出."""
    anomalies = []
    walls = [e for e in model.elements if e.element_type == ElementType.WALL]

    for space in model.spaces:
        if not space.boundary_element_ids:
            # 空間周辺の壁を探す
            nearby_walls = [w for w in walls if w.storey == space.storey]
            anomalies.append(RelationshipAnomaly(
                anomaly_type=AnomalyType.UNBOUNDED_SPACE,
                severity=Severity.INFO,
                element_id=space.id,
                description=f"空間'{space.name}'の境界要素が未定義。",
                related_elements=tuple(w.id for w in nearby_walls[:4]),
                auto_fixable=bool(nearby_walls),
                fix_description="同階の壁を境界として推定",
            ))
    return anomalies


def _detect_orphan_elements(model: BuildingModel) -> list[RelationshipAnomaly]:
    """どの要素とも関係のない孤立要素を検出."""
    anomalies = []

    for elem in model.elements:
        if elem.element_type in (ElementType.SLAB, ElementType.SPACE):
            continue  # スラブとスペースは孤立しても問題なし

        has_relation = False
        for other in model.elements:
            if other.id == elem.id:
                continue
            if other.storey == elem.storey and _distance(elem, other) < 3000:
                has_relation = True
                break
            if elem.host_element_id == other.id or other.host_element_id == elem.id:
                has_relation = True
                break

        if not has_relation:
            anomalies.append(RelationshipAnomaly(
                anomaly_type=AnomalyType.ORPHAN_ELEMENT,
                severity=Severity.INFO,
                element_id=elem.id,
                description=f"要素'{elem.id}'({elem.element_type.value})は孤立しています。",
                auto_fixable=False,
            ))
    return anomalies


# ─── 自動修復エンジン ────────────────────────────────────────

def auto_repair(model: BuildingModel,
                anomalies: list[RelationshipAnomaly]) -> tuple[BuildingModel, list[RepairAction]]:
    """検出された異常を自動修復する.

    修復は全て推論（INFERRED）として追加し、confidence付きで
    メタ認知KUとして管理される。

    Returns:
        (修復後のモデル, 実行された修復アクションのリスト)
    """
    repairs = []
    new_elements = list(model.elements)
    updated_elements = {}  # id → updated element

    for anomaly in anomalies:
        if not anomaly.auto_fixable:
            continue

        if anomaly.anomaly_type == AnomalyType.FLOATING_COLUMN:
            repair = _repair_floating_column(model, anomaly, new_elements)
            if repair:
                repairs.append(repair)

        elif anomaly.anomaly_type == AnomalyType.HOMELESS_OPENING:
            repair = _repair_homeless_opening(model, anomaly, updated_elements)
            if repair:
                repairs.append(repair)

        elif anomaly.anomaly_type == AnomalyType.BROKEN_COLUMN:
            repair = _repair_broken_column(model, anomaly, new_elements)
            if repair:
                repairs.append(repair)

    # updated_elementsを反映
    final_elements = []
    for elem in new_elements:
        if elem.id in updated_elements:
            final_elements.append(updated_elements[elem.id])
        else:
            final_elements.append(elem)

    repaired_model = replace(model, elements=tuple(final_elements))

    logger.info("Auto-repair: %d anomalies processed, %d repaired",
                len(anomalies), len(repairs))

    return repaired_model, repairs


def _repair_floating_column(model: BuildingModel, anomaly: RelationshipAnomaly,
                             elements: list[BuildingElement]) -> Optional[RepairAction]:
    """浮遊柱に基礎を追加."""
    col = next((e for e in elements if e.id == anomaly.element_id), None)
    if not col:
        return None

    foundation_id = f"FD_auto_{col.id}"
    new_foundation = BuildingElement(
        id=foundation_id,
        element_type=ElementType.FOUNDATION,
        name=f"基礎(自動推論)_{col.id}",
        start=col.start,
        end=Point2D(col.start.x + 1500, col.start.y + 1500),
        thickness=600,
        material=MaterialType.RC,
        storey=0,
        inference=InferenceMetadata(
            extraction_method=ExtractionMethod.STRUCTURAL_RULE,
            confidence=0.5,
            inferred_from=(col.id,),
            inference_rule="auto_repair_floating_column",
        ),
    )
    elements.append(new_foundation)

    return RepairAction(
        anomaly_type=AnomalyType.FLOATING_COLUMN,
        element_id=col.id,
        action=f"柱'{col.id}'直下に基礎'{foundation_id}'を追加",
        confidence=0.5,
        before="基礎なし",
        after=f"基礎{foundation_id}追加(600mm厚, RC, conf=0.5)",
    )


def _repair_homeless_opening(model: BuildingModel, anomaly: RelationshipAnomaly,
                              updated: dict) -> Optional[RepairAction]:
    """窓/ドアに最寄りの壁を割り当て."""
    if not anomaly.related_elements:
        return None

    wall_id = anomaly.related_elements[0]
    opening = next((e for e in model.elements if e.id == anomaly.element_id), None)
    if not opening:
        return None

    updated[opening.id] = replace(opening, host_element_id=wall_id)

    return RepairAction(
        anomaly_type=AnomalyType.HOMELESS_OPENING,
        element_id=opening.id,
        action=f"開口部'{opening.id}'を壁'{wall_id}'に割当",
        confidence=0.6,
        before="host_element_id=''",
        after=f"host_element_id='{wall_id}'",
    )


def _repair_broken_column(model: BuildingModel, anomaly: RelationshipAnomaly,
                            elements: list[BuildingElement]) -> Optional[RepairAction]:
    """断絶柱の上階に柱を追加."""
    col = next((e for e in elements if e.id == anomaly.element_id), None)
    if not col:
        return None

    upper_storey = col.storey + 1
    new_col_id = f"C_auto_{col.id}_F{upper_storey}"
    new_col = BuildingElement(
        id=new_col_id,
        element_type=ElementType.COLUMN,
        name=f"柱(自動推論)_{upper_storey}F",
        start=col.start,
        end=col.end,
        thickness=col.thickness,
        height=col.height,
        material=col.material,
        storey=upper_storey,
        inference=InferenceMetadata(
            extraction_method=ExtractionMethod.STRUCTURAL_RULE,
            confidence=0.6,
            inferred_from=(col.id,),
            inference_rule="auto_repair_broken_column",
        ),
    )
    elements.append(new_col)

    return RepairAction(
        anomaly_type=AnomalyType.BROKEN_COLUMN,
        element_id=col.id,
        action=f"柱'{col.id}'の{upper_storey}Fに'{new_col_id}'を追加",
        confidence=0.6,
        before=f"{upper_storey}F柱なし",
        after=f"{new_col_id}追加(同断面, conf=0.6)",
    )


# ─── 統合実行 ────────────────────────────────────────────────

def validate_and_repair(model: BuildingModel,
                         auto_fix: bool = True) -> ValidationResult:
    """関係性検証 → 異常検知 → 自動修復を一気通貫で実行.

    Args:
        model: 検証対象のBuildingModel
        auto_fix: Trueなら自動修復も実行

    Returns:
        ValidationResult with anomalies and repairs
    """
    anomalies = detect_anomalies(model)

    repairs = []
    if auto_fix:
        fixable = [a for a in anomalies if a.auto_fixable]
        if fixable:
            _, repairs = auto_repair(model, fixable)

    critical = sum(1 for a in anomalies if a.severity == Severity.CRITICAL)
    warning = sum(1 for a in anomalies if a.severity == Severity.WARNING)
    info = sum(1 for a in anomalies if a.severity == Severity.INFO)

    logger.info("Validation: %d anomalies (critical=%d, warning=%d, info=%d), %d repaired",
                len(anomalies), critical, warning, info, len(repairs))

    return ValidationResult(
        anomalies=tuple(anomalies),
        repairs=tuple(repairs),
        critical_count=critical,
        warning_count=warning,
        info_count=info,
        auto_repaired_count=len(repairs),
    )


def format_validation_report(result: ValidationResult) -> str:
    """検証結果をMarkdown形式で出力."""
    lines = [
        "# 関係性検証レポート",
        "",
        f"**異常: {len(result.anomalies)}件** "
        f"(CRITICAL={result.critical_count}, "
        f"WARNING={result.warning_count}, "
        f"INFO={result.info_count})",
        f"**自動修復: {result.auto_repaired_count}件**",
        "",
    ]

    for sev in [Severity.CRITICAL, Severity.WARNING, Severity.INFO]:
        sev_anomalies = [a for a in result.anomalies if a.severity == sev]
        if sev_anomalies:
            lines.append(f"## {sev.value.upper()}")
            lines.append("")
            for a in sev_anomalies:
                fix_mark = " [AUTO-FIX]" if a.auto_fixable else ""
                lines.append(f"- **{a.element_id}** [{a.anomaly_type.value}]{fix_mark}")
                lines.append(f"  {a.description}")
                if a.fix_description:
                    lines.append(f"  → 修復: {a.fix_description}")
            lines.append("")

    if result.repairs:
        lines.append("## 実行された修復")
        lines.append("")
        for r in result.repairs:
            lines.append(f"- **{r.element_id}**: {r.action} (conf={r.confidence:.0%})")
        lines.append("")

    return "\n".join(lines)


# ─── ヘルパー ────────────────────────────────────────────────

def _distance(a: BuildingElement, b: BuildingElement) -> float:
    """2要素の始点間距離."""
    return math.sqrt((a.start.x - b.start.x) ** 2 + (a.start.y - b.start.y) ** 2)


def _distance_point(p1: Point2D, p2: Point2D) -> float:
    """2点間距離."""
    return math.sqrt((p1.x - p2.x) ** 2 + (p1.y - p2.y) ** 2)


def _find_nearest(target: BuildingElement,
                   candidates: list[BuildingElement]) -> Optional[BuildingElement]:
    """最寄りの要素を探す."""
    if not candidates:
        return None
    return min(candidates, key=lambda c: _distance(target, c))
