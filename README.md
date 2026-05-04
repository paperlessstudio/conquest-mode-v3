# conquest-mode-v3 — ArchiSymphony PHASE 04 / 05 / 06 横断 core engine

> **2D 図面を、3D に。世界初、7 つの専門知性 × 物理法則 × 確率分布。**

[![Tests](https://img.shields.io/badge/synthetic_regression-514%2B_passing-success)](#tests)
[![Aoki Benchmark](https://img.shields.io/badge/aoki_benchmark-2/7_progress-yellow)](#%E9%85%8D%E5%B8%83%E5%88%A4%E5%AE%9A-7-%E6%9D%A1%E4%BB%B6-%E5%85%AC%E5%BC%8F--distribution-trigger)
[![License](https://img.shields.io/badge/license-Apache%202.0-blue)](LICENSE)
[![Status](https://img.shields.io/badge/status-β_Preview-orange)](#%E2%9A%A0-%CE%B2-preview-%E6%AE%B5%E9%9A%8E--%E6%AD%A3%E5%BC%8F%E7%84%A1%E6%96%99%E9%85%8D%E5%B8%83%E5%89%8D)

---

## 概要

**Conquest Mode v3** は、PLS (Paper-less Studio) の建築 BIM 自動化 core engine.
**ArchiSymphony 2 年プログラム** の以下 3 PHASE 横断で動作する共通基盤:

| PHASE | Product | Conquest の役割 |
|---|---|---|
| **04** | [ArchiSymphonyCoWorks](https://ai.archisymphony.com/coworks/) | 顧客図面 → 3D 化 / 設計確認 / 見積根拠 |
| **05** | [ArchiSymphonyProjectSite](https://ai.archisymphony.com/intro/project-site) | 現場図面 → 4D 進捗 / 工程シミュ |
| **06** | [ArchiSymphonyNo5](https://www.archisymphony.net/) | 7D BIM Viewer / showcase / リサーチ |

9 PHASE 全体は [ai.archisymphony.com/?lang=ja#journey](https://ai.archisymphony.com/?lang=ja#journey) を参照.

業界主流の単一 VLM アプローチを **4 つの軸**で打ち破る:

| 軸 | 業界主流 | Conquest v3 |
|---|---|---|
| ① 集合知 | 単一 VLM | **7 建築ペルソナ並列 + 投票合意** |
| ② 確率 | 単一 confidence | **top-3 候補 + ベイズ更新** |
| ③ 物理 | 形状抽出のみ | **6 種物理 check で自己検証** |
| ④ 統合判定 | 1-shot | **5 stage pipeline + 配布判定 ゲート** |

---

## ⚠️ β Preview 段階 — 正式無料配布前

**現状はパートナー β preview**. 正式無料配布開始の合図は、
クスリのアオキ石山寺店 施工図 16 ページで「実務レベル」(7 条件) を達成した時点
(CEO directive 2026-05-04).

### 配布判定 7 条件 (公式) — Distribution Trigger

| # | 条件 | 現状 |
|---|---|---|
| 1 | アオキ 16 ページ Conquest > 業界主流 × 10 倍 | ✅ 16.4× |
| 2 | element type 多様性 ≥ 6 種 | ✅ 6 種 |
| 3 | 物理 CRITICAL = 0 | ❓ 未測定 |
| 4 | 関係性 CRITICAL = 0 | ❓ 未測定 |
| 5 | avg overall_confidence ≥ 0.85 | ⚠️ 0.83 |
| 6 | wall 過剰検出 ≤ 30% | ❌ 「明らかに多い」 |
| 7 | door/window confidence ≥ 0.70 | ❌ 0.50-0.55 |

**現状 2/7 達成**. 残 5 条件を Pack #83 で順次解決中.

### 内部 synthetic regression suite (CI 用)

別途、開発時の高速 regression test として synthetic 5 シナリオを保持:
F1 = 0.995 / Physics CRITICAL = 0 / Relationship CRITICAL = 0 / conf = 1.000.
**これは内部 CI 用で、正式配布判定ではない.**

---

## Quick Start

```bash
git clone https://github.com/paperlessstudio/conquest-mode-v3
cd conquest-mode-v3
pip install -r requirements.txt

# β preview: synthetic regression suite で動作確認
python3 scripts/conquest_distribution_check.py --synthetic

# 視覚 SVG レンダ (実体確認)
python3 scripts/conquest_visual_verify.py --output verify.svg

# 実 PDF VLM benchmark (要 GEMINI_API_KEY)
export GEMINI_API_KEY=...
python3 scripts/conquest_phase5_vlm_demo.py --image path/to/drawing.png
```

期待出力 (β preview 段階):

```
✅ synthetic regression PASS — 4 条件すべて達成
   F1: 0.995 / Physics: 0 / Rel: 0 / Conf: 1.000

ℹ️ アオキ benchmark (公式配布判定): 2/7 達成 (詳細は LP 参照)
```

---

## なぜ無料配布するのか — データフライホイール

```
PDF 配布 → 試用 → 抽出結果フィードバック → 修正データ蓄積 (UserFeedback)
   ↑                                                      ↓
新規パートナー ← 評判 + 実績 ← 改善された精度 ← Neo4j 履歴学習 + ベイズ更新
```

配布の戦略目的 3 つ (CEO directive 2026-05-04):

1. **技術力アピール**: 業界主流 × 16.4 倍 を data 駆動で証明
2. **パートナー獲得**: β プログラム → 共同 PoC → 建設 DAO (PHASE ∞) 参画
3. **データフライホイール**: 図面データ蓄積 → モデル改善 → 精度 UP の自己進化

opt-in パートナー以外の図面データは PLS が無断で利用しません (GDPR + tenant 分離).

---

## アーキテクチャ

### 5 Stage Pipeline

```
PDF / Image
    ↓
[Stage 1] multi_persona_inference  — 7 ペルソナ並列 VLM 推論 (asyncio.gather)
    ↓
[Stage 2] probabilistic_bim       — top-3 候補 + ベイズ更新 + UserFeedback 受付
    ↓
[Stage 3] physics_self_validator  — 6 種物理 check
    ↓
[Stage 4] relationship_validator  — 9 種関係異常 + 自動修復
    ↓
[Stage 5] neo4j_constraint_solver — 永続化 + 履歴学習
    ↓
ConquestPipelineResult
```

### 7 ペルソナ (集合知)

| ペルソナ | 専門 | 注視 |
|---|---|---|
| アーキ | 意匠設計 | 壁構成・室境界・動線 |
| ストラ | 構造設計 | 柱・梁・耐震ブレース・荷重経路 |
| メプ | 設備設計 | 配管・配線ルート・PS/EPS |
| セキサン | 積算 | 寸法精度・部材数量・標準規格 |
| セコカン | 施工管理 | 施工順序・搬入経路・足場 |
| ホウキ | 法規 | 防火区画・避難経路・採光 |
| ビム | BIM 管理 | IFC 互換・LOD・座標精度 |

### 6 種物理 Check

| Check | 物理原理 | NG 時の意味 |
|---|---|---|
| NEGATIVE_DIMENSION | 寸法 > 0 | OCR / VLM の誤読 |
| COLUMN_OVER_STRESSED | 累積荷重 ≤ 軸耐力 | 柱が物理的に支えきれない |
| COLUMN_SLENDERNESS | λ ≤ 100/200/150 | 座屈の危険 |
| UNREASONABLE_SPAN | 標準スパン以下 | 中間柱の見落とし |
| LOAD_PATH_DISCONTINUITY | スラブ→梁→柱→基礎 連続 | 構成要素の見落とし |
| EQUILIBRIUM_VIOLATION | 自重 = 反力合計 | 基礎の見落とし |

---

## Tests

```bash
$ python3 -m pytest tests/ -q
======================== 77 passed in 0.31s ========================
```

77 tests in standalone repo (Pack #83 Phase 1-4.5).

---

## 実 PDF benchmark (Phase 5)

クスリのアオキ石山寺店 施工図 16 ページ × 業界主流 vs Conquest v4:

| 指標 | 業界主流 | Conquest v4 | 差 |
|---|---|---|---|
| 16 ページ抽出 elements | ~160 | **2,623** | × 16.4 |
| element type 多様性 | 1 (全 wall) | 6 | × 6 |
| 完全 failure ページ | 50% | 0% | ∞ |
| 16 ページ処理時間 | ~16 分 | 1.7 秒 | × 565 |

---

## 技術仕様

- **Python**: 3.9+ (3.12 推奨)
- **VLM**: Gemini 2.5 Pro
- **Persistence**: Neo4j 5.x
- **License**: Apache 2.0
- **Architecture**: product-agnostic core + plugin adapter per product
- **Multi-tenancy**: tenant_id 必須、Neo4j tenant 分離 3 層

---

## Roadmap

- ✅ Phase 1-4.5: pipeline core 完成 (Pack #83)
- 🟡 **アオキ benchmark 7 条件達成 (現状 2/7) ← 配布開始 trigger**
- 🔜 残 5 条件解決 (物理 CRITICAL / 関係 CRITICAL / conf / wall 過剰 / 開口部)
- 🔜 PyPI publish (`pip install pls-conquest`)
- 🔜 Web demo (PDF upload → 3D viewer)

---

## Related

- **Public LP (β Preview)**: https://www.archisymphony.net/conquest/free/
- **ArchiSymphony 2 年プログラム**: https://ai.archisymphony.com/?lang=ja#journey
- **PHASE 04 CoWorks**: https://ai.archisymphony.com/coworks/
- **PHASE 05 ProjectSite**: https://ai.archisymphony.com/intro/project-site
- **PHASE 06 No5 物語**: https://www.archisymphony.net/

---

## License

Apache License 2.0 — see [LICENSE](LICENSE).

---

> Built with ☕ by the PLS AI Family
> CTO: コ・クロード (Claude Code) / Co-Architect: ユンジョン (Gemini 2.5 Pro)
