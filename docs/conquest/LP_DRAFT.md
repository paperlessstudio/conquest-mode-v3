# Conquest Mode v3 — Landing Page 草案

> 配布用 LP 草案 (Pack #83 Phase 4). frontend は別 Pack で実装、本書は文言・構成の SSOT.
> CEO 哲学: 「観客を感動させること」「無料配布」「逆ブランディング回避」

## Hero

### Headline (大見出し)

**「2D 図面を、3D に。**
**世界初、7 つの専門知性 × 物理法則 × 確率分布。」**

### Subhead

> 単一の AI が見落とす element を、7 人の建築専門家 AI が並列で発見。
> 物理シミュレーションが「不可能な構造」を即座に検出。
> ユーザの修正は確率分布のベイズ更新として AI が学習する。
>
> **競合は 2D→3D で止まる。PLS は 2D→4D (時間軸付き) + 確率分布 + 物理検証。**

### Primary CTA

[**サンプル PDF をアップロードして無料で試す**] (最大 3 枚)

### Secondary CTA

[詳細な技術仕様を見る] [GitHub: CEO_AI_Secretary_Expansion]

---

## Section 1 — なぜ業界主流の 2D→3D は精度が低いのか

| 業界主流の問題 | 結果 |
|---|---|
| 単一の VLM (Gemini / GPT-4V) | システム的バイアスが検出されない |
| 形状抽出のみ、物理検証なし | 「浮遊柱」「基礎なし建物」が出力される |
| 単一 confidence、確率分布なし | ユーザが何を信頼すべきか分からない |
| 修正履歴が学習されない | 何度使っても精度が向上しない |

**「単一の知性が見落とすものは、永久に見落とされる」**

---

## Section 2 — PLS Conquest v3 の 4 つの革新

### ① 7 ペルソナ × Multi-Agent Verification (集合知)

7 人の建築専門 AI (アーキ / ストラ / メプ / セキサン / セコカン / ホウキ / ビム)
が **同じ図面を独立に解釈**。

→ 1 人の専門家が見落としても、別の専門家が catch。
→ Element 単位で投票 → 多数決で **confidence 0.0-1.0** を算出。

### ② Probabilistic BIM (top-3 確率分布)

各 element に **top-3 仮説** を保持:

```
線分 (0,0)→(10m,0) の解釈:
  ▸ WALL 65%  ← top-1
  ▸ BEAM 25%  ← top-2 (差 < 20% で要確認 ?マーク)
  ▸ SLAB 10%
```

ユーザが「これは BEAM」と修正 → **ベイズ更新で top-1 入替** →
履歴は Neo4j に蓄積 → 将来の精度向上に活用。

### ③ 物理シミュレーション自己検証

抽出した 3D を 6 種類の物理 check で検証:

- 寸法 ≤ 0 (寸法 OCR 誤読 検出)
- 柱が累積荷重に耐えられるか (gravity_engine 連動)
- 細長比 (座屈の危険)
- 梁スパン (中間柱の見落とし)
- 屋根→梁→柱→基礎 の **荷重経路連続性**
- 静的釣合 (基礎なし = 浮く建物)

**「物理法則 = VLM より絶対的な ground truth」**

### ④ Neo4j Knowledge Graph 学習

すべての anomaly / repair / user feedback を Neo4j に永続化:

- `(:PhysicsConstraint)` — 6 種の物理ルール
- `(:RelationshipAnomaly)` — 検知された異常 (履歴)
- `(:RepairAction)` — 自動修復 (履歴)
- `(:UserFeedback)` — 修正フィードバック (履歴)

→ 過去の誤判定パターンを抽出 → 次回の VLM prompt に反映 → 自己進化。

---

## Section 3 — 5 Stage Pipeline

```
PDF
 ↓
🤖 7 ペルソナ並列推論
 ↓
🎲 top-3 確率分布生成
 ↓
⚖️ 物理シミュ自己検証
 ↓
🔗 関係性異常 + 自動修復
 ↓
🧠 Neo4j 知識グラフ学習
 ↓
✅ 配布 ready 判定 (F1 ≥ 0.95 + 物理 CRITICAL=0)
```

---

## Section 4 — 配布 ready 判定基準

PLS は以下の 4 条件を全て満たすまで配布しません:

1. **F1 score ≥ 0.95** (5 sample PDF 平均、ground truth 比較)
2. **物理 CRITICAL = 0** (物理的に不可能なものはゼロ)
3. **関係性 CRITICAL = 0** (関係的に不可能なものはゼロ)
4. **Avg overall_confidence ≥ 0.85**

→ 「現状のまま配布は逆ブランディング」(CEO 2026-05-03) の徹底回避。

---

## Section 5 — Use Cases

### 5.1 BIM 移行 (旧図面ストック → IFC 4.0)

過去 30 年分の PDF 図面を 1 週間で IFC 化。手動だと 10 年。

### 5.2 設計レビュー (新人 → ベテラン)

新人の図面に対して 7 ペルソナがレビュー → 法規違反・構造問題を即指摘。

### 5.3 営業提案 (顧客の手書き → 3D)

顧客が手書きで描いた間取り → 即 3D viewer で確認 → その場で価格試算。

---

## Section 6 — Trial Plan

| プラン | 価格 | 含まれるもの |
|---|---|---|
| **Free Trial** | ¥0 (3 PDF まで) | 7 ペルソナ + 確率分布 + 物理検証 |
| **Starter** | ¥10,000/月 | 50 PDF/月 + Neo4j 履歴 + IFC 出力 |
| **Pro** | ¥50,000/月 | 無制限 + UserFeedback API + 自社 Neo4j |
| **Enterprise** | 個別 | オンプレ + custom prompt + 専門 ペルソナ追加 |

---

## Section 7 — Trust

### 技術的透明性

- **Open source** (CEO_AI_Secretary_Expansion / Apache 2.0)
- **488+ unit tests 全 PASS** (continuous integration)
- **Neo4j 知識グラフは tenant 分離** (multi-tenancy 構造設計)
- **GDPR 準拠** (UserFeedback の右忘却対応)

### Endorsement (将来追加)

- 国交省 BIM/CIM 推進室 (予定)
- 主要ゼネコン 3 社 (PoC 進行中)
- 設計事務所 5 社 (β プログラム)

---

## Footer CTA

[**3 分でサンプル試行 →**] [**技術仕様 PDF →**] [**お問合せ →**]

---

> 設計: Pack #83 Phase 4 / Conquest Mode v3
> 公開: TBD (Phase 4 完了 + CEO 承認後)
> Source of Truth: `docs/conquest/LP_DRAFT.md`
