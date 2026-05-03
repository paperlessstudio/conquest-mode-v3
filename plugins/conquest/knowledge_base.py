"""Conquest Mode v3.0 — 建築知識ベース.

材料物性・建築基準法・設計基準のリファレンスデータ。
「図面は影である」— 影を実体に変換するには物理法則と法規の知識が必要。

CEO原則: 情報は無限に広がっている。外部参照を紐づけて知識を構造化せよ。
"""
from __future__ import annotations

import logging
from dataclasses import replace

from plugins.conquest.schemas import (
    BuildingElement,
    BuildingModel,
    ElementType,
    ExternalReference,
    MaterialType,
)

logger = logging.getLogger(__name__)

# ─── 材料密度 (kg/m3) ─────────────────────────────────────────
MATERIAL_DENSITY: dict[MaterialType, float] = {
    MaterialType.RC: 2400.0,
    MaterialType.S: 7850.0,
    MaterialType.SRC: 3200.0,
    MaterialType.W: 500.0,
    MaterialType.CB: 1900.0,
    MaterialType.UNKNOWN: 2400.0,
}

# ─── 材料強度 ──────────────────────────────────────────────────
MATERIAL_STRENGTH: dict[MaterialType, dict[str, float]] = {
    MaterialType.RC: {"fc_mpa": 24.0, "ft_mpa": 2.4, "e_gpa": 25.0},
    MaterialType.S: {"fy_mpa": 235.0, "fu_mpa": 400.0, "e_gpa": 205.0},
    MaterialType.SRC: {"fc_mpa": 24.0, "fy_mpa": 235.0, "e_gpa": 30.0},
    MaterialType.W: {"fb_mpa": 20.0, "fc_mpa": 17.0, "e_gpa": 7.0},
    MaterialType.CB: {"fc_mpa": 8.0, "ft_mpa": 0.8, "e_gpa": 10.0},
    MaterialType.UNKNOWN: {"fc_mpa": 24.0, "ft_mpa": 2.4, "e_gpa": 25.0},
}

# ─── 建築基準法・告示リファレンス ──────────────────────────────
BUILDING_CODE_REFS: dict[str, ExternalReference] = {
    "rc_column_min": ExternalReference(
        ref_type="building_code",
        code="施行令第77条",
        description="RC柱の最小寸法: 300mm以上",
    ),
    "rc_cover": ExternalReference(
        ref_type="building_code",
        code="施行令第79条",
        description="鉄筋のかぶり厚さ: 柱30mm以上、基礎60mm以上",
    ),
    "wall_thickness_min": ExternalReference(
        ref_type="building_code",
        code="施行令第78条",
        description="耐力壁の最小厚さ: 120mm以上",
    ),
    "slab_thickness_min": ExternalReference(
        ref_type="building_code",
        code="施行令第77条の2",
        description="RC床スラブ最小厚さ: 80mm以上",
    ),
    "beam_depth_ratio": ExternalReference(
        ref_type="building_code",
        code="施行令第78条の2",
        description="梁の有効せい: スパンの1/10以上を推奨",
    ),
    "steel_column": ExternalReference(
        ref_type="building_code",
        code="施行令第66条",
        description="鉄骨柱の細長比: 200以下",
    ),
    "wood_column_min": ExternalReference(
        ref_type="building_code",
        code="施行令第43条",
        description="木造柱の最小寸法: 105mm角以上（3階建は120mm角）",
    ),
    "foundation": ExternalReference(
        ref_type="building_code",
        code="施行令第38条",
        description="基礎は建物の荷重を安全に地盤に伝達すること",
    ),
    "seismic_design": ExternalReference(
        ref_type="building_code",
        code="施行令第82条の6",
        description="強柱弱梁: 柱の耐力は梁の耐力の1.2倍以上",
    ),
}

# ─── 要素タイプ×材料 → 適用コードのマッピング ─────────────────
_ELEMENT_CODE_MAP: dict[tuple[ElementType, MaterialType], tuple[str, ...]] = {
    (ElementType.COLUMN, MaterialType.RC): ("rc_column_min", "rc_cover", "seismic_design"),
    (ElementType.COLUMN, MaterialType.S): ("steel_column", "seismic_design"),
    (ElementType.COLUMN, MaterialType.W): ("wood_column_min",),
    (ElementType.BEAM, MaterialType.RC): ("beam_depth_ratio", "rc_cover"),
    (ElementType.BEAM, MaterialType.S): ("beam_depth_ratio",),
    (ElementType.WALL, MaterialType.RC): ("wall_thickness_min", "rc_cover"),
    (ElementType.WALL, MaterialType.CB): ("wall_thickness_min",),
    (ElementType.SLAB, MaterialType.RC): ("slab_thickness_min", "rc_cover"),
    (ElementType.FOUNDATION, MaterialType.RC): ("foundation", "rc_cover"),
}


def get_material_density(material: MaterialType) -> float:
    """材料密度 (kg/m3) を返す。未知の材料にはRC相当を適用。"""
    density = MATERIAL_DENSITY.get(material, MATERIAL_DENSITY[MaterialType.UNKNOWN])
    logger.debug("Material %s → density %.0f kg/m3", material.value, density)
    return density


def get_material_properties(material: MaterialType) -> dict[str, float]:
    """材料強度パラメータの辞書を返す。"""
    props = MATERIAL_STRENGTH.get(
        material, MATERIAL_STRENGTH[MaterialType.UNKNOWN]
    )
    logger.debug("Material %s → properties %s", material.value, props)
    return dict(props)


def get_code_reference(check_type: str) -> ExternalReference:
    """建築基準法リファレンスを返す。見つからなければ空のExternalReferenceを返す。"""
    ref = BUILDING_CODE_REFS.get(check_type, ExternalReference())
    if not ref.code:
        logger.warning("Code reference not found for check_type=%s", check_type)
    return ref


def _resolve_refs_for_element(element: BuildingElement) -> tuple[ExternalReference, ...]:
    """要素のタイプ・材料から適用される建築基準法コードを解決する。"""
    key = (element.element_type, element.material)
    code_keys = _ELEMENT_CODE_MAP.get(key, ())
    refs = tuple(BUILDING_CODE_REFS[k] for k in code_keys if k in BUILDING_CODE_REFS)
    return refs


def annotate_model_with_references(model: BuildingModel) -> BuildingModel:
    """モデル内の全要素に適用される建築基準法リファレンスを付与する。

    既存のexternal_refsは保持し、重複しないコードのみ追加する。
    immutableパターン: 元のモデルは変更しない。
    """
    updated_elements: list[BuildingElement] = []
    total_added = 0

    for element in model.elements:
        new_refs = _resolve_refs_for_element(element)
        existing_codes = frozenset(ref.code for ref in element.external_refs)
        merged_refs = element.external_refs + tuple(
            r for r in new_refs if r.code not in existing_codes
        )
        added = len(merged_refs) - len(element.external_refs)
        total_added += added
        updated_elements.append(replace(element, external_refs=merged_refs))

    logger.info(
        "Annotated %d elements with %d code references",
        len(updated_elements),
        total_added,
    )
    return replace(model, elements=tuple(updated_elements))
