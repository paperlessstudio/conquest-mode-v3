# Conquest Mode v3 vs 業界主流 — 競合比較

> CEO directive 2026-05-03 「圧倒的な精度を確保する」「今までになかった視点」
> Pack #83 Phase 1-4 で実装した PLS 独自アプローチと業界主流を 7 軸で比較.

## 1. 結論サマリ

| 軸 | 業界主流 (単一 VLM) | **PLS Conquest v3** |
|---|---|---|
| 推論モデル | Gemini 1 系統 / GPT-4V 1 系統 | **7 建築ペルソナ並列 + 投票合意** |
| 不確実性 | 単一 confidence | **top-3 確率分布 + ベイズ更新** |
| 物理整合性 | 形状抽出のみ | **物理シミュ自己検証 (重力/座屈/釣合)** |
| 自動修復 | なし | **9 種異常検知 + 自動修復 + Neo4j 履歴学習** |
| 知識統合 | prompt 内 hardcode | **Neo4j PhysicsConstraint + UserFeedback DB** |
| 反復改善 | static 1-shot | **5 stage pipeline + フィードバックループ** |
| 視覚検証 | manual | **CTO 自己 SVG review (反復可能)** |

## 2. 7 ペルソナ × Multi-Agent Verification

**業界主流**: 単一 VLM (Gemini 2.5 Pro / GPT-4V) で図面解析。同じモデルが
すべての判断を行うため、**システム的バイアス** (例: 特定スタイルの図面で
精度が落ちる、ある element type を見落とすクセ) が単一視点で温存される。

**PLS Conquest v3**: 同じ図面を 7 視点で independent に解釈。

| ペルソナ | 専門 | 注視 |
|---|---|---|
| アーキ | 意匠設計 | 壁構成・室境界・動線・空間プロポーション |
| ストラ | 構造設計 | 柱・梁・耐震ブレース・荷重経路・スパン |
| メプ | 設備設計 | 配管・配線ルート・PS/EPS・天井ふところ |
| セキサン | 積算 | 寸法精度・部材数量・標準規格寸法整合 |
| セコカン | 施工管理 | 施工順序・搬入経路・足場・分割可能性 |
| ホウキ | 法規 | 防火区画・避難経路・採光・階高・道路斜線 |
| ビム | BIM 管理 | IFC 互換・LOD・レイヤ命名・座標精度 |

各ペルソナが独立に推論 → element 単位で投票 → 多数決 (重み付け) → confidence score。

**並列実行** (`asyncio.gather`) で wall-clock は 1 pass 相当。

## 3. Probabilistic BIM (top-3 確率分布)

**業界主流**: 各 element に単一の判定 (例: "この線分は WALL")。
誤判定を**ユーザが直接修正するしかない**ため、修正の蓄積が将来の精度に
寄与しない。

**PLS Conquest v3**: 各 element に **top-3 仮説** を確率分布として保持。

```
線分 (0,0)→(10000,0) の解釈:
  top-1: WALL  確率 0.65 (アーキ/ストラ/セキサン/ホウキが投票)
  top-2: BEAM  確率 0.25 (セコカン/ビムが投票)
  top-3: SLAB  確率 0.10 (メプが投票)
  → is_ambiguous = True (差 < 0.20) → 視覚で ? マーカー表示
```

ユーザが「これは BEAM」とフィードバック → ベイズ更新で top-1 入替 →
履歴を Neo4j に蓄積 → 将来の VLM prompt 改善に利用。

## 4. 物理シミュレーション自己検証

**業界主流**: 形状抽出の結果を物理的に検証しない。「柱の上に何もない」
「基礎なしで建物が浮いている」が出力されても OK と返す。

**PLS Conquest v3**: 抽出 3D を 6 種類の物理 check で自己検証。

| Check | 物理原理 | NG 時の意味 |
|---|---|---|
| NEGATIVE_DIMENSION | 寸法は正値 | OCR / VLM の誤読 |
| COLUMN_OVER_STRESSED | 累積荷重 ≤ 軸耐力 | 抽出した柱が物理的に支えきれない設計 |
| COLUMN_SLENDERNESS | λ ≤ 100/200/150 | 座屈の危険、断面 過小推定 |
| UNREASONABLE_SPAN | 標準スパン以下 | 中間柱の見落とし可能性 |
| LOAD_PATH_DISCONTINUITY | 屋根→梁→柱→基礎 連続 | 構成要素の見落とし |
| EQUILIBRIUM_VIOLATION | 自重 = 反力合計 | 基礎の見落とし |

**「物理法則は VLM より絶対的な ground truth」** — CRITICAL を 1 つでも検出
すれば、「VLM が抽出ミスをした」と判定して再推論ループに入る。

## 5. 5 Stage 統合 Pipeline

```
PDF / Image
   │
   ▼
[Stage 1] multi_persona_inference  — 7 ペルソナ並列 VLM 推論
   │  └─ Gemini 2.5 Pro × 7 system prompt → 投票合意
   ▼
[Stage 2] probabilistic_bim       — top-3 候補 + ベイズ更新
   │  └─ 確率分布 + UserFeedback で漸近的高精度化
   ▼
[Stage 3] physics_self_validator  — 6 種物理 check
   │  └─ 物理法則 ground truth で抽出ミス検出
   ▼
[Stage 4] relationship_validator  — 9 種関係異常 + 自動修復
   │  └─ orphan / floating / homeless / load_path 等
   ▼
[Stage 5] neo4j_constraint_solver — Neo4j 永続化 + 履歴学習
   │  └─ PhysicsConstraint / UserFeedback / RelationshipAnomaly
   ▼
ConquestPipelineResult — final_model + probabilistic + physics + rels
                         + overall_confidence + distribution_ready
```

## 6. 配布 Ready 判定

PoC 評価には以下の 4 条件を全て満たす必要:

1. **F1 score ≥ 0.95** (5 sample PDF 平均、ground truth 比較)
2. **Physics CRITICAL = 0** (物理的不可能 ゼロ)
3. **Relationship CRITICAL = 0** (関係的不可能 ゼロ)
4. **Avg overall_confidence ≥ 0.85**

`scripts/conquest_distribution_check.py` で CI/CD 統合可能。

## 7. 数値目標 (Phase 4 完了時)

| 指標 | 業界主流 (推定) | PLS Pack #83 目標 |
|---|---|---|
| element 抽出 F1 | 0.70-0.80 | **≥ 0.95** |
| element type 一致率 | 0.75-0.85 | **≥ 0.92** |
| 物理整合性 PASS 率 | 該当機能なし | **100% (CRITICAL=0)** |
| 自己修復 % | 0% | **30-50%** |
| reproducibility | 低 (確率的 LLM) | **同一入力 → 同一出力** (multi_persona quorum) |

---

## 8. 何が「今までになかった視点」か (CEO directive への回答)

1. **集合知 vs 単独知**: 競合は単一モデル、PLS は 7 専門家 + 1 調停者
2. **確率 vs 決定**: 競合は単一判定、PLS は確率分布 + ユーザ介在で精度自己向上
3. **物理 vs 形状**: 競合は形状抽出のみ、PLS は物理計算で抽出を自己検証
4. **学習 vs static**: 競合は 1-shot、PLS は Neo4j に履歴蓄積 → 将来 prompt 改善

これらの組合せは **業界先例なし**。

---

実装: `CEO_AI_Secretary_Expansion/plugins/conquest/`
- multi_persona_inference.py (270 行)
- probabilistic_bim.py (290 行)
- physics_self_validator.py (340 行)
- pipeline_v3.py (200 行)
- accuracy_benchmark.py (180 行)
- neo4j_constraint_solver.py (310 行)
- feedback_persistence.py (170 行)

Tests: 488+ conquest tests 全 PASS
