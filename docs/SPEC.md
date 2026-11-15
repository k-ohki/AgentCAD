# 仕様

最終更新: 2026-11-12（F2・U3・V1: フィーチャー・作図ツール・画面の操作を一般的な 3D CAD 寄りに拡張。「未実装」と書いた節は設計のみ）

## 1. 全体構成
```
app/   Web UI（Three.js）。表示と入力だけを担当し、状態は持たない（Python 側の写し）
  │    WebSocket（localhost）: 制御は JSON-RPC、メッシュはバイナリ
core/  Python。全ロジック。UI 無しで pytest により検証できる
  cad/solver     汎用拘束ソルバ（スケッチと合致で共用）
  cad/sketch     2D スケッチ
  cad/part       フィーチャーツリー・再生成
  cad/naming     トポロジカルネーミング・参照の解決
  cad/kernel     形状カーネルの抽象 IF（mock / usd_brep / occt）
  cad/assembly   アセンブリ・合致
  cad/doc        Undo/Redo（History）、式、Workspace、USD 保存（usdio）
  cad/api        RPC と HTTP サーバ
```
依存の向き: `solver ← sketch ← part → kernel, naming` / `solver ← assembly → part`。
solver は他のどれにも依存しない。UI・通信は `cad/api` より内側に入れない。

起動は 2 通り（UI と Python の間はどちらも同じ HTTP）:
- ブラウザ: `run.bat`（`python -m cad.api`）。開発・確認用
- デスクトップ（Tauri の殻、`app/src-tauri`、9.4 節）: `app` で `npm install` → `npm run tauri dev`（ビルドは `npm run tauri build`）

## 2. 拘束ソルバ（cad/solver）
### 2.1 モデル
- 変数は 1 本のベクトル `x`（固定フラグつき）
- 拘束 = 種類 `Kind` + 変数インデックス + 定数。`Kind.fn(p, c)` が残差のリストを返す
- 同じ Kind の拘束はまとめて評価する（バッチ）。`ad.py` の前進型自動微分で正確なヤコビアンを得て、
  疎行列に組み立てる。評価コストは拘束の「種類数」に比例し、個数にはほぼ依存しない
- `FuncConstraint`（任意の関数 + 差分ヤコビアン）は試作・テスト用の遅い経路

### 2.2 求解
双対形式の Levenberg-Marquardt:

    dx = W⁻¹Jᵀ (J W⁻¹ Jᵀ + λI)⁻¹ (−r)

- λ→0 で「重み付き最小ノルム Newton」。自由度が残る系では、今の形からの移動が最小の解に動く
  （触っていない部分はなるべく動かない）
- ステップで残差が減らなければ λ を上げる（最急降下に近づく）。冗長な拘束があっても λ が正則化する
- 小さい系は密行列、大きい系は疎行列（`DENSE_LIMIT`）
- 収束判定: 最大残差 < 1e-7 × スケール（スケール = max(1, |x| の最大)）で `ok`、それ以外は `inconsistent`

### 2.3 ドラッグ
掴んだ変数を目標値に置き、その変数の移動コスト W を大きく（1e3）して解く。
掴んだ点はカーソルに留まろうとし、他が追従する。拘束上届かないときは満たせる近い点で止まる。
解けなければ元の状態に戻す。

### 2.4 診断（`diagnose`）
- ヤコビアンの行を正規化し、追加順にグラム・シュミット直交化してランクを求める
- 自由度 = 自由変数の数 − ランク
- 冗長: 満たされていて、かつ自由度を 1 つも奪わない拘束（一部だけ従属するものは正常扱い。D7）
- 矛盾: 解けないとき、残差が残った拘束のうち後から追加したものから順に外し、解けるようになる
  最小の組を原因として返す
- 動ける変数: 零空間に成分を持つ変数。`dof_of(変数群)` でその部分だけの自由度（部品ごとの DOF）
- `x` は変更しない

### 2.5 性能の基準
400 変数・399 拘束のスケッチで、求解 6ms / 診断 26ms / ドラッグ 6ms（実測）。
予算はドラッグ 1 回 100ms 以内（テストで監視）。

## 3. スケッチ（cad/sketch）
- エンティティ: 点、線、円（中心 + 半径変数）、円弧（中心・始点・終点 + 半径変数。両端が円上にある
  ことは暗黙拘束）、作図線フラグ
- 幾何拘束: 一致、水平、垂直（線または 2 点）、平行、直角、等しい（線の長さ / 半径）、点-線上、
  点-円上、中点、正接（線-円/円弧、円/円弧同士の外接・内接）、同心、対称（2 点と線）、固定
- 寸法拘束: 距離（点-点 / 点-線）、水平距離、垂直距離、長さ、半径、直径、角度（線 1→線 2 の符号付き、度）
  - 寸法の文字の位置 `label`（既定の位置からのずれ、スケッチ座標。None なら自動）。形が変わっても相対位置を保つ
- エッジ上（`on_edge`、エンティティ変換）: 線・円・円弧を、モデルのエッジ（永続名 `source`）の投影に固定する。
  線は両端、円は中心と半径、円弧は中心・始点と終点の向き（半径は暗黙拘束で決まる）。再生成のたびに投影し直す（3.2）
  - 値を省略すると現在の形状から測った値で作る（一般的な 3D CAD の寸法挿入と同じ）
- 参照の型は作成時に検査する（不正なら `ValueError`、何も追加されない）
- 解の枝: 正接の側、点-線距離の側、水平/垂直距離の向きは作成時の形状から符号として記録する
- 接点が分かっている正接: 2 要素が点を共有している場合（円弧の端点、一致拘束、点-円上拘束）は
  接点を使って定式化する（線: 半径方向 ⟂ 線 / 円同士: 2 つの中心と接点が一直線）。
  「距離 = 半径」型の式は重根になり自由度を誤判定するため
- 円同士の正接は、作成時の形状に近い方（外接 / 内接）を選んで保持する
- `solve()` の結果: 状態（ok / inconsistent）、自由度、全体の定義状態（under / full / over）、
  冗長・矛盾拘束、エンティティごとの状態（UI の色分け）。解けないときは形状を変えない
- `drag(点, x, y, diagnose=False)`: マウス移動ごとは診断を省き、離したときに診断する
- `delete(id)`: 依存するエンティティ・拘束も削除
- プロファイル: 非作図要素の閉ループ（端点座標で接続、全端点の次数 2）と単独の円

### 3.1 編集操作（cad/sketch/ops.py）
定義を書き換えるだけで求解はしない（呼び出し側が続けて `solve()`）。
- `fillet(線1, 線2, 半径)`: 角を共有する 2 線を円弧でつなぐ。線を接点まで縮め、正接 ×2 と半径寸法を付ける。
  元の角の点は、寸法などから参照されていれば仮想交点として残す（両線の延長上に拘束）。
  線の長さ寸法は「仮想交点から反対側の端点までの距離」に付け替える（寸法の意味が変わらない）
- `trim(要素, x, y)`: (x, y) 付近の区間を、他の要素との交点まで切り取る（パワートリム相当）。
  交点なし → 要素を削除 / 片側だけ → 端点を移動 / 両側 → 2 つに分割（線は同一直線上、円弧は同心・等半径）/
  円は交点 2 つ以上で円弧になる（ID は同じ）。新しい端点は切った相手の上に拘束する。
  切られた線の長さ寸法は削除する

- 中心矩形（`Sketch.center_rect`）: 矩形 + 対角の作図線 2 本 + 中心点が対角線の中点（一般的な 3D CAD と同じ作り）
- ミラー（`ops.mirror`）: 線（軸）について鏡映コピーし、点ごとに対称拘束でつなぐ。軸上の点は共有、円は半径が等しい、
  円弧は始点と終点を入れ替える（反時計回りを保つ）。自分自身に重なる要素は作らない

- 追加の作図（U3）: 中心線（作図線の線）、3 点円弧、正接円弧（線・円弧の端点から。正接拘束つき）、
  スロット（中心線 + 半円 2 つ + 線 2 本。正接 ×4 と「半径が等しい」）、正多角形（外接円の作図線 + 頂点が円上 + 辺が等しい）
- 面取り（`ops.chamfer`）: 角を距離 d1 / d2 で切り落とす。元の角は仮想交点として残し、そこからの距離寸法 2 つで決める
- 延長（`ops.extend`）: 線の端を、その先で最初に当たる要素まで伸ばし、端点を相手の上に拘束する（長さ寸法は外す）
- オフセット（`ops.offset`、D45）: つながった線・円弧は鎖として同じ側へ。線は「平行 + 元の線からの距離」、
  円・円弧は中心を共有して「半径の差」（`offset` 拘束）。正接でつながる所は新しい端点を「元の端点と中心を結ぶ線上」
  （`collinear` 拘束）に置き、その線からは距離（両端とも正接なら平行も）を外す。寸法は最初の 1 つが親で、
  残りは式で親に従う（親を消すと子は今の値で独立する）。つながっていない端は未定義のまま

### 3.2 エンティティ変換（cad/sketch/fit.py、SketchFeature.update_projections）
- エッジの折れ線（カーネルの `edge_polyline`）をスケッチ平面に投影し、つなげて 直線 / 円（閉じている）/ 円弧（反時計回り）に当てはめる。
  表示用の折れ線は弦の中点も含むので、円の半径は外側の点で取り、ずれは半径の 0.5% まで許す
- 直線・円・円弧にならないもの（自由曲線、傾いた面の円 = 楕円、平面に垂直な線 = 点）は変換しない（理由を返す）
- 投影した点は要素ごとに別（共有すると固定が重なって冗長になる）。輪郭は端点の座標でつながる
- 再生成: 投影元のエッジを探し直して形を合わせる（一般的な 3D CAD の「エッジ上」と同じく追従）。
  見つからない・形の種類が変わったときは前の位置のまま警告。投影を持つスケッチはボディに依存する（uses_body）

### 3.3 外部参照（ほかのスケッチ。D52）
- `ext_point`（点を、ほかのスケッチの点に一致）/ `ext_line`（点を、ほかのスケッチの直線の上に）。`source = "スケッチID:要素ID"`
- `data` は参照先を今のスケッチ平面に投影した座標（点 [u, v] / 線 [u1, v1, u2, v2]）。再生成で `external_geometry` が計算し直す。
  参照先が無効・見つからないときは前の位置のまま警告。参照先はツリーで前にあるスケッチだけ（`sketch.add` の点 `{"ext": source, x?, y?}`）
- D54 で広げた source: `"S:L@mid"`（線の中点）、`"edge:名前@vertex|mid|center"`（モデルのエッジの端点 / 中点 / 円・円弧の中心）、
  `"edge:名前"`（モデルの直線エッジの上）。@vertex は前の位置（作るときはクリック位置）に近い端
- `sketch.reference {doc, source, x?, y?}`: スマート寸法の相手になる参照の要素を作る（点 → 固定点、ほかのスケッチの線 → 作図線、
  円 → 中心、モデルのエッジ → 投影した作図線・作図円）。created に要素 ID
- `doc.state.sketches`: スケッチごとに id・名前・平面・used（フィーチャーに使われたか）・visible・shown（表示するか）と、
  ワールド座標の点・曲線（折れ線）。UI はこれを 3D に描き、スナップ先にする。`sketch.visible {doc, feature, visible}` で表示を切り替える

## 4. フィーチャーツリー（cad/part）

- スイープ `sweep`（D58）: profile・path（スケッチ ID）・op。`feature.add {type: "sweep", sketch: 断面, path?: 経路, op}`（path を省くと自動で選ぶ）。
  カーネル: `Kernel.sweep(profile, path)`（path は始点から順・向きをそろえた線・円弧。"arc_cw" は時計回り）
- 穴ウィザード `hole`（D57）: sketch（位置のスケッチ）・kind（cbore / csink / clearance / tap / simple）・size（M2〜M20 / custom）・end（blind / through）・
  depth・diameter・cbore_diameter・cbore_depth・csink_diameter・csink_angle・tip_angle・reverse。`feature.add {type: "hole", sketch, kind, size, depth, end}`

- フィーチャー = シリアライズできる定義 + `build(ctx)` + `deps()`
- 面上スケッチの座標系: 原点 = 部品原点の面への投影、x = 世界 X の面への投影（D22）
- 実装済み: スケッチ（基準平面 / 面上 / 参照平面）、押し出し、回転（作図線が軸）、フィレット、面取り、シェル、参照平面、
  直線パターン、円形パターン、ミラー（4.2 節）
- 再生成の規則:
  - 上から順に評価する。ボディを使うフィーチャーは直前のボディ出力フィーチャーに依存
  - 差分再生成: 定義（JSON）と依存先の結果バージョンが前回と同じなら結果を再利用
  - ロールバックバー: それ以降は評価しない。新しいフィーチャーはバーの位置に入る
  - 抑制: 飛ばす（ボディは素通し）
  - エラー隔離: 失敗したフィーチャーはエラーを表示し、ボディは素通しで続行。
    エラーのフィーチャーを参照するものは「参照先が無効」
  - 警告: スケッチが未定義、参照を付け替えた、形が変わらなかった（追加・カットの結果にそのフィーチャーの面が無い。D68）など。
    ツリーでは未定義・過剰定義のスケッチは ⚠ でなく名前の前の (-) / (+)
- Undo/Redo: `with doc.transaction(label):` 単位（`cad.doc.History`、スナップショット方式）

### 4.2 フィーチャーの定義（cad/part/features/。種類ごとのモジュール。D59）
- 参照の書式: 平面 `{"plane": "Front"|"Top"|"Right"}` / `{"face": 面の永続名}` / `{"feature": 参照平面の ID}`、
  向き `{"axis": "X"|"Y"|"Z"}` / `{"edge": 直線エッジの永続名}`、回転軸は向きに加えて `{"face": 円筒面の永続名}`
- 押し出し（`extrude`）: `op`（new / add / cut）、`reverse`、`end`（押し出し状態）
  - 向き: 追加は平面の法線、カットはその逆（材料の中へ）。`reverse` で反転
  - `blind`: 距離 / `mid`（中間平面）: 平面の両側に距離の半分ずつ / `through`（全貫通）: 向きの側のボディを抜けるまで /
    `through_both`: 両側。全貫通は直前のボディの範囲（法線方向）を測り、1% 余分に突き抜ける。その向きにボディが無ければエラー
  - 中間平面・全貫通は「スケッチ平面を法線方向へずらして押し出す」だけで作る（カーネルの追加機能は不要）
- 参照平面（`plane`）: `base`（平面の参照）から法線方向へ `offset`（`reverse` で逆）。結果は `data["plane"]`。
  スケッチの `plane` やミラーの平面から `{"feature": ID}` で参照する（依存関係に入る）
- 直線パターン（`lpattern`）: `seeds`（押し出し・回転の ID）、`direction`、`spacing`、`count`（元を含む個数）、`reverse`、
  2 方向目 `direction2`（None なら 1 方向）/ `spacing2` / `count2` / `reverse2`
- 円形パターン（`cpattern`）: `seeds`、`axis`、`count`（元を含む）、`angle`（360 なら 1 周に等間隔、それ以外はその範囲に等間隔）、`reverse`
- ミラー（`mirror`）: `seeds`、`plane`
- パターン・ミラーの作り方（D43）: 元のフィーチャーの「ツール形状を作る処理」（`tool(ctx, placement)`）を、
  置き直したスケッチ平面でもう一度実行し、元と同じ操作（追加 / カット）で重ねる。鏡映では平面内の v を反転して
  法線を鏡映する（平面が左手系にならないように。円弧は始点と終点を入れ替え、回転は軸の向きを逆にする）。
  カーネルに必要なのは押し出し・回転とブーリアンだけ。面の名前は `<パターンのID>~<番号>/…`、500 インスタンスまで
- 面取り（`chamfer`）: `edges`（永続エッジ名）、`distance`。usd-brep では `fillet_edges(xsect_type=0)`。
  円のフィレットが面に接する線どうしを平らにつなぐので、90° のエッジでは distance が各面の後退量（一般的な 3D CAD の「距離」）。
  90° でないエッジでは後退量が少し違う。新しい面の名前は `<ID>/chamfer/<元のエッジ>`
- シェル（`shell`）: `faces`（取り除く面の永続名。空なら閉じた中空）、`thickness`、`outward`（既定は内側）。
  usd-brep では `shell_brep(body, ∓thickness, faces_to_shell=…)`（面は `find_face` で探す）。
  壁の面の名前は `<ID>/wall/<厚さだけ離れた元の面>`（`nearest_offset_face`）
- フィーチャーの移動（`PartDocument.move`）: 順番を入れ替える。依存が逆転したものは再生成でエラー表示になる
- インポート（`import`。D71）: `source`（取り込んだファイルの名前）、`mesh`（頂点と三角形を zlib + base64）。
  パートの最初のボディだけ。面は三角形の向きから作り（`cad/kernel/meshbody.py`）、名前は `<ID>/F<番号>`。
  形を変える演算の相手にはできない（カーネルがエラー）。`doc.open` でメッシュのファイルを開くと作られる（feature.add では作らない）

### 4.1 式（cad/doc/expr.py）
- グローバル変数: `doc.variables.exprs["width"] = "100"`。変数同士は参照でき、循環はエラー
- 寸法の連動: フィーチャーは `exprs["distance"] = "height * 2"`、スケッチ寸法は `constraint.expr`。
  再生成のたびに評価して値を上書きする
- 参照できるもの: 変数、`フィーチャーID.パラメータ`、`スケッチID.拘束ID`。
  寸法は自分より上のフィーチャーのもの（と、同じスケッチ内で先に決まるもの）だけ
- 構文: 数値、+ − * / ** %、括弧、関数（sin cos tan asin acos atan atan2 sqrt abs min max
  floor ceil round rad deg）、pi。三角関数はラジアン。ast で解釈し eval は使わない
- 式のエラーはそのフィーチャーのエラーになる（ツリー全体は止まらない）
- アセンブリ: `assembly.variables` と `mate.expr`。エラー時は前回の値のまま解き、`expr_errors` に載せる

## 5. トポロジカルネーミングと参照（cad/naming）
- 面の名前: `<F>/side/<スケッチ要素ID>`、`<F>/start`、`<F>/end`、変形して残った面は元の名前、
  `<F>/fillet/<エッジ名>`
- エッジの名前: `E[<面名a>|<面名b>]`
- 同名が複数できた場合は `#2`, `#3`…。順序はカーネルの `sort_key`（形状から決まる値）で決める
- 参照の解決は `TopoNames.find_face / find_edge` に集約:
  1. 完全一致
  2. `#n` を無視して候補が 1 つならそれを採用（警告「参照を付け替えました」）
  3. それ以外は `DanglingReference`（候補一覧つき。UI で付け直す）
- スケッチ平面・フィレット対象・合致は、すべて永続名で保存しここで解決する

## 6. カーネル（cad/kernel）
要件:

| | 内容 |
|---|---|
| R1 | 平面プロファイル（線・円弧・円）の押し出し・回転 |
| R2 | ブーリアン（和・差・積） |
| R3 | エッジ指定のフィレット |
| R4 | 面・エッジの列挙、エッジの隣接 2 面 |
| R5 | 履歴: 結果の各面の由来（側面↔スケッチ要素 / 端面 / 変形元 / フィレット元エッジ） |
| R6 | メッシュ化（三角形 → 面） |
| R7 | 面の平面・円筒軸の取得、`sort_key` |
| R8 | 速度 |

- 受け入れ: `tests/test_kernel_conformance.py`（全カーネル共通。形状を計算しないカーネルは形状の項目を省く）
- 登録: `cad.kernel.available_kernels()`（import できたカーネルだけ載る）
- 実装:
  - `mock`: 形状を計算しない。位相と履歴だけ（上位層のテスト用）
  - `usd_brep`: 適合テスト全通過。`MeshTopoKernel` を土台にし、演算前に複製する（D19, D21）
  - `occt`: 未実装（D20 の条件を満たしたら）

### 6.1 MeshTopoKernel（cad/kernel/meshtopo.py）
履歴（R5）・隣接（R4）・平面（R7）を返さないカーネル向けの土台。サブクラスが実装するのは次だけ:
`_raw_extrude(plane, groups, distance)`, `_raw_revolve`, `_raw_boolean(op, a, b)`,
`_raw_fillet(raw, edge_points, radius)`, `_face_meshes(raw, chord)`, `_raw_size`, `_raw_volume`。
- プロファイルのループは `group_loops` で (外形, 穴) に分けて渡す（入れ子の深さの偶奇）
- フィレット対象のエッジは「エッジ上の点」で渡す（カーネル側で点からエッジを特定）
- ハンドル: 面 `("f", 形状ID, 番号)`、エッジ `("e", 形状ID, 番号)`（形状をまたいで一意）
- 許容値: 弦誤差 = モデル寸法 × `chord_rel`(0.1%)、判定の許容値 = 弦誤差 × 4。
  ただし平面の面（メッシュが誤差 0）との判定と `face_plane` には厳しい許容値 = モデル寸法 × `flat_rel`(1e-6) を使う
- 面の由来は代表点 7 個の多数決。「どの入力面にも乗らない」も 1 票に数える
- 同名の面（分割）に付ける `#n` の順序は、面の重心（許容値で丸めた座標）の順

## 7. アセンブリ（cad/assembly）
- 部品 = 参照元 + 姿勢（位置 3 + 四元数 4。|q|=1 を拘束に入れて自由度 6）
- 最初に追加した部品は固定
- 幾何の取得（`GeometrySource`）: `PartSource` は面の永続名 → 平面 / 円筒軸、`Origin`、
  `Front/Top/Right`。`StaticSource` はテスト用
- 合致（引数の順は自由。作成時に組み合わせを検査）:

  | 合致 | 組み合わせ → 外す自由度 |
  |---|---|
  | 一致 | 平面-平面 3 / 平面-点 1 / 平面-軸 2 / 点-点 3 / 軸-点 2 / 軸-軸 4 |
  | 距離 | 平面-平面 3 / 平面-点 1 / 平面-軸 2 / 点-点 1 / 軸-点 1 / 軸-軸 3（平行にして離す） |
  | 平行 | 平面・軸 同士 2 |
  | 直角 | 平面・軸 同士 1 |
  | 角度 | 平面・軸 同士 1 |
  | 同心 | 軸-軸 4 |
  | 正接 | 平面-円筒 2（軸が平面に平行で、距離 = 半径。半径はパートから毎回取る） |
  | ロック | 2 部品の相対姿勢を固定 6（幾何は指定しない） |

- 向き（`aligned`）: 平面-平面でのみ意味を持つ。一致の既定は逆向き（面を突き合わせる）。
  求解前に、動かせる側の部品を最小回転で正しい側へ回す（事前位置合わせ）。求解後に確認し、
  逆なら `flipped` に載せる。軸には向きがないので同心・平行では問わない
- 距離の側（`sign`）は作成時の配置から決める
- 結果: 状態、全体の自由度、部品ごとの自由度、冗長・矛盾・flipped。解けないときは姿勢を変えない
- ドラッグ: 部品位置を目標にして解く
- 合致は永続名で参照するので、パートを再生成して解き直すと部品が追従する
- 未実装: 円筒同士の正接、幅、ギア等の機械的合致、サブアセンブリ

## 8. 保存形式（cad/doc/usdio.py、S）
USD のテキスト形式（`.usda`）。pxr を使わず自前で書き、読む（D32）。単位 mm（`metersPerUnit = 0.001`）、`upAxis = "Y"`。
`customLayerData` の `agentcad:format` に `agentcad.part/1` / `agentcad.assembly/1`。

- パート `名前.usda`（defaultPrim `/Part`, kind=component）
  - `/Part/FeatureTree.agentcad:definition`: `PartDocument.to_dict()` の JSON 文字列。**開くときはこれだけを読む**（形状は再生成）
  - `/Part/Display`（Mesh）: 表示メッシュ。`primvars:agentcad:faceIndex`（uniform、三角形 → 面番号）、`agentcad:faceNames`（面の永続名）、`primvars:displayColor`（uniform、外観の色。D69）。
    `orientation` は三角形の向きから決める（体積の符号）
  - `/Part/Edges`（BasisCurves, linear）: エッジの線分。`primvars:agentcad:edgeIndex`、`agentcad:edgeNames`
  - `/Part/Body`: BRep のファイル `名前_body.usda`（usd-brep の `usd.export_brep` が書く omniSolid の BrepArray）を
    `prepend references = @./名前_body.usda@</World/…>` で参照する（型も参照先から来る）。`agentcad:body` にファイル名。
    カーネルが書けたときだけ（D33）。BrepArray が 1 個でなければ参照せず、ファイル名だけ書いて警告
- アセンブリ `名前.usda`（defaultPrim `/Assembly`, kind=assembly）
  - `/Assembly.agentcad:definition`: `Assembly.to_dict()` の JSON。部品の `source` はパートファイルの相対パス（`./parts/pin.usda`）
  - `/Assembly/<部品>`（Xform）: パートを `prepend references`、姿勢を `xformOp:transform`（行ベクトル規約）、`agentcad:component` に部品 ID
  - USD の prim は他のツールで見るための写し。開くときは JSON だけを読み、参照先のパートも開く（開いていればそれを使う）
- 保存の規則（`Workspace.save`）
  - 一時ファイルに書いてから置き換える。拡張子が無ければ `.usda` を付け、`.ucpart` / `.ucasm` は `.usda` に替える
  - アセンブリの保存では、使っているパートのうち「未保存」「変更あり」「旧形式」のものも保存する（D34）。
    未保存のパートはアセンブリと同じフォルダに `パート名.usda`（既存のファイルがあれば `_2`…）
  - 別の文書として開いているファイルには保存しない。同じファイルを開くと既に開いている文書に切り替わる
  - 変更の有無: 保存（読み込み）時の `to_dict()` と比べる（Undo で戻れば「変更なし」）
- 旧形式（E4 まで）: `.ucpart`（JSON）/ `.ucasm`（パート定義を中に含む JSON。D30）は開くことだけできる

### 8.1 ほかのソフトとのやりとり（cad/doc/exchange.py・meshio.py。D71）
| 形式 | 書き出し（`doc.export`） | 読み込み（`doc.open`） |
|---|---|---|
| STL | バイナリ。全部の物体を 1 つに | バイナリ・テキスト |
| 3MF | 単位 mm、部品ごとの object、色は basematerials（三角形ごと） | unit を mm に直す。build の item・components の transform を反映 |
| OBJ | 部品ごとの `o`、色ごとの `usemtl`、同じ名前の `.mtl`（Kd） | v / f（多角形は扇形に分割、負の番号も可）、o / g ごとに分ける |
- 単位は mm（STL・OBJ は単位を持たないので mm とみなす）。読み込んだ物体は 1 つのメッシュにまとめ、インポートのフィーチャーだけの
  未保存のパートにする。色は読まない
- 細かさ（`quality`）: `coarse` / `standard`（表示と同じメッシュ）/ `fine`。アセンブリは部品の姿勢（`Component.matrix()`）を掛ける

## 9. Workspace・API・UI（E1 で実装）
### 9.1 起動
`run.bat [--demo] [--kernel auto|usd_brep|prism|mock] [--port 8710] [--no-browser]`
（= `cd core && python -m cad.api …`）。localhost の HTTP サーバが UI（`app/web`）と API を提供し、ブラウザが開く。
カーネルの auto は usd_brep → prism（簡易カーネル）の順。

### 9.2 Workspace（cad/doc/workspace.py）
開いている文書（パート・アセンブリ）とカーネルを持つ。保存・読み込みは 8 章。

### 9.3 API（cad/api/rpc.py・feature_api.py、`POST /rpc`、JSON-RPC 風 `{id, method, params}` → `{id, result | error}`）
| メソッド | 内容 |
|---|---|
| `kernel.info` | 使用中・使えるカーネル |
| `doc.list / new / demo / open / save / close` | 文書の操作。`doc.list` は `modified` も返す |
| `ui.settings {show_volume?}` | 表示の設定を変えて全部を返す。show_volume = 状態に体積を載せるか（既定 false。D66） |
| `agent.settings {provider?, api_key?, model?, base_url?, effort?, web_search?, max_steps?}` | AI の設定を変えて返す（キーは末尾だけ。D67） |
| `agent.chat {text, images?, doc?}` / `agent.poll {since, wait?}` / `agent.stop` / `agent.reset` | AI に依頼（裏で動く）/ 経過のイベント（ロックの外で最大 wait 秒待つ）/ 止める / 新しい会話（D67） |
| `doc.demo {kind?}` | デモ部品を作る。kind = plate（板・ボス・穴・フィレット。既定）/ car（車。D63） |
| `doc.open {path}` | `.usda`（旧形式も可）を開く。アセンブリなら部品のパートも開く。`.stl / .3mf / .obj` は取り込んだパートにする（D71） |
| `doc.export {doc, path, quality?}` | パート・アセンブリを STL / 3MF / OBJ に書き出す（拡張子で形式。quality = coarse / standard / fine）。戻り値 `{path, files, objects, triangles}`（D71） |
| `asm.joints {doc}` | 「動かしてみる」の回転軸: 固定でない部品ごとに同心円の合致の相手の軸 `[{comp, origin, dir}]`（世界座標。文書は変えない。D70） |
| `ui.progress` | 開いている途中の進み具合 `{title, step, done, total}` か null（HTTP のロックの外。D69） |
| `appearance.set {doc, color, feature?/features?/face?/faces?}` | 外観（色 "#rrggbb"、null で解除）。対象なしはパート全体。面 > フィーチャー > パート（D69） |
| `doc.save {doc, path?}` | USD で保存。戻り値 `{path, saved（書いたファイル）, warnings（BRep が書けなかった等）}` |
| `ui.pick_path {mode: "open"\|"save", doc?, ext?}` | OS のファイルダイアログ（ext = usda / pdf / dxf / stl / 3mf / obj）。戻り値 `{path（取り消しは null）, available}`。出せない環境では `available=false` |
| `doc.state {doc, mesh=true}` | 文書の状態（下記） |
| `feature.params {doc, id}` | 編集できる寸法（スケッチは寸法拘束、他は数値パラメータ）と式・状態 |
| `feature.edit {doc, id, key, value? , expr?}` | 数値か式で変更（式 "" で解除）。式の構文はその場で検査 |
| `feature.suppress / feature.rename` | 抑制・名前 |
| `feature.set {doc, id, key, value}` | 数値以外の設定（押し出し・回転の op: add/cut/new、押し出しの reverse） |
| `feature.delete {doc, id}` | 削除（Undo で戻せる） |
| `rollback.set {doc, index}` | ロールバックバー（null で最後まで） |
| `variable.set {doc, name, expr}` | グローバル変数（expr 空で削除） |
| `edit.undo / edit.redo` | Undo / Redo |

- 編集系は 1 回 = 1 トランザクションで、結果として `doc.state` を返す（UI は状態を持たない）
- 状態: `modified`（保存後に変更あり）, `tree`（id, name, type, state, message）, `rollback`, `variables`, `undo`/`redo`（次に戻す操作名）,
  `volume`, `mesh`
- `mesh`: `positions`(float32), `indices`(uint32), `face_of_triangle`(uint32), `face_names`（永続名）,
  `edges`(float32 の線分), `edge_of_segment`, `edge_names`, `edge_faces`（隣接 2 面の番号）, `bbox`。配列は base64（リトルエンディアン）
- 失敗は `error.message`（日本語）。フィーチャーの失敗は例外ではなくツリーの状態（error）として返る

### 9.3.1 スケッチ編集と作成の API（cad/api/sketch_api.py、E2）
| メソッド | 内容 |
|---|---|
| `sketch.new {doc, plane}` | 新しいスケッチ（`{"plane": "Front"\|"Top"\|"Right"}` か `{"face": 面の永続名}`）を作って編集に入る |
| `sketch.begin {doc, id}` / `sketch.end {doc}` | 既存のスケッチの編集開始 / 終了 |
| `sketch.add {doc, kind, points, radius?, relations?}` | 点・線・矩形・円・円弧。点は `[u, v]` か既存の点 ID（共有＝一致）か `"ORIGIN"`（原点の固定点）。線はほぼ水平・垂直（誤差 1e-5×長さ以内）なら拘束を自動で付ける。円弧は描いた向きに関係なく反時計回りに直す。relations（線）: 作図中の推測 [[perpendicular|parallel|tangent, 相手], ...]（D61） |
| `sketch.constrain {doc, type, refs}` | 参照の順は自由。一致は相手に応じて 点-線上 / 点-円上 / 同心 に、固定は線なら両端に、水平・垂直は複数の線にまとめて付ける |
| `sketch.dimension {doc, refs, value?, type?, label?}` | スマート寸法（線→長さ、円→直径、円弧→半径、点と点→距離、点と線→距離、円と線→中心と線の距離、線と線→角度、円の中心どうし→距離）。`type`: 線・2 点（円は中心）に `hdistance` / `vdistance`、円に `radius`、平行な 2 線に `distance`。`label`: 文字の位置 |
| `sketch.add` の points | 各点は `[x, y]` / 既存の点 ID / `"ORIGIN"` / スナップ `{"mid": 線}`（中点拘束）・`{"on": 線か円, x, y}`（点-線上 / 点-円上）。点として使わない位置（円の半径、矩形の角）のスナップは座標だけ使う。kind に `crect`（中心, 角） |
| `sketch.mirror {doc, ids, axis?}` | ミラー。axis を省くと ids の中の作図線（1 本）が軸 |
| `sketch.pattern {doc, ids, kind, count, spacing?, angle?, total?, center?}` | 直線（linear: 間隔・向き°）/ 円形（circular: 角度°・中心 = 点 ID か [x, y]、既定は原点）のスケッチパターン（D61） |
| `sketch.add` の追加 | kind に `polyline`（点の列。`closed` 既定 true。点を共有する線。D67）/ `arc3`（始点, 終点, 通る点）/ `tarc`（端点の ID, 終点）/ `slot`（中心 1, 中心 2, 幅の点）/ `polygon`（中心, 頂点。`sides`）。`construction=true` で作図線 |
| `sketch.fillet {doc, radius, l1, l2 \| point}` / `sketch.chamfer {doc, distance, distance2?, l1, l2 \| point}` | 角のフィレット / 面取り。線 2 本か、角の点で指定 |
| `sketch.offset {doc, ids, distance, x?, y?, reverse?}` | オフセット。(x, y) の側へ |
| `sketch.extend {doc, id, x, y}` | 延長（(x, y) に近い端） |
| `feature.add` | type に `plane`（base, offset）/ `lpattern`（seeds, direction, spacing, count）/ `cpattern`（seeds, axis, count, angle）/ `mirror`（seeds, plane）、押し出しに `end` |
| `feature.set {doc, id, key, value}` | 数値以外の設定（選択肢・bool・参照・seeds）。`feature.params` の `option_meta`（key, label, kind, value, choices）に従って UI が描く |
| `feature.move {doc, id, index}` | ツリーの中で移動 |
| `feature.add` の追加（面取り・シェル） | `chamfer`（edges, distance）/ `shell`（faces, thickness） |
| `view.rollback {doc, id?}` | 表示だけをフィーチャー id の直前まで巻き戻す（省略で解除）。参照を選び直すときに使う（D44）。保存・Undo の対象外。`feature.set` で自動的に解除 |
| `doc.state` の追加 | `planes`（標準平面と参照平面: ref, name, origin, x, y, normal）、ツリーの各項目に `op`（押し出し）・`seeds`（パターン） |
| `sketch.dim_label {doc, id, label}` | 寸法の文字の位置を変える（null で既定の位置） |
| `sketch.drag {doc, x, y, point? \| entity?, grab?, final}` | ドラッグ。点はその位置へ、線は掴んだ位置 `grab` からの移動量だけ平行移動、円・円弧は (x, y) を通る大きさに。途中は形だけ返し、final=true で 1 操作として Undo に記録 |
| `sketch.convert {doc, edges}` | エンティティ変換（3.2）。変換できないエッジは `warnings` に理由 |
| `sketch.delete / sketch.construction / sketch.trim / sketch.fillet` | 削除、作図線の切り替え、トリム、スケッチフィレット |
| `feature.add {doc, type, …}` | 押し出し（op: add / cut / new）、回転（作図線 1 本を軸に）、フィレット（エッジの永続名） |

- 編集中は表示用の一時ロールバック（`view_rollback`、保存・Undo の対象外）でスケッチの直後までに戻す
- `doc.state` の `sketch` に、平面の座標系・要素・拘束・求解結果（状態、自由度、冗長・矛盾、要素ごとの定義状態）・閉じた輪郭の数を載せる

### 9.3.2 アセンブリの API（cad/api/asm_api.py、E4）
| メソッド | 内容 |
|---|---|
| `asm.new {name?}` | 新しいアセンブリ |
| `asm.add {doc, part, position?}` | 開いているパート（文書 ID）を部品として置く。位置を省くと既存の部品の右隣。最初の部品は固定 |
| `asm.mate {doc, type, a, b, value?, aligned?}` | 合致。a, b は `[部品 ID, 面の永続名]`。ロックは面の名前を問わない |
| `asm.edit {doc, id, value?, flip?, suppressed?}` | 合致の値・向きの反転・抑制 |
| `asm.interference {doc}` / `asm.copy {doc, comp, offset?}` / `asm.pattern {doc, comp, kind, ref? \| axis?, count, spacing, angle, flip}` | 干渉チェック（近似）・部品の複製・部品のパターン（種にロック。D56） |
| `asm.mate_options {doc, a, b}` | 付けられる合致の種類と既定の種類、今の距離・角度（D51） |
| `asm.mate {…, pending: true}` / `asm.mate_set {doc, id, type, a, b, value?, aligned?}` / `asm.mate_cancel {doc, id}` | 合致の PropertyManager: 付けて見せる / 種類・値・整列を変える（Undo は作成とまとめて 1 つ）/ 取り消す（部品も元の位置へ） |
| `asm.fix {doc, comp, value}` / `asm.delete {doc, ids}` | 固定の切り替え / 部品・合致の削除（部品を消すと関係する合致も消える） |
| `asm.drag {doc, comp, position, final}` | 部品のドラッグ。途中は各部品の変換行列だけ返し、final で 1 操作として記録 |

- `doc.state`（アセンブリ）: `kind: "assembly"`, `components`（id, 元のパート, 固定, 位置, 変換行列（行優先 4×4）, 残りの自由度）,
  `mates`（種類, 参照, 値, 状態: ok / conflict / redundant / flipped / suppressed）, `solve`（状態・自由度）, `parts`（使っているパートのメッシュ）,
  `part_docs`（部品にできるパート）。状態を返すたびに解き直すので、パートを編集すると部品が追従する
- 保存: `.usda`（8 章）。パートを reference し、未保存・変更ありのパートも一緒に保存する
- `doc.list` は `kind`（part / assembly）を返す

- ウィンドウ（D50）: RPC の params の `_client` はウィンドウの ID（サーバが取り除く）。`ws.rev {since, me}` → `{rev, others}`
  （since より後に自分以外が文書を変えたか）。`ui.open_window {doc}` → `{opened}`（殻が開いたか。false ならブラウザが開く）。
  `kernel.info` の `shell_windows` で殻があるかを知る

### 9.3.2 図面の API（cad/api/drawing_api.py、D60）
| メソッド | 内容 |
|---|---|
| `drw.new {model, size?, landscape?, scale?, standard?}` | パートかアセンブリ model の図面を作る（既定: A3 横、縮尺は自動、標準 3 面図）。アセンブリは部品を姿勢どおりに置いて投影し、エッジの名前は `<部品 ID>/<パートでの名前>`（D72） |
| `drw.sheet {doc, size?, landscape?, scale?, title?}` | 用紙・向き・縮尺（"1:2" か [1, 2]）・名称 |
| `drw.view_add {doc, orient \| parent + side, x?, y?}` / `{doc, standard: true}` | モデルビュー / 投影図（side: right・left・up・down）/ 標準 3 面図。(x, y) = 図の中心 |
| `drw.view_set {doc, id, x?, y?, scale?, hidden?}` | 位置・ビューの縮尺（null = シートの縮尺）・隠線の表示 |
| `drw.dim_add {doc, view, a, b?, pos, dir?}` / `drw.dim_set {doc, id, pos}` | 寸法（a・b = `{src, hint}`。b なしで a が円なら直径・半径）/ 文字の位置 |
| `drw.note_add {doc, view, text, pos?, h?}` / `drw.note_set {doc, id, text?, h?}` | 注記（自由な文字。室名など。D73）。pos = 文字の中心（ビューの位置からの用紙の mm）、h = 文字の高さ（mm、既定 3.5）。寸法と同じ一覧（`dims`、kind = "note"）に入り、移動は `drw.dim_set`、削除は `drw.delete` |
| `drw.delete {doc, ids}` / `drw.export {doc, format: pdf \| dxf, path, scale: sheet \| model}` | 削除 / 書き出し |

`doc.state`（kind = "drawing"）: sheet（size・scale・width・height・items）、views（id・label・x・y・bbox・items・snaps・edges）、
dims（id・view・text・box・items）。items は描く要素（render.py）で、ビュー・寸法のものはビューの位置からの相対座標。

### 9.4 UI（app/web、外部ライブラリなし）
モジュール（D59）: core.js（土台）← evaluate.js・sketch_ui.js・props.js・windows.js ← asm_ui.js ← app.js。
import は下の層へだけ。上の層の関数は core.js の `ui` 表を通して呼ぶ（render・updateButtons・showPick など。app.js が登録）。
操作は一般的な 3D CAD に合わせる（D42・D46）。
- ツールバーは `overflow` を付けない（付けるとドロップダウンのメニューが切れる。WebView2 で起きた）
- ツールバー（D53。`toolbar.js` が組み立てる）: 標準ツールバー（新規 ▾・開く・保存・元に戻す・やり直し・文書・別ウィンドウ。アイコンだけ）、
  CommandManager（タブ: フィーチャー = 押し出し・回転・カット・フィレット / 面取り・シェル・直線パターン / 円形パターン・ミラー・参照平面、
  スケッチ = スケッチ（中は終了）・スマート寸法・作図ツール・スケッチのフィレット / 面取り・エンティティ変換・オフセット・トリム・延長・ミラー・直線 / 円形スケッチパターン（D61）、
  アセンブリ = 部品の挿入・合致）。スケッチを始める / 終えるとタブが切り替わる。表示はヘッドアップ ツールバー
  （3D ビューの上の中央: 選択フィルタ ▾・全体表示・正対・断面表示（D62）・表示方向 ▾・表示スタイル ▾・透視）。評価のタブ = 計測・質量特性（D55、select.js）
- 左: フィーチャーツリー。原点、基準面（正面 / 平面 / 右側面）、フィーチャー（状態の色・アイコン、エラー文）、ロールバックバー、グローバル変数
  - 平面をクリックで選ぶと 3D に半透明の四角で出る。選んで「スケッチ」でその平面に描く（ダブルクリックでも）
  - 右クリックメニュー: スケッチを編集 / フィーチャーを編集 / 名前を変更（F2）/ 抑制 / ここまでロールバック / 上へ・下へ移動 / 削除（Del）
- 中央: 3D ビュー（自作 WebGL2）
  - 投影: 平行投影が既定（透視に切り替え可）。左下に座標軸（トライアド）
  - マウス: 中ドラッグ = 回転、Ctrl+中ドラッグ・右ドラッグ・Shift+左ドラッグ = パン、Shift+中ドラッグ = ズーム、
    ホイール = カーソルの位置を中心にズーム、何もない所の左ドラッグ = 回転、右クリック = メニュー
  - 表示方向: Ctrl+1〜7（正面・背面・左・右・平面・底面・等角）、Ctrl+8 = 正対（選んだ面・平面、スケッチ中はスケッチ平面）。
    矢印キー = 15° 回転（Shift で 90°）、Z / Shift+Z = 縮小 / 拡大、F = 全体表示。視点はなめらかに動く
  - 表示スタイル: エッジ付きシェーディング / シェーディング / 隠線なし / ワイヤーフレーム
  - 面・エッジにカーソルを置くと強調し、ステータスバーに名前と大きさ（面積・長さ。選択中のエッジは合計）を出す。
    面をクリックすると、その面を作ったフィーチャーをツリーで選ぶ。エッジは近く（6px 以内）をクリック、Ctrl で複数
  - 右クリック（面の上）: この面にスケッチ / 正対 / 参照平面を作成 / フィーチャーを編集 / フィレット（エッジ選択中）/ 表示方向
- 右: プロパティ（一般的な 3D CAD の PropertyManager に当たる）
  - 寸法の数値と式、数値以外の設定（押し出し状態、操作、反転、基準の平面、方向、回転軸、ミラーの平面、シェルの取り除く面）、抑制、ロールバック、削除
  - シェルの取り除く面: 一覧（× で外す）+「面を選ぶ」で 3D の面をクリックするたびに足す / 外す（Esc で終了）
  - 作ったばかりのフィーチャーには ✓ OK（Enter）/ ✗ キャンセル（Esc = 作ったフィーチャーを削除）を出す。
    ほかのものを選んだり 3D をクリックしたりすると確定したことにする
  - 平面・方向・軸は、一覧から選ぶか「面を選ぶ / エッジを選ぶ」で 3D をクリックして指定する。
    選ぶ間は、そのフィーチャーの直前の形を表示する（`view.rollback`。後のフィーチャーで面の名前が変わるため）
- スケッチの開始: 面か平面を選んで「スケッチ」→ そこにすぐ描ける。何も選んでいなければ平面を選ぶメニュー
- パターン・ミラー: 元にする押し出し・カット・回転をツリーで選んでボタンを押す。エッジ（1 本）を選んであれば向き・軸に、
  平面を選んであればミラーの平面に使う。省いたときの既定は、元のスケッチ平面の x 方向 / 法線に近い世界の軸、ミラーは右側面
- キー: Ctrl+Z / Ctrl+Y / Ctrl+S（保存）/ Ctrl+Shift+S / Ctrl+O（開く）/ Ctrl+B（再構築）/ F2 / Del / Enter / Esc
- 保存・開く: OS のファイルダイアログ（`ui.pick_path`）。出せない環境では画面内のパスの入力欄。保存先が未定か旧形式なら聞く。
  題名に変更ありの印（*）、未保存なら「（未保存）」。BRep が書けなかったなどの警告は赤い通知で出す
- 書き出し（D71）: 保存の横の ▾ のメニュー（名前を付けて保存・STL / 3MF / OBJ で書き出し・細かさ）。細かさは選んだものを覚える
  （localStorage）。開くのダイアログはメッシュのファイルも選べる
- 押し出し・カット・回転の対象: 編集中のスケッチ → ツリーで選択中のスケッチ → ツリー末尾の未使用スケッチ（編集中のまま押せる）
- スケッチ編集（app/web/sketch.js）
  - 平面に正対し、2D オーバーレイに描く。色は定義状態（青=未定義、黒=完全定義、赤=過剰・矛盾）、作図線は破線
  - ツール: 選択(S) / 線(L、連続。Esc・ダブルクリック・既存の点で終了) / 矩形(R) / 円(C、中心→半径) /
    円弧(A、中心→始点→終点) / 点(P) / 寸法(D、1 つ目→2 つ目か何もない所) / トリム(T)
  - スナップ: 既存の点、原点、水平・垂直（3° 以内）、線の中点、線上、円上（確定すると 中点 / 一致 の拘束が付く。カーソルに種類を表示）
  - 中心矩形（中心→角）。ミラー: 軸の作図線を含めて選び、右パネルの「ミラー」
  - 範囲選択: 何もない所からドラッグ。左→右は枠に完全に入ったもの、右→左（破線の緑の枠）はかかったものも。Ctrl で追加
  - 選択: クリック、Ctrl+クリックで追加。ドラッグで動かす（点はその位置へ、線は平行移動、円・円弧は大きさ。
    ドラッグ中は求解だけ、離したら記録）。拘束で動けない方向には動かない
  - スマート寸法（一般的な 3D CAD と同じ流れ）: 要素をクリック（2 つまで）→ 寸法がカーソルについてくる → 何もない所をクリックして置く
    → 値の入力欄。線・2 点は置く位置で変わる（両端の x の範囲内で上下 → 水平、y の範囲内で左右 → 垂直、それ以外 → 平行）。
    円は直径（⌀）、円弧は半径（R）。平行な 2 線は間隔、交わる 2 線は角度
  - 寸法の表示: 補助線・寸法線・矢印・文字。文字はドラッグで移動、ダブルクリックで値を編集（`=` で始めると式）
  - ツール（下段のツールバー）: 選択、線、中心線、矩形、中心矩形、円、円弧、3 点円弧、正接円弧、スロット、多角形、点、寸法、
    トリム、延長、フィレット、面取り、オフセット、エンティティ変換。使い方はステータスバーに出る
  - 原点（赤い点）も選択・寸法・拘束の相手にできる。スケッチに原点の固定点がまだ無いときは、UI は仮の "ORIGIN" を送り、
    サーバが原点の固定点を作って使う（`_resolve_origin`）。範囲選択では原点を選ばない
  - 右パネルの選択欄: 何も選んでいないときは拘束・寸法の付け方。選ぶと、その組み合わせに付けられる拘束のボタンだけ
    （`applicableConstraints`）と、寸法を付けられるなら「寸法を付ける (D)」。先に選んでから D でも寸法になる（一般的な 3D CAD と同じ）
  - フィレット・面取り・オフセット・多角形は、ツールを選んだときに値（半径・距離・辺の数）を聞く。
    フィレット・面取りは角の点（か線 2 本）をクリック、オフセットは要素をクリックして選び、オフセットする側をクリック
  - 右上に「スケッチ終了」と拘束記号の切り替え。右クリックでメニュー（選択 / 寸法 / 線 / 作図線 / 削除 / 正対 / スケッチ終了）
  - トリム・延長・オフセットのクリックは、点や拘束記号ではなく線・円を拾う
  - エンティティ変換: 選択ツールでモデルのエッジ（クリック）や面（その面のエッジ全部）を選び、ツールバーの「エンティティ変換」。
    作った要素は黒（完全定義）、拘束一覧に「エッジ上」
  - 拘束記号（H, V, ∥, ⊡ など）の表示切り替え
  - 右パネル: 定義状態と自由度、拘束ボタン、作図線・削除、拘束と寸法の一覧（矛盾・冗長を赤で表示、× で削除）
- 文書: ツールバーの選択欄で開いている文書（パート / アセンブリ）を切り替え。新規パート・新規アセンブリ
- アセンブリ（app/web/asm.js）
  - 部品のメッシュに変換をかけて 1 つにまとめて表示（面番号 → [部品, 面の永続名]）
  - 左: 部品（固定 / 自由度、固定の切り替え、削除）、部品を追加、合致の一覧（状態、値の変更、向きの反転、抑制、削除）
  - 面をクリックで選択（2 つまで。別々の部品）→ 右パネルの合致ボタン。距離・角度は値の欄から
  - 部品の面を左ドラッグで部品を動かす（視線に垂直な平面内で引っ張り、合致で許される方向にだけ動く）。固定部品は動かない
  - 何もない所の左ドラッグで回転
- 確認: `scripts/ui_smoke.py`（ヘッドレス Chrome で操作して画面を撮る。開発用）

### 9.4 デスクトップ用の殻（app/src-tauri、T）
Tauri 2。Rust は `src/main.rs` の 1 ファイルで、UI と Python の間には入らない（IPC・プラグイン・capability を使わない。D41）。
- 起動: 空いているポートを取り、`python -m cad.api --no-browser --exit-with-stdin --port <p> --kernel <k>` を
  `core` を作業フォルダにして起動（コンソールの窓は出さない。出力は `%TEMP%\agentcad-server.log`）。
  `agentcad.exe` に付けた引数（`--demo` など）はサーバに渡す
- ウィンドウ: まず起動画面（`dist/index.html`、実行ファイルに埋め込み）。サーバのポートに接続できたら UI
  （`http://127.0.0.1:<p>/`）に置き換える。起動できない・応答前に終了・90 秒応答なしのときは起動画面に理由とログの場所を出す
- 終了: ウィンドウを閉じるとサーバを止める。殻が異常終了しても、サーバは標準入力が閉じたことで終了する（`--exit-with-stdin`）
- 場所の解決: リポジトリのルートは 環境変数 `AGENTCAD_ROOT` → 実行ファイルから上へ → 作業フォルダから上へ（`core/cad/api/server.py` がある所）。
  Python は 環境変数 `AGENTCAD_PYTHON` → `<ルート>/agentcad.config.json` の `python` → `python`。カーネルは同じファイルの `kernel`（既定 auto）
- 開発・ビルド: `app/package.json` の tauri-cli（`npm run tauri dev` / `npm run tauri build`）。`build.devUrl` は無く、
  起動画面は `frontendDist`（`./dist`）。アイコン `icons/icon.ico`（BMP 形式の ico。Windows のリソースに必要）。インストーラは作らない（`bundle.active: false`）
- ファイルダイアログ（cad/api/dialogs.py）: サーバが別プロセスの Python で tkinter のダイアログを出す（D40）。ブラウザでも同じに動く
- 文書ごとのウィンドウ（D50）: サーバを `--shell-windows` で起動し、サーバの標準出力の `@@agentcad:window <ID> <名前>` を読んで
  `http://127.0.0.1:<p>/?doc=<ID>` のウィンドウ（ラベル `doc-<ID>`）を作る。あれば前に出す。標準出力のほかの行はログへ
- まだ無いもの: 未保存の変更があるときの終了確認
