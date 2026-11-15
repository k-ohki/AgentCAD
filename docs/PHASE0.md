# フェーズ0: カーネル調査の結果

## 1 回目（2026-11-11 受領、`phase0_report.txt`）

### 環境
- Windows（`os` モジュールの内容から判断。Tauri・パス・コマンド例は Windows 前提にする）
- usd-brep のバージョン表示は `?`（`__version__` が無い。0b で `importlib.metadata` から取る）
- 中身は SMLib 系のカーネル（`io` の説明に "Native SMLib file I/O"）。公開モジュールは `_core` / `_omni_solid` と、
  用途別のサブモジュール（primitives, curves, sweeps, booleans, fillets, queries, usd ほか）

### 要件ごとの判定

| 要件 | 判定 | 根拠（API） |
|---|---|---|
| R1 押し出し・回転 | ○（引数は要確認） | `linear_sweep(curves, direction, distance, …)`, `rotational_sweep(curves, base_point, axis, …)`, `create_line_segment`, `create_arc`, `create_circle`, `create_planar_faces`, `non_manifold_linear_sweep` |
| R2 ブーリアン | ○ | `boolean_union / boolean_difference / boolean_intersection` |
| R3 フィレット | ○（引数は要確認） | `fillet_edges(brep, edges, *, r…)`, `fillet_edges_per_edge`, `variable_radius_fillet` |
| R4 面・エッジ | △ | `Brep.faces / edges / vertices`、`find_face(brep, point)`, `find_edge(brep, point)`。エッジの隣接 2 面を返す API は見当たらない |
| **R5 履歴** | **×** | 生成元・変形元を返す API が無い |
| R6 メッシュ化 | △ | `tessellate(brep, *, chord_height_tolerance, …)`。三角形 → 面 の対応があるかは不明 |
| R7 平面・円筒軸 | △ | `evaluate_surface_normal(surface, u, v)` はあるが、面から曲面の種類を取る方法は不明 |
| R8 速度 | 未測定 | 試行がブーリアンの前で止まった（下記） |

その他、後で使えそうなもの: `shell_brep`（シェル）, `offset_brep`, `draft_sweep` / `taper_extrude`（抜き勾配）,
`pipe_sweep` / `curve_sweep`（スイープ）, `create_skin_*`（ロフト）, `remove_fillet`, `fillet_preview`,
`intersect_brep_with_plane`（断面）, `brep_distance` / `brep_relationship`（干渉チェック）, `usd.export_brep / import_breps`。

### 試行が途中で止まった理由
スクリプトが `ub.booleans.subtract` を呼んでいたが、この版には無い（PyPI の説明の例にはあるが、実際は
`boolean_difference`）。例外は標準エラーに出ていたのでレポートに残らなかった。
0b のスクリプトは自分でレポートを書き、例外も記録する。

### 結論
- **usd-brep を続ける**。形を作る機能は十分ある。
- **履歴（R5）が無い問題は、こちらで補う**（D19）: カーネルには「面ごとのメッシュ」だけを求め、
  エッジ・各面の由来・平面を形状から逆算する層 `cad/kernel/meshtopo.py`（`MeshTopoKernel`）を作った。
  合成メッシュと偽カーネルでテスト済み。OCCT に切り替える場合も同じ層を使える。
- 残る不明点は「面ごとのメッシュを取れるか（R6）」と各関数の引数。0b で確かめる。
  面ごとのメッシュがどうしても取れない場合は、三角形の重心で `find_face` して振り分ける（遅いが動く）。
- OCCT へ切り替える条件（D20）: 0b の後、適合テストの R1〜R3・R6 のどれかが usd-brep の機能不足で通せないとき。

## 2 回目（0b、2026-11-11 受領、`phase0b_report.txt`）

### 環境
Windows 11 / Python 3.12.13 / usd-brep 1.0.1 / numpy 2.5.2 / scipy 1.18.1

### 分かったこと
| 項目 | 結果 |
|---|---|
| `linear_sweep(curves, direction, distance, *, cap_ends=True)` | 蓋付きの立体。**外形と穴の曲線をまとめて渡すと穴あきになる**（体積 187.434 = 200 − 4π） |
| `create_arc(center, r, start_deg, end_deg)` | XY 平面、反時計回り。**角度は 360 を超えられない**（SM_ERR_INVALID_INPUT） |
| `create_circle` | XY 平面の円 |
| `rotational_sweep(curves, base_point, axis, angle_deg, *, cap_ends=True)` | **右ねじの向き**（+Y 軸まわり 90° で +X → −Z）。体積は理論値どおり |
| ブーリアン | `boolean_difference(box, cyl)` の体積は理論値どおり。**入力を消費する** |
| `fillet_edges(brep, edges, *, radius, …)` | **入力を書き換える**。半径はトレランスの 8 倍（8e-5）以上 |
| `rotate` / `translate` | **入力を書き換える**（同じハンドルを返す）。右ねじ |
| `find_face(brep, point)` | `faces()` と同じオブジェクトを返す（`is` / `==` / `hash` で照合できる） |
| `find_edge(brep, point)` | `(エッジ番号, パラメータ)`。距離の上限は無いので自分で確かめる |
| `Edge.faces()`, `Edge.point_at(t)`, `Edge.parameter_range()` | 隣接面と、エッジ上の点が取れる → **R4 はカーネルから正確に取れる** |
| `Face.surface().is_planar()`, `Face.interior_point()`, `Face.outward_normal_at_uv()` | 平面判定と内部の点が取れる |
| `tessellate(brep, *, chord_height_tolerance, …)` | `PolyBrep` を返す。`to_numpy_arrays()` / `get_faces()` がある。**三角形 → 面 の対応の有無はまだ不明** |
| `tessellate_boundaries(brep)` | (点の並び, エッジごとの点数, Edge) → エッジの折れ線 |
| `brep.copy()` | 複製できる |

### 試行が途中で止まった理由
E7 で `boolean_difference(box, cyl)` の後、消費済みの `box` と `cyl` を次の `boolean_union` に渡して
プロセスごと落ちた（例外が記録されずにレポートが途切れている）。0b のスクリプトはこの性質を知らずに書いていた。

### アダプタへの反映
- 演算の前に必ず `copy()` する（フィーチャーツリーは前回の結果をキャッシュして再利用するため、消費されると壊れる）
- 押し出しは外形と穴をまとめて `linear_sweep` に渡す
- 円弧は 0° から作って回転・移動する（360° 制限を避ける）
- フィレット対象は複製した形状の上で `find_edge` → `edges()[番号]`。点がエッジ上にあるか確かめる
- エッジと隣接面は `Edge.faces()` と `tessellate_boundaries` から取る（MeshTopoKernel に `_raw_edges` フックを追加）
- 三角形 → 面 の対応が無い場合は、三角形の重心で `find_face` して振り分ける

## 3 回目（0c、2026-11-11 受領、`phase0c_report.txt`）

### 分かったこと
| 項目 | 結果 |
|---|---|
| `PolyBrep.to_numpy_arrays()` | キーは `points`(float32) / `normals` / `faces` / `face_normals`。`faces` は `[3, i, j, k, 3, …]`（頂点数つき）。三角形の向きは外向き |
| 三角形 → 面 の対応 | **無い**（`get_faces()` は三角形ごとの PolyFace で、法線しか持たない）→ 三角形の重心で `find_face` して振り分ける |
| `create_arc` | 負の角度（−60→60）も可。0→120 を 300° 回して移動すると期待どおりの位置 |
| ブーリアン（copy 後） | 差・和・積とも体積は理論値どおり。元の形状は無事。球で 20 回連続の差: 105 ms |
| フィレット（copy 後） | 体積 199.4635（理論値どおり）、面 7 |
| `Edge.faces()` | `faces()` と同じオブジェクト。`tessellate_boundaries` の順は `edges()` と同じ |
| 円柱 | エッジ 3 本（継ぎ目は隣接面 1）。側面は平面でない（`is_planar` False） |
| 適合テスト | usd_brep は 8 件すべて失敗。原因は 1 つ: `faces` 配列の形式をアダプタが解釈できなかった |

### アダプタへの反映
- `faces` 配列を読む処理を追加（`meshtopo.triangles_from_prefixed`、テスト済み）
- 机上の点検で見つけた弱点も修正: フィレット面の点のうち接線に近いものは、弦誤差程度の許容値だと
  隣の平面の上と判定され、フィレット面が「平面の変形」と誤判定されうる。
  → 平面の入力面には厳しい許容値（モデル寸法 × 1e-6）を使い、多数決では「どこにも乗らない」も 1 票に数える
- 適合テストの出力の文字化け（子プロセスが cp932 で出力）を修正

## 4 回目（0c 再実行、2026-11-11 受領）
- **適合テスト: usd_brep 8 件すべて通過**（13 passed, 3 skipped。プロセスの異常終了なし）
- 速度（10×5×4 の箱）: 押し出し 3 ms、円柱での穴あけ 19 ms、フィレット 17 ms
- 履歴: 押し出し 6 面すべて正しい由来、穴あけ後の 7 面すべて入力面に対応、フィレット面 1

### 結論
**フェーズ0 完了。カーネルは usd-brep で確定**（D20 の切り替え条件に当たらない）。
次は実カーネルでのフィーチャーツリー・合致の通しテスト（`tests/test_part_real_kernel.py`）。

## 5 回目（ファイルの入出力、2026-11-14、`scripts/x_probe_io.py`。usd-brep 1.0.1・Linux で実行）
- 入出力の関数は `io`（独自形式 `.smb` / `.smp` の読み込み）と `usd`（BRep・メッシュの USD 入出力、`tessellate_file`）だけ。
  STEP・IGES・STL・OBJ などの関数は無い
- `turn_to_nurbs` で曲面を NURBS にできるが、`Surface` / `Curve`（`BSplineCurve`）から取り出せるのは評価（点・微分・法線）と
  範囲だけで、制御点・ノット・重み・次数は取れない → STEP（AP214 の `B_SPLINE_SURFACE_WITH_KNOTS` など）を自前で書けない
- `tessellate` は `PolyBrep`（三角形メッシュ）を返し、`usd.export_mesh` → `usd.import_mesh` で往復できる
- 結論: 外部の形式はメッシュ（STL・3MF・OBJ）を自前で書く。STEP は usd-brep では扱えない（D71）

