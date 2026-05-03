"""Conquest Mode — 中間表現（IR）スキーマ定義.

VLM図面解析の出力とIFC生成の入力を繋ぐデータ構造。
施工手順（construction_phase, sequence_order）を第一級市民として扱う。

v2.0: 「図面は影である、実体は別にある。影から実体を想像せよ」
  - ExtractionMethod: 要素が「見えた(影)」か「推論した(実体)」かを区別
  - StructuralRole: 要素の構造的役割（耐力壁/間仕切/せん断壁）
  - InferenceMetadata: 推論の根拠・確信度・パス番号
  - StructuralSystem: 建物全体の構造種別（RCラーメン/壁式/S造等）
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


# ─── 施工フェーズ定義 ─────────────────────────────────────────

class ConstructionPhase(Enum):
    """施工の大分類フェーズ。日本の一般的なRC造施工順序に準拠。"""
    FOUNDATION = "foundation"          # 基礎工事（杭・フーチング・地中梁・耐圧版）
    COLUMN = "column"                  # 柱工事
    BEAM = "beam"                      # 梁工事
    SLAB = "slab"                      # スラブ工事（床版・屋根版）
    STRUCTURAL_WALL = "structural_wall"  # 耐力壁
    PARTITION_WALL = "partition_wall"    # 間仕切壁
    OPENING = "opening"                # 開口部（ドア・窓）
    FINISHING = "finishing"             # 仕上げ工事
    MEP = "mep"                        # 設備工事（機械・電気・配管）


class ElementType(Enum):
    """建築要素の種類。IFCエンティティに対応。"""
    WALL = "wall"
    COLUMN = "column"
    BEAM = "beam"
    SLAB = "slab"
    DOOR = "door"
    WINDOW = "window"
    STAIR = "stair"
    SPACE = "space"           # 部屋・空間
    FOUNDATION = "foundation"  # 基礎
    ROOF = "roof"


class MaterialType(Enum):
    """主要構造材質。"""
    RC = "rc"             # 鉄筋コンクリート
    S = "s"               # 鉄骨
    SRC = "src"           # 鉄骨鉄筋コンクリート
    W = "w"               # 木造
    CB = "cb"             # コンクリートブロック
    UNKNOWN = "unknown"


# ─── v2.0: 影と実体の区別 ────────────────────────────────────

class ExtractionMethod(Enum):
    """要素の抽出方法 — 影(visual)から見えたか、実体(inferred)を想像したか。"""
    VISUAL = "visual"                # 図面に描かれている（影に映った線）
    INFERRED = "inferred"            # VLMが推論した（影から想像した実体）
    STRUCTURAL_RULE = "structural_rule"  # ルールエンジンが生成した確定推論


class StructuralRole(Enum):
    """建築要素の構造的役割。"""
    LOAD_BEARING = "load_bearing"        # 鉛直荷重を支持
    PARTITION = "partition"              # 間仕切（非構造）
    SHEAR_WALL = "shear_wall"           # 水平力（地震・風）に抵抗
    BRACING = "bracing"                 # ブレース（鉄骨造の斜材）
    TRANSFER_BEAM = "transfer_beam"     # 転換梁（荷重経路を変更）
    GRADE_BEAM = "grade_beam"           # 地中梁
    FOUNDATION_FOOTING = "foundation_footing"  # フーチング基礎


class StructuralSystem(Enum):
    """建物全体の構造種別。"""
    RC_RAHMEN = "rc_rahmen"              # RCラーメン構造
    RC_BEARING_WALL = "rc_bearing_wall"  # RC壁式構造
    STEEL_RAHMEN = "steel_rahmen"        # Sラーメン構造
    STEEL_BRACED = "steel_braced"        # Sブレース構造
    WOOD_POST_BEAM = "wood_post_beam"    # 木造軸組工法
    WOOD_PANEL = "wood_panel"            # 木造枠組壁工法
    SRC_COMPOSITE = "src_composite"      # SRC造
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class InferenceMetadata:
    """推論メタデータ — この要素がどのように「想像」されたかを記録する。"""
    extraction_method: ExtractionMethod = ExtractionMethod.VISUAL
    confidence: float = 1.0              # 0.0-1.0 確信度
    inferred_from: tuple[str, ...] = ()  # 推論の根拠となった要素ID
    inference_rule: str = ""             # 適用されたルール名
    pass_number: int = 1                 # VLMの何パス目で検出されたか


# ─── v3.0: 重力・接合・フレーム・LOD ─────────────────────────

class LOD(Enum):
    """Level of Development — ディテールは見ようとすれば見えてくる。"""
    LOD_100 = 100  # 概形のみ（寸法なし）
    LOD_200 = 200  # 概略寸法あり
    LOD_300 = 300  # 確定寸法＋材質
    LOD_350 = 350  # 施工調整（接合部・取合い）
    LOD_400 = 400  # 製作図（ボルト・溶接詳細）


class JointType(Enum):
    """接合部の種類 — 接合しなければ地震に耐えられない。"""
    RIGID = "rigid"              # 剛接合（RCラーメン柱梁）
    PIN = "pin"                  # ピン接合（ブレース端部）
    SEMI_RIGID = "semi_rigid"    # 半剛接合（木造仕口）
    FIXED_BASE = "fixed_base"    # 固定端（柱脚）
    UNKNOWN = "unknown"


class FrameType(Enum):
    """フレームの種類 — フレームを構成しなければ空間を構築できない。"""
    MOMENT_FRAME = "moment_frame"          # ラーメンフレーム
    BRACED_FRAME = "braced_frame"          # ブレースフレーム
    SHEAR_WALL_FRAME = "shear_wall_frame"  # 耐力壁付きフレーム
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class GravityLoad:
    """重力荷重 — 重力と地球を感じながら設計せよ。"""
    self_weight_kn: float = 0.0       # 自重 (kN)
    cumulative_load_kn: float = 0.0   # 累積荷重 (kN)
    volume_m3: float = 0.0            # 体積 (m3)
    density_kg_m3: float = 2400.0     # 材料密度 (kg/m3)


@dataclass(frozen=True)
class JointInfo:
    """接合部情報."""
    joint_id: str = ""
    joint_type: JointType = JointType.UNKNOWN
    connected_elements: tuple[str, ...] = ()
    moment_capacity_knm: float = 0.0
    seismic_adequate: bool = True


@dataclass(frozen=True)
class FrameInfo:
    """構造フレーム情報."""
    frame_id: str = ""
    frame_type: FrameType = FrameType.UNKNOWN
    member_ids: tuple[str, ...] = ()
    span_mm: float = 0.0
    storey: int = 1
    forms_enclosure: bool = False


@dataclass(frozen=True)
class ExternalReference:
    """外部情報参照 — 情報は無限に広がっている。"""
    ref_type: str = ""       # "building_code" | "standard_detail" | "material_spec"
    code: str = ""
    description: str = ""


@dataclass(frozen=True)
class HumanReviewPoint:
    """人間検証ポイント — 最終的な手順は実際に建てる人間に問え。"""
    element_id: str = ""
    review_type: str = ""     # "approval" | "question" | "warning"
    question: str = ""
    suggestion: str = ""
    status: str = "pending"


@dataclass(frozen=True)
class Connection:
    """要素間の接続 — 関連性のLODを管理。設計=関連性の深化。"""
    id: str = ""
    element_a_id: str = ""
    element_b_id: str = ""
    connection_type: str = ""   # "column_beam", "column_foundation", "wall_slab"
    lod: LOD = LOD.LOD_200
    uniclass_ss: str = ""       # 接続のUniclassコード
    detail: str = ""            # "rigid_moment", "simple_shear", "base_plate"


# ─── v4.0: 環境・快適性・感性 ─────────────────────────────────

@dataclass(frozen=True)
class SolarAnalysis:
    """日照・採光分析 — 葉の舞を見て、風を感じ。"""
    orientation_deg: float = 0.0          # 建物主軸方位角（0=北, 90=東）
    south_facing_window_ratio: float = 0.0  # 南面窓面積比
    estimated_daylight_hours: float = 0.0   # 推定日照時間(h)
    daylight_factor: float = 0.0            # 昼光率(%)
    solar_heat_gain_summer: float = 0.0     # 夏季日射取得量(W)


@dataclass(frozen=True)
class ComfortMetrics:
    """通風・温熱快適性 — 人の微笑みを見て、温度を感じ。"""
    pmv: float = 0.0                # PMV（予測平均温冷感申告）
    ppd: float = 0.0                # PPD（予測不満足者率 %）
    cross_ventilation: bool = False  # 通風経路の有無
    ventilation_openings: int = 0    # 通風開口部数
    window_to_wall_ratio: float = 0.0  # 窓壁面積比
    natural_light_score: float = 0.0   # 自然採光スコア(0-1)


@dataclass(frozen=True)
class SustainabilityScore:
    """サステナビリティ評価 — 自然に優しく。"""
    casbee_rank: str = ""             # CASBEE評価ランク (S/A/B+/B-/C)
    casbee_score: float = 0.0         # CASBEE BEE値
    energy_efficiency: float = 0.0    # エネルギー効率スコア(%)
    natural_material_ratio: float = 0.0  # 自然素材比率(0-1)
    biophilic_score: float = 0.0      # バイオフィリックデザインスコア(0-1)
    green_area_potential: float = 0.0  # 緑化ポテンシャル(0-1)


@dataclass(frozen=True)
class KanseiScore:
    """感性工学スコア — 数値に還元できない「心地よさ」を数値化する試み。"""
    overall: float = 0.0        # 総合感性スコア(0-100)
    light_quality: float = 0.0  # 光の質
    air_quality: float = 0.0    # 空気の質
    thermal_quality: float = 0.0  # 温熱の質
    nature_connection: float = 0.0  # 自然とのつながり
    spatial_quality: float = 0.0    # 空間の質


# ─── 幾何学データ ─────────────────────────────────────────────

@dataclass(frozen=True)
class Point2D:
    """2D座標（mm単位）."""
    x: float
    y: float


@dataclass(frozen=True)
class Point3D:
    """3D座標（mm単位）."""
    x: float
    y: float
    z: float = 0.0


# ─── 建築要素 ─────────────────────────────────────────────────

@dataclass(frozen=True)
class BuildingElement:
    """図面から抽出された建築要素.

    施工フェーズと順序が第一級フィールド。
    """
    id: str
    element_type: ElementType
    name: str = ""

    # 幾何学
    start: Point2D = field(default_factory=lambda: Point2D(0, 0))
    end: Point2D = field(default_factory=lambda: Point2D(0, 0))
    height: float = 0.0          # mm
    thickness: float = 0.0       # mm
    width: float = 0.0           # mm（開口部用）

    # 属性
    material: MaterialType = MaterialType.UNKNOWN
    storey: int = 1              # 階数（1=1F, 0=基礎, -1=地下1F）
    host_element_id: str = ""    # 開口部の場合、所属する壁のID
    notes: str = ""

    # ★ 施工手順（Conquest Modeの核心）
    construction_phase: ConstructionPhase = ConstructionPhase.FINISHING
    sequence_order: int = 0      # 同一フェーズ内の施工順序
    depends_on: tuple[str, ...] = ()  # この要素の施工前に完了すべき要素ID

    # ★ v2.0: 影と実体（推論メタデータ）
    position_3d: Point3D = field(default_factory=lambda: Point3D(0, 0, 0))
    structural_role: StructuralRole = StructuralRole.LOAD_BEARING
    inference: InferenceMetadata = field(default_factory=InferenceMetadata)

    # ★ v3.0: 重力・LOD・外部参照
    gravity_load: GravityLoad = field(default_factory=GravityLoad)
    lod: LOD = LOD.LOD_200
    external_refs: tuple[ExternalReference, ...] = ()

    # ★ v5.0: Uniclass分類 — 設計施工ネットワーク
    uniclass_ef: str = ""     # EF code (Elements/Functions)
    uniclass_ss: str = ""     # Ss code (Systems)
    uniclass_pr: str = ""     # Pr code (Products)


@dataclass(frozen=True)
class Space:
    """部屋・空間定義."""
    id: str
    name: str                  # "LDK", "洋室1", "トイレ" etc.
    storey: int = 1
    boundary_element_ids: tuple[str, ...] = ()
    area: float = 0.0         # m2
    uniclass_sl: str = ""     # SL code (Spaces/Locations)
    activities: tuple[str, ...] = ()  # Ac codes for this space


# ─── 建物全体 ─────────────────────────────────────────────────

@dataclass(frozen=True)
class DrawingMetadata:
    """図面メタデータ."""
    source_file: str = ""
    drawing_type: str = ""     # "平面図", "断面図", "立面図"
    scale: str = ""            # "1:100"
    scale_factor: float = 0.01  # mm→m変換
    unit: str = "mm"
    confidence: float = 0.0


@dataclass(frozen=True)
class BuildingModel:
    """Conquest Modeの中間表現（IR）— 建物全体モデル.

    VLM解析の出力であり、IFC生成の入力。
    施工手順情報を含む4Dモデル。
    """
    metadata: DrawingMetadata = field(default_factory=DrawingMetadata)
    storeys: tuple[int, ...] = (1,)        # 階数リスト
    storey_heights: dict[int, float] = field(default_factory=lambda: {1: 3000.0})  # 階→天井高(mm)
    elements: tuple[BuildingElement, ...] = ()
    spaces: tuple[Space, ...] = ()

    # ★ 施工スケジュール概要
    total_phases: int = 0
    estimated_construction_days: int = 0

    # ★ v2.0: 構造解析結果
    structural_system: StructuralSystem = StructuralSystem.UNKNOWN
    structural_confidence: float = 0.0
    validation_passed: bool = False
    validation_issues: tuple[str, ...] = ()
    inference_stats: dict = field(default_factory=dict)

    # ★ v3.0: 重力・接合・フレーム・レビュー
    joints: tuple[JointInfo, ...] = ()
    frames: tuple[FrameInfo, ...] = ()
    review_points: tuple[HumanReviewPoint, ...] = ()
    gravity_check_passed: bool = False
    total_building_weight_kn: float = 0.0

    # ★ v5.0: Uniclass建物分類 + 接続LOD
    uniclass_en: str = ""     # En code (Entity)
    uniclass_co: str = ""     # Co code (Complex)
    connections: tuple = ()   # Connection tuples

    # ★ v4.0: 環境・快適性・感性
    solar: SolarAnalysis = field(default_factory=SolarAnalysis)
    comfort: ComfortMetrics = field(default_factory=ComfortMetrics)
    sustainability: SustainabilityScore = field(default_factory=SustainabilityScore)
    kansei: KanseiScore = field(default_factory=KanseiScore)

    @property
    def walls(self) -> tuple[BuildingElement, ...]:
        return tuple(e for e in self.elements if e.element_type == ElementType.WALL)

    @property
    def columns(self) -> tuple[BuildingElement, ...]:
        return tuple(e for e in self.elements if e.element_type == ElementType.COLUMN)

    @property
    def beams(self) -> tuple[BuildingElement, ...]:
        return tuple(e for e in self.elements if e.element_type == ElementType.BEAM)

    @property
    def slabs(self) -> tuple[BuildingElement, ...]:
        return tuple(e for e in self.elements if e.element_type == ElementType.SLAB)

    @property
    def openings(self) -> tuple[BuildingElement, ...]:
        return tuple(e for e in self.elements
                     if e.element_type in (ElementType.DOOR, ElementType.WINDOW))

    def get_construction_sequence(self) -> list[BuildingElement]:
        """施工順序で全要素をソート。Conquest Modeの核心出力。"""
        phase_order = {phase: i for i, phase in enumerate(ConstructionPhase)}
        return sorted(
            self.elements,
            key=lambda e: (e.storey, phase_order.get(e.construction_phase, 99), e.sequence_order),
        )
