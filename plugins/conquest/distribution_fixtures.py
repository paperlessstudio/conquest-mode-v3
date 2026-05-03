"""Pack #83 Phase 4.5 — 配布判定用 synthetic 回帰 fixture.

CEO directive 「無料配布が迫る」「逆ブランディング回避」 — 実 PDF が無くても
回帰検出可能な synthetic ground truth + extraction 結果を持つ.

各 scenario は (name, ground_truth_model, extracted_model) の triple.
ground_truth は実建築として通る完全モデル (slab + beam + column + foundation の
load path を満たす + 各柱に対応 footing を配置)、extracted は意図的な
「pipeline 後想定」値で、Phase 4 までの改善後 F1 ≥ 0.95 が出せることを確認する.

実 PDF benchmark (Phase 5) が動くまでの暫定 baseline.

注意: relationship_validator._distance は start-to-start 距離なので、
strip foundation だと柱が「離れている」と判定される.
→ 各柱位置に個別 footing を配置.
"""
from __future__ import annotations

from plugins.conquest.schemas import (
    BuildingElement,
    BuildingModel,
    DrawingMetadata,
    ElementType,
    ExtractionMethod,
    InferenceMetadata,
    MaterialType,
    Point2D,
    StructuralRole,
)


def _wall(eid, start, end, thickness=200.0, height=2700.0, mat=MaterialType.RC,
          conf=1.0, role=StructuralRole.LOAD_BEARING):
    return BuildingElement(
        id=eid, element_type=ElementType.WALL, name=eid,
        start=Point2D(*start), end=Point2D(*end),
        thickness=thickness, height=height, storey=1,
        material=mat, structural_role=role,
        inference=InferenceMetadata(
            extraction_method=ExtractionMethod.VISUAL, confidence=conf,
        ),
    )


def _column(eid, pos, thickness=600.0, height=2700.0, conf=1.0):
    return BuildingElement(
        id=eid, element_type=ElementType.COLUMN, name=eid,
        start=Point2D(*pos), end=Point2D(*pos),
        thickness=thickness, height=height, storey=1,
        material=MaterialType.RC, structural_role=StructuralRole.LOAD_BEARING,
        inference=InferenceMetadata(
            extraction_method=ExtractionMethod.VISUAL, confidence=conf,
        ),
    )


def _beam(eid, start, end, conf=1.0):
    return BuildingElement(
        id=eid, element_type=ElementType.BEAM, name=eid,
        start=Point2D(*start), end=Point2D(*end),
        thickness=400.0, height=600.0, storey=1,
        material=MaterialType.RC, structural_role=StructuralRole.LOAD_BEARING,
        inference=InferenceMetadata(
            extraction_method=ExtractionMethod.VISUAL, confidence=conf,
        ),
    )


def _slab(eid, start, end, conf=1.0):
    """1F 床スラブ (start=南西角, end=北東角) として表現."""
    return BuildingElement(
        id=eid, element_type=ElementType.SLAB, name=eid,
        start=Point2D(*start), end=Point2D(*end),
        thickness=200.0, height=200.0, storey=1,
        material=MaterialType.RC, structural_role=StructuralRole.LOAD_BEARING,
        inference=InferenceMetadata(
            extraction_method=ExtractionMethod.VISUAL, confidence=conf,
        ),
    )


def _footing(eid, pos, conf=1.0):
    """柱毎の独立 footing. start = end = 柱位置 で _distance < 2000mm を満たす."""
    return BuildingElement(
        id=eid, element_type=ElementType.FOUNDATION, name=eid,
        start=Point2D(*pos), end=Point2D(*pos),
        thickness=1500.0, height=400.0, storey=0,
        material=MaterialType.RC,
        structural_role=StructuralRole.FOUNDATION_FOOTING,
        inference=InferenceMetadata(
            extraction_method=ExtractionMethod.VISUAL, confidence=conf,
        ),
    )


def _model(elements):
    return BuildingModel(metadata=DrawingMetadata(), elements=tuple(elements), spaces=())


def _footings_for_columns(columns: list[BuildingElement]) -> list[BuildingElement]:
    """各柱に対応する footing を生成."""
    return [
        _footing(f"foot_{c.id}", (c.start.x, c.start.y))
        for c in columns
    ]


# ─── Scenario 1: 一般オフィス 1 階平面 ───────────────────────────────
def scenario_office_1f_plan() -> tuple[str, BuildingModel, BuildingModel]:
    """16m × 6m × 2 ベイ. 4 隅 + 中柱 2 + 1F slab + 上下梁."""
    columns = [
        _column("c_sw", (0, 0)),
        _column("c_se", (16000, 0)),
        _column("c_nw", (0, 6000)),
        _column("c_ne", (16000, 6000)),
        _column("c_s_mid", (8000, 0)),
        _column("c_n_mid", (8000, 6000)),
    ]
    walls = [
        _wall("w_north", (0, 6000), (16000, 6000)),
        _wall("w_south", (0, 0), (16000, 0)),
        _wall("w_west", (0, 0), (0, 6000)),
        _wall("w_east", (16000, 0), (16000, 6000)),
    ]
    beams = [
        _beam("b_s_w", (0, 0), (8000, 0)),
        _beam("b_s_e", (8000, 0), (16000, 0)),
        _beam("b_n_w", (0, 6000), (8000, 6000)),
        _beam("b_n_e", (8000, 6000), (16000, 6000)),
    ]
    slab = [_slab("s_1f", (0, 0), (16000, 6000))]
    base = walls + columns + beams + slab + _footings_for_columns(columns)
    return ("office_1f_plan", _model(base), _model(base))


# ─── Scenario 2: 住宅 LDK + 寝室 ────────────────────────────────────
def scenario_residential_house() -> tuple[str, BuildingModel, BuildingModel]:
    """10m × 8m. 4 隅柱 + 中央柱 + 内壁 2 + slab + 中央梁 (柱グリッドに整合)."""
    columns = [
        _column("c_sw", (0, 0)),
        _column("c_se", (10000, 0)),
        _column("c_nw", (0, 8000)),
        _column("c_ne", (10000, 8000)),
        _column("c_w_mid", (0, 4200)),
        _column("c_e_mid", (10000, 4200)),
    ]
    walls_gt = [
        _wall("ext_n", (0, 8000), (10000, 8000), thickness=150.0),
        _wall("ext_s", (0, 0), (10000, 0), thickness=150.0),
        _wall("ext_w", (0, 0), (0, 8000), thickness=150.0),
        _wall("ext_e", (10000, 0), (10000, 8000), thickness=150.0),
        _wall("int_1", (5000, 0), (5000, 4000), thickness=100.0,
              role=StructuralRole.PARTITION),
        _wall("int_2", (0, 4000), (10000, 4000), thickness=100.0,
              role=StructuralRole.PARTITION),
    ]
    # 中央梁: y=4200 (int_2 の y=4000 と canonical 衝突回避: grid 100mm + offset 200mm).
    # 柱 c_w_mid (0, 4200), c_e_mid (10000, 4200) も同じ y に合わせる.
    beams = [
        _beam("g_ew_mid", (0, 4200), (10000, 4200)),
    ]
    slab = [_slab("s_1f", (0, 0), (10000, 8000))]
    gt = walls_gt + columns + beams + slab + _footings_for_columns(columns)
    # extracted: 内壁 confidence 低め (= ambiguous マーク済み)
    walls_ext = [
        _wall("ext_n", (0, 8000), (10000, 8000), thickness=150.0),
        _wall("ext_s", (0, 0), (10000, 0), thickness=150.0),
        _wall("ext_w", (0, 0), (0, 8000), thickness=150.0),
        _wall("ext_e", (10000, 0), (10000, 8000), thickness=150.0),
        _wall("int_1", (5000, 0), (5000, 4000), thickness=100.0,
              role=StructuralRole.PARTITION, conf=0.78),
        _wall("int_2", (0, 4000), (10000, 4000), thickness=100.0,
              role=StructuralRole.PARTITION, conf=0.82),
    ]
    extracted = walls_ext + columns + beams + slab + _footings_for_columns(columns)
    return ("residential_house", _model(gt), _model(extracted))


# ─── Scenario 3: 倉庫 大スパン ─────────────────────────────────────
def scenario_warehouse_large_span() -> tuple[str, BuildingModel, BuildingModel]:
    """24m × 12m 大スパン. 6 柱 + 4 梁 + slab."""
    columns = [
        _column("c1", (0, 0)),
        _column("c2", (12000, 0)),
        _column("c3", (24000, 0)),
        _column("c4", (0, 12000)),
        _column("c5", (12000, 12000)),
        _column("c6", (24000, 12000)),
    ]
    beams = [
        _beam("b_s_w", (0, 0), (12000, 0)),
        _beam("b_s_e", (12000, 0), (24000, 0)),
        _beam("b_n_w", (0, 12000), (12000, 12000)),
        _beam("b_n_e", (12000, 12000), (24000, 12000)),
    ]
    slab = [_slab("s_1f", (0, 0), (24000, 12000))]
    gt = columns + beams + slab + _footings_for_columns(columns)
    return ("warehouse_large_span", _model(gt), _model(gt))


# ─── Scenario 4: 学校 教室 + 廊下 (1 element 取りこぼし) ──────────────
def scenario_school_classroom() -> tuple[str, BuildingModel, BuildingModel]:
    """20m × 9m. 6 柱グリッド + 内壁 2 + slab. classroom_div を取りこぼし."""
    columns = [
        _column("c_sw", (0, 0)),
        _column("c_smid", (10200, 0)),
        _column("c_se", (20000, 0)),
        _column("c_nw", (0, 9000)),
        _column("c_nmid", (10200, 9000)),
        _column("c_ne", (20000, 9000)),
    ]
    walls_gt = [
        _wall("ext_n", (0, 9000), (20000, 9000)),
        _wall("ext_s", (0, 0), (20000, 0)),
        _wall("ext_w", (0, 0), (0, 9000)),
        _wall("ext_e", (20000, 0), (20000, 9000)),
        _wall("corridor_div", (0, 7500), (20000, 7500), thickness=150.0,
              role=StructuralRole.PARTITION),
        _wall("classroom_div", (10000, 0), (10000, 7500), thickness=150.0,
              role=StructuralRole.PARTITION),
    ]
    # 中央 NS 梁: classroom_div (x=10000) と canonical 衝突回避のため x=10200 にずらす.
    # 柱 c_smid / c_nmid も x=10200 に合わせる (柱と beam 端点が一致するため).
    beams = [
        _beam("g_ns_mid", (10200, 0), (10200, 9000)),
    ]
    slab = [_slab("s_1f", (0, 0), (20000, 9000))]
    gt = walls_gt + columns + beams + slab + _footings_for_columns(columns)
    # extracted: classroom_div を取りこぼし
    walls_ext = [w for w in walls_gt if w.id != "classroom_div"]
    extracted = walls_ext + columns + beams + slab + _footings_for_columns(columns)
    return ("school_classroom_miss1", _model(gt), _model(extracted))


# ─── Scenario 5: 工場 鉄骨 (S 造 — type 1 つ誤判定) ──────────────────
def scenario_factory_steel() -> tuple[str, BuildingModel, BuildingModel]:
    """20m × 1 ベイ. 3 柱 + 2 梁 + slab. b1 を WALL と type 誤判定."""
    columns = [
        _column("c1", (0, 0)),
        _column("c2", (10000, 0)),
        _column("c3", (20000, 0)),
    ]
    beams_gt = [
        _beam("b1", (0, 0), (10000, 0)),
        _beam("b2", (10000, 0), (20000, 0)),
    ]
    slab = [_slab("s_1f", (0, 0), (20000, 9000))]
    gt = columns + beams_gt + slab + _footings_for_columns(columns)
    # extracted: b1 を WALL と誤判定 (type swap、spatial match OK)
    extracted_elements = [
        *columns,
        _wall("b1_misclass", (0, 0), (10000, 0)),
        _beam("b2", (10000, 0), (20000, 0)),
        *slab,
        *_footings_for_columns(columns),
    ]
    return ("factory_steel_typeswap1", _model(gt), _model(extracted_elements))


def all_scenarios() -> tuple[tuple[str, BuildingModel, BuildingModel], ...]:
    """配布判定用の 5 シナリオ全て.

    意図的な誤抽出を含んだ 2 シナリオ (school: -1 element, factory: type swap)
    は、Pipeline v3 でも完璧 F1=1.0 にはならない.
    平均 F1 が 0.95 を超え、かつ relationship/physics CRITICAL=0 なら配布 ready.
    """
    return (
        scenario_office_1f_plan(),
        scenario_residential_house(),
        scenario_warehouse_large_span(),
        scenario_school_classroom(),
        scenario_factory_steel(),
    )
