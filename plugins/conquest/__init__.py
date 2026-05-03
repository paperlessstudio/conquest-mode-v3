"""Conquest Mode — 2D図面→4D BIM（3D+施工手順）自動生成パイプライン.

PLS ArchiSymphonyNo5のキラー機能。AI家族全員の総力戦。

Pipeline:
  Stage 0: 前処理（PDF/DWG/画像→正規化）
  Stage 1: VLM図面解析（Gemini 2.5 Pro Vision）
  Stage 2: 施工手順推論（構造依存グラフ→トポロジカルソート）
  Stage 3: IFC 4.0生成（ifcopenshell + 施工フェーズ属性）
  Stage 4: 検証（寸法照合 + ビジュアル比較）
"""
