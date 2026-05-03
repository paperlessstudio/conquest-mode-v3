"""Conquest Mode v3.0 — 重力解析エンジン.

全ての建築物は重力に逆らって立っている。
要素の自重を算出し、荷重伝達経路に沿って累積し、柱の耐力を検証する。

CEO原則: 重力と地球を感じながら設計せよ。
"""
from __future__ import annotations

import logging
import math
from dataclasses import replace

from plugins.conquest.knowledge_base import get_material_density, get_material_properties
from plugins.conquest.schemas import (
    BuildingElement,
    BuildingModel,
    ElementType,
    GravityLoad,
    HumanReviewPoint,
    MaterialType,
)

logger = logging.getLogger(__name__)

_GRAVITY = 9.81  # m/s²


def _distance_2d(element: BuildingElement) -> float:
    """start→endの2D距離 (mm)."""
    dx = element.end.x - element.start.x
    dy = element.end.y - element.start.y
    return math.sqrt(dx * dx + dy * dy)


def compute_element_volume(element: BuildingElement) -> float:
    """要素の体積を算出する (m3).

    全寸法はmm入力 → mm3で計算後 1e9で割ってm3に変換。
    - Wall:   length(start→end) × thickness × height
    - Column: thickness² × height (正方形断面を仮定)
    - Slab:   |dx| × |dy| × thickness (矩形を仮定)
    - Beam:   length(start→end) × thickness × height
    - Others: length × thickness × height (汎用)
    """
    etype = element.element_type

    if etype == ElementType.COLUMN:
        volume_mm3 = element.thickness * element.thickness * element.height
    elif etype == ElementType.SLAB:
        dx = abs(element.end.x - element.start.x)
        dy = abs(element.end.y - element.start.y)
        volume_mm3 = dx * dy * element.thickness
    elif etype in (ElementType.WALL, ElementType.BEAM):
        length = _distance_2d(element)
        volume_mm3 = length * element.thickness * element.height
    else:
        length = _distance_2d(element)
        if length < 1.0:
            length = element.thickness
        volume_mm3 = length * element.thickness * element.height

    volume_m3 = volume_mm3 / 1e9
    logger.debug(
        "Element %s (%s): volume=%.4f m3",
        element.id, etype.value, volume_m3,
    )
    return volume_m3


def compute_self_weight(element: BuildingElement) -> GravityLoad:
    """要素の自重を算出する.

    self_weight (kN) = volume (m3) × density (kg/m3) × 9.81 (m/s²) / 1000
    """
    volume = compute_element_volume(element)
    density = get_material_density(element.material)
    weight_kn = volume * density * _GRAVITY / 1000.0

    return GravityLoad(
        self_weight_kn=round(weight_kn, 3),
        cumulative_load_kn=round(weight_kn, 3),
        volume_m3=round(volume, 6),
        density_kg_m3=density,
    )


def _build_dependency_graph(
    elements: tuple[BuildingElement, ...],
) -> dict[str, list[str]]:
    """depends_onを逆引きして「この要素に荷重を預けている子要素」のマップを作る。"""
    children_of: dict[str, list[str]] = {e.id: [] for e in elements}
    for elem in elements:
        for parent_id in elem.depends_on:
            if parent_id in children_of:
                children_of[parent_id].append(elem.id)
    return children_of


def _topological_order(
    elements: tuple[BuildingElement, ...],
) -> list[str]:
    """逆トポロジカルソート: 葉(上層)→根(基礎)の順。"""
    elem_map = {e.id: e for e in elements}
    in_degree: dict[str, int] = {e.id: 0 for e in elements}

    for elem in elements:
        for parent_id in elem.depends_on:
            if parent_id in in_degree:
                in_degree[parent_id] += 1

    queue: list[str] = [eid for eid, deg in in_degree.items() if deg == 0]
    order: list[str] = []

    while queue:
        current = queue.pop(0)
        order.append(current)
        for parent_id in elem_map[current].depends_on:
            if parent_id in in_degree:
                in_degree[parent_id] -= 1
                if in_degree[parent_id] == 0:
                    queue.append(parent_id)

    if len(order) != len(elements):
        logger.warning(
            "Dependency graph has cycles: processed %d / %d elements",
            len(order), len(elements),
        )
        missing = {e.id for e in elements} - set(order)
        order.extend(missing)

    return order


def accumulate_gravity_loads(model: BuildingModel) -> BuildingModel:
    """荷重伝達経路に沿って累積荷重を計算する.

    逆トポロジカル順（上層→基礎）で走査し、子要素の累積荷重を親要素に加算する。
    """
    elem_map: dict[str, BuildingElement] = {e.id: e for e in model.elements}
    weight_map: dict[str, GravityLoad] = {}

    for elem in model.elements:
        weight_map[elem.id] = compute_self_weight(elem)

    order = _topological_order(model.elements)
    cumulative: dict[str, float] = {
        eid: weight_map[eid].self_weight_kn for eid in elem_map
    }

    for eid in order:
        elem = elem_map[eid]
        for parent_id in elem.depends_on:
            if parent_id in cumulative:
                cumulative[parent_id] += cumulative[eid]

    updated: list[BuildingElement] = []
    for elem in model.elements:
        gl = weight_map[elem.id]
        new_gl = replace(gl, cumulative_load_kn=round(cumulative[elem.id], 3))
        updated.append(replace(elem, gravity_load=new_gl))

    logger.info(
        "Accumulated gravity loads for %d elements", len(updated),
    )
    return replace(model, elements=tuple(updated))


def verify_column_capacity(element: BuildingElement, cumulative_kn: float) -> bool:
    """柱の軸耐力を簡易検証する.

    RC: capacity = 0.4 × fc (MPa) × (thickness/1000)² (m²) × 1000 (kN)
    S:  capacity = fy (MPa) × (thickness/1000)² (m²) × 1000 (kN)
    """
    if element.element_type != ElementType.COLUMN:
        return True

    props = get_material_properties(element.material)
    section_m2 = (element.thickness / 1000.0) ** 2

    if element.material in (MaterialType.RC, MaterialType.SRC, MaterialType.CB):
        fc = props.get("fc_mpa", 24.0)
        capacity_kn = 0.4 * fc * section_m2 * 1000.0
    elif element.material == MaterialType.S:
        fy = props.get("fy_mpa", 235.0)
        capacity_kn = fy * section_m2 * 1000.0
    elif element.material == MaterialType.W:
        fc = props.get("fc_mpa", 17.0)
        capacity_kn = 0.4 * fc * section_m2 * 1000.0
    else:
        fc = props.get("fc_mpa", 24.0)
        capacity_kn = 0.4 * fc * section_m2 * 1000.0

    adequate = cumulative_kn <= capacity_kn
    if not adequate:
        logger.warning(
            "Column %s OVER-STRESSED: cumulative=%.1f kN > capacity=%.1f kN",
            element.id, cumulative_kn, capacity_kn,
        )
    else:
        logger.debug(
            "Column %s OK: cumulative=%.1f kN <= capacity=%.1f kN",
            element.id, cumulative_kn, capacity_kn,
        )
    return adequate


def run_gravity_analysis(model: BuildingModel) -> BuildingModel:
    """重力解析のフルパイプライン.

    1. 各要素の自重算出
    2. 荷重伝達経路に沿った累積
    3. 柱耐力の検証
    4. 結果をモデルに反映
    """
    logger.info("=== Gravity Analysis Start (%d elements) ===", len(model.elements))

    result = accumulate_gravity_loads(model)

    total_weight = sum(e.gravity_load.self_weight_kn for e in result.elements)

    all_ok = True
    review_points: list[HumanReviewPoint] = list(result.review_points)

    for elem in result.elements:
        if elem.element_type == ElementType.COLUMN:
            ok = verify_column_capacity(elem, elem.gravity_load.cumulative_load_kn)
            if not ok:
                all_ok = False
                review_points.append(HumanReviewPoint(
                    element_id=elem.id,
                    review_type="warning",
                    question=(
                        f"柱 {elem.id} の累積荷重 "
                        f"{elem.gravity_load.cumulative_load_kn:.1f} kN が "
                        f"簡易軸耐力を超過しています"
                    ),
                    suggestion="断面を拡大するか、荷重経路を再検討してください",
                    status="pending",
                ))

    result = replace(
        result,
        gravity_check_passed=all_ok,
        total_building_weight_kn=round(total_weight, 3),
        review_points=tuple(review_points),
    )

    logger.info(
        "=== Gravity Analysis Complete: total=%.1f kN, passed=%s ===",
        total_weight, all_ok,
    )
    return result
