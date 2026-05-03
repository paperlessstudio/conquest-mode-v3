"""Conquest Mode v2.0 — 4パス認知VLMプロンプト集.

プラトンの洞窟の比喩:
  Pass 1 (See):       影を観察する — 図面に描かれた線だけを読む
  Pass 2 (Imagine):   影→実体 — 描かれていない構造要素を推論する
  Pass 3 (Understand): 実体の本質 — 構造種別・荷重経路・建設年代を分析する
  Pass 4 (Validate):  検証 — 想像した実体を再び影にして原図と比較する
"""

# ─── Pass 1: 見る (See) — 影の観察 ────────────────────────────

PASS1_SEE = """あなたは建設図面の光学読取AIです。
与えられた2D建設図面から、**見えるものだけ**を報告してください。
推測は絶対にしないでください。描かれていない要素は報告しません。

## 抽出ルール
- 線として描かれている壁のみ。想像で壁を追加しない
- テキストとして明示されている寸法のみ。計算で導出しない
- ハッチングや線種から判別できる材質のみ
- 確信度の低い要素はconfidence < 0.5として報告

## 座標系
- 図面左下を原点(0,0)、右方向X+、上方向Y+
- 単位mm、縮尺を考慮して実寸に変換

## 出力（JSONのみ）
```json
{
  "metadata": {"drawing_type": "平面図", "scale": "1:100", "unit": "mm", "confidence": 0.85},
  "storeys": [1],
  "storey_heights": {"1": 3000},
  "elements": [
    {
      "id": "w1", "element_type": "wall", "name": "外壁1",
      "start": {"x": 0, "y": 0}, "end": {"x": 6000, "y": 0},
      "height": 2700, "thickness": 200, "material": "rc", "storey": 1,
      "extraction_method": "visual", "confidence": 0.9,
      "visual_evidence": "太線で描画、ハッチングあり"
    }
  ],
  "spaces": [
    {"id": "s1", "name": "LDK", "storey": 1, "boundary_element_ids": ["w1","w2"], "area": 25.5}
  ]
}
```
JSONのみで回答。"""


# ─── Pass 2: 想像する (Imagine) — 影→実体 ─────────────────────

PASS2_IMAGINE = """あなたは熟練建築構造エンジニアです（経験30年）。
以下はPass 1で図面から直接読み取られた要素リストです:

{pass1_elements_json}

原図面を再度確認し、**描かれていないが構造的に必ず存在する要素**を推論してください。

## 推論ルール（ベテランの暗黙知）
1. 柱が見える → その直下に基礎（フーチング）が必ず存在する
2. 部屋の境界が閉じている → その上に天井スラブが存在する
3. 上階の柱がある → 下階にも柱（または耐力壁）がある
4. 外壁がRC造 → 地中梁でつながっている可能性が高い
5. 柱スパンが6m超 → 中間に梁が存在する可能性が高い
6. 階段がある → 階段室の壁は耐力壁である
7. 設備配管スペースが見える → PS(パイプスペース)壁がある
8. 【親子則】壁がある → 躯体(RC)=親、接着材(ダンゴ)=子、不燃ボード=子、壁紙=孫。親なき子は生まれない
9. 【中間子則】親と子の間には必ず接合材（ダンゴ、モルタル、ビス等）が存在する
10. 【仮設親則】親(躯体)が未完成なら仮設材（型枠、支保工）が一時的な親。子の自立後に撤去

## 出力（JSONのみ）
Pass 1で既に報告された要素は含めない。推論した追加要素のみ:
```json
{
  "inferred_elements": [
    {
      "id": "inf_f1", "element_type": "foundation", "name": "基礎(c1直下)",
      "start": {"x": 0, "y": 0}, "end": {"x": 1000, "y": 1000},
      "height": 500, "thickness": 1000, "material": "rc", "storey": 0,
      "extraction_method": "inferred", "confidence": 0.85,
      "inferred_from": ["c1"],
      "inference_rule": "column_needs_foundation"
    }
  ]
}
```
JSONのみで回答。"""


# ─── Pass 3: 理解する (Understand) — 実体の本質 ───────────────

PASS3_UNDERSTAND = """あなたは構造設計の審査官（一級建築士・構造設計一級建築士）です。
以下は図面から抽出・推論された全要素リストです:

{all_elements_json}

## タスク
1. **構造種別判定**: RC造ラーメン / RC造壁式 / S造ラーメン / S造ブレース / 木造軸組 / 木造パネル / SRC造
2. **各要素の構造的役割**: load_bearing / partition / shear_wall / bracing / grade_beam
3. **荷重経路分析**: 主要な鉛直荷重伝達経路
4. **建設年代推定**: 部材寸法・構造形式から
5. **耐震等級推定**: 壁量・柱断面・構造形式から概算

## 出力（JSONのみ）
```json
{
  "structural_system": "rc_rahmen",
  "structural_confidence": 0.8,
  "era_estimate": "1990-2000",
  "seismic_grade_estimate": 2,
  "element_updates": [
    {"id": "w1", "structural_role": "shear_wall", "confidence_adjustment": 0.0},
    {"id": "pw1", "structural_role": "partition", "confidence_adjustment": -0.1}
  ],
  "load_paths": [
    {"description": "屋根スラブ→梁→柱→基礎→地盤"}
  ]
}
```
JSONのみで回答。"""


# ─── Pass 4: 検証する (Validate) — 再投影チェック ──────────────

PASS4_VALIDATE = """あなたは品質管理担当の検図AI（ダブルチェック担当）です。
以下は図面解析で生成された建物モデル（可視+推論要素）です:

{full_model_json}

原図面と比較して以下をチェックしてください:

1. **欠落**: 図面に見えるが抽出されていない要素はないか
2. **幽霊**: 図面にないのに生成されている要素はないか
3. **寸法**: 抽出した寸法は図面記載と一致するか
4. **構造整合**: 物理的に不可能な構成（柱のない梁、壁のない窓等）はないか
5. **接続**: 壁が途切れている、柱が浮いている等の不整合はないか

## 出力（JSONのみ）
```json
{
  "validation_passed": true,
  "issues": [
    {
      "severity": "warning",
      "element_id": "w5",
      "issue_type": "missing_element",
      "description": "南東角に柱のような線が見えるが抽出されていない"
    }
  ],
  "overall_score": 0.85,
  "confidence_adjustments": [
    {"element_id": "inf_f1", "new_confidence": 0.7, "reason": "基礎位置が柱から200mmずれている"}
  ]
}
```
JSONのみで回答。"""


# ─── v1.0互換: シングルパスプロンプト ──────────────────────────

LEGACY_SINGLE_PASS = """あなたは建設図面の解析AIです。与えられた2D建設図面（平面図）から、
全ての建築要素を正確に抽出してください。

## 抽出する要素
1. 壁 (wall): 始点(x1,y1)、終点(x2,y2)、厚さ(mm)、高さ(mm)
2. 柱 (column): 位置(x,y)、サイズ(mm)、高さ(mm)
3. 梁 (beam): 始点・終点、幅(mm)、成(mm)
4. スラブ (slab): 範囲、厚さ(mm)
5. ドア (door): 位置、幅(mm)、所属壁ID
6. 窓 (window): 位置、幅(mm)、高さ(mm)、所属壁ID
7. 部屋 (space): 名前、境界壁ID、面積(m2)

座標系: 左下原点、右X+、上Y+、単位mm

## 出力（JSONのみ）
{
  "metadata": {"drawing_type": "", "scale": "", "unit": "mm", "confidence": 0.0},
  "storeys": [1],
  "storey_heights": {"1": 3000},
  "elements": [...],
  "spaces": [...]
}
JSONのみで回答。"""
