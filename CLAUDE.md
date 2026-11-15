# AgentCAD — 引き継ぎメモ（新しいセッションは最初にこれを読む）

フィーチャーベースのパラメトリック 3D CAD を自作している。利用者は 1 人（このリポジトリの持ち主）、ローカル実行のみ。
**主目的は機械系 3D CAD の中核機能（スケッチ拘束ソルバ、フィーチャーツリー、アセンブリ合致）を自前で実装すること**。
形状演算（BRep）は外部カーネル usd-brep（NVIDIA、Python 3.12）に任せる。
**操作感は一般的な機械系 3D CAD の流れ・既定値に合わせる**（利用者の希望。具体的な手本は `CLAUDE.local.md`（公開しない）にある）。

最終更新: 2026-11-14（D1〜D71 まで実装・確認済み。次の段階の候補は `docs/PLAN.md` 6 章）

## 作業環境（利用者の Windows PC の Claude Code）
- リポジトリの場所で直接作業し、git もそこで管理する（ブランチ main。大きな作業はブランチを切って main に取り込む）
- Python 3.12 の venv（numpy・scipy・usd-brep・pytest・playwright、AI には anthropic）。以下のコマンドの `python` はその venv のもの。
  場所は PC ごとに違うので、最初に利用者に聞くか探す。usd-brep が入っていれば実カーネルのテスト
  （`test_part_real_kernel.py`・`test_kernel_conformance.py`）も自動で走る
- Tauri: `cd app` → `npm install` → `npm run tauri dev`（Python の場所は `agentcad.config.json`）。Rust の変更はその場でビルドして確かめる
- 公開しない手元のもの（.gitignore 済み）: `Data/`（利用者の作業データ。写真から作った装置のモデルなど）、`memo.txt`、
  `*.bundle`、`*.lnk`、`agentcad.config.json`、`app/package-lock.json`
- Git Bash の注意: ヒアドキュメント（`<<'EOF'`）の中でも `\\` が `\` に縮むことがある。バックスラッシュを含む Python は
  Write ツールでファイルに書いてから実行する（Windows のパス・正規表現・JSON で事故が起きた）

## 引き継ぎ（2026-11-14 時点）
- 状態: D1〜D71 実装済み。`cd core && python -m pytest -q` で 240 passed, 3 skipped（usd-brep あり）、`scripts/ui_smoke.py` も OK
- 利用者の確認待ち: DXF の日本語（D60）、実際の AI の API（D67）、デスクトップ版での「動かしてみる」の体感と「その他の色…」（D69・D70）
- ファイルの読み込み・書き出し（STL・3MF・OBJ。D71）を実装。**利用者の確認待ち**: 実際のスライサ・Blender で開けるか、
  手元の STL を開いたときの面の分かれ方。STEP は usd-brep では不可（PLAN.md 6.0）
- 次の候補: `docs/PLAN.md` 6 章。ほかに今回の作業で出たもの: 「動かしてみる」の直線の動き（引き出し）、断面の切り口を部品・
  フィーチャーの色で塗る（今はパートの色一色）

## まず読む資料
| ファイル | 内容 |
|---|---|
| `docs/PLAN.md` | 進め方の原則、フェーズと状態、**次の作業（6 章）**、バックログ、変更履歴（8 章） |
| `docs/SPEC.md` | 仕様（ソルバ、スケッチ、フィーチャー、命名、カーネル、アセンブリ、API（9 章）、UI） |
| `docs/DECISIONS.md` | 設計判断 D1〜D71 と理由、未解決の課題 |
| `docs/PHASE0.md` | usd-brep の調査結果（API の性質・落とし穴・ファイルの入出力） |

## 現在の状態（2026-11-14 の区切り）
- 揃っているもの: 拘束ソルバ、スケッチ（作図ツール一式・スマート寸法・作図中の推測とスナップ・パターン・ミラー・オフセット・トリム・
  エンティティ変換・外部参照）、フィーチャー（押し出し / カット・回転・スイープ・穴ウィザード・フィレット・面取り・シェル・
  参照平面・直線 / 円形パターン・ミラー）、式と変数、アセンブリ（合致・干渉チェック・部品のパターン）、評価（計測・質量特性）、
  2 次元図面（PDF・DXF）、USD（.usda）保存、STL・3MF・OBJ の書き出しと読み込み（参照用のボディ。D71）、Tauri のデスクトップ版（文書ごとのウィンドウ）、断面表示（ハンドル付き）、
  デモ部品（板・車）
- すべて実カーネル（usd-brep）で確認済み。利用者の確認待ちは **DXF の日本語（Shift-JIS にした版。D60）** だけ
- 性能（この PC、usd-brep）: 車のデモの作成 1.43 秒・全体の再生成 1.28 秒。残りの約 8 割はカーネルの中（D64・D65）
- 外観（色）: パート・フィーチャー・面。右クリックの「外観」、API `appearance.set`（D69）
- 体積など普段使わない計算は既定でオフ（ステータスバーの「体積」で切り替え。D66）
- AI エージェント（D67）: 標準ツールバーの「AI」。Claude（既定 claude-opus-5-5、公式 SDK `anthropic` を venv に入れた）と
  OpenAI 互換。テストは偽のモデル・偽の API サーバで確かめ、**実際の API では利用者のキーで確認待ち**。
  AI の道具やシステムプロンプトを変えたら `tests/test_agent.py` と ui_smoke の scenario_agent を流す

## フィーチャーを足すとき（D59）
1. `core/cad/part/features/` に Feature を継承したクラスを書いて `@register`（label・default_name・options、必要なら
   hidden_params / option_changed / param_edited）。`features/__init__.py` と `part/__init__.py` に足す
2. `core/cad/api/feature_api.py` に `_make_<type>`（作るときの既定値と検査）。新しい設定・寸法の名前は OPTION_LABELS / PARAM_LABELS
3. カーネルの新しい操作が要るなら `kernel/base.py`（既定はエラー）→ `meshtopo.py`（履歴の逆算）→ `usd_brep.py`・`mock.py`。
   usd-brep の関数は推測で書かず、`scripts/` に調査スクリプトを書いて実際の振る舞いを確かめてから（例: `w_probe_sweep.py`）
4. UI: `app/web/toolbar.js`（ボタンとアイコン）、`app.js`（押したときの `addFeature`）。右のパネルは options から自動で出る
5. テスト: `tests/test_feature_registry.py` が足し忘れを見つける。形は `test_part_real_kernel.py`

## 構成（どこに何があるか）
```
core/cad/solver    拘束ソルバ（バッチ自動微分 ad.py + 疎行列 LM、診断、ドラッグ）。スケッチと合致で共用
core/cad/sketch    スケッチ（sketch.py: 要素・拘束・求解、kinds.py: 残差、ops.py: フィレット・トリム・ミラー・パターン・オフセット）
core/cad/part      フィーチャー（features/: base・sketch・solid・hole・dressup・reference・pattern・imported。種類ごとの名前・設定は
                   クラスに書く）、穴の規格（holes.py）、再生成エンジン（document.py: 差分再生成・ロールバック・抑制・エラー隔離）
core/cad/naming    トポロジカルネーミング（永続名）と参照の解決（救済・候補提示）
core/cad/kernel    カーネル IF（base.py）、mock（形状なし）、prism（押し出しのみの簡易形状）、
                   meshtopo.py（履歴を返さないカーネル用: 面メッシュから履歴・隣接を逆算。D65 で高速化）、usd_brep.py（実カーネル）、
                   meshbody.py（取り込んだメッシュを面に分けて形状にする。D71）
core/cad/assembly  アセンブリと合致（kinds.py: 合致の残差）、干渉チェック
core/cad/drawing   2 次元図面（D60）: hlr.py（投影・隠線）、document.py（シート・ビュー・寸法）、render.py（描く要素の一覧）、
                   pdf.py・dxf.py（書き出し。外部ライブラリなし。DXF の全角は Shift-JIS）
core/cad/geom.py   エッジの折れ線・円の当てはめ（スケッチの外部参照と図面で共用）
core/cad/doc       History（Undo/Redo）、expr（式・変数）、workspace（開いている文書・保存）、usdio（.usda の読み書き）、
                   meshio（STL・3MF・OBJ の読み書き）・exchange（取り込み・書き出し。D71）
core/cad/agent     AI エージェント（D67）: config（設定・~/.agentcad/agent.json）、providers（Claude = 公式 SDK / OpenAI 互換 = urllib）、
                   tools（CAD の API を呼ぶ道具・全権限）、render（形を PNG に）、prompt（システムプロンプト）、session（会話・裏のスレッド）
core/cad/api       rpc.py（文書・状態・Undo・ウィンドウ・表示の設定）、agent_api.py（AI）、feature_api.py（フィーチャーの作成・寸法・設定・ツリー）、
                   sketch_api.py（スケッチ編集）、asm_api.py、drawing_api.py（図面）、demos.py（デモ部品）、errors.py、
                   server.py（標準ライブラリの HTTP。POST /rpc）
app/package.json   tauri-cli（npm run tauri dev / build）
app/src-tauri      Tauri 2 の殻（src/main.rs、起動画面 dist/index.html、icons。gen/ は生成物で無視）
app/web            UI（外部ライブラリなし）。部品: viewer.js（自作 WebGL2。断面表示とハンドルも）、sketch.js・asm.js（編集器）、
                   select.js（選択・計測の計算）、toolbar.js（ツールバーの DOM とアイコン）。画面: core.js（土台: DOM・RPC・状態 app・
                   編集器の実体・ui 表）← evaluate.js / sketch_ui.js / props.js / windows.js ← asm_ui.js・drawing_ui.js（図面。
                   キャンバスは drawing.js）・agent_ui.js（AI パネル）← app.js（パートの画面・断面のパネル・起動）。下の層から上の層の関数を呼ぶときは core.js の ui 表を通す
scripts            ui_smoke.py（ヘッドレス Chrome で UI を操作して検証）、bench_solver.py・bench_regen.py（性能）、
                   *_probe_*.py（カーネルの調査。役目を終えたものも記録として残す）
run.bat            利用者用の起動（cd core && python -m cad.api）
```
設計の要点: ロジックはすべて Python、UI は状態を持たず API の返す状態を描くだけ（D1）。
編集系 API は 1 回 = 1 トランザクション（Undo 1 回分）で文書の状態を返す。

## usd-brep の落とし穴（PHASE0.md・D65）
- ブーリアンは入力を消費し、fillet/rotate/translate は入力を書き換える → アダプタは演算前に必ず `copy()`（D21）。消費済みの形状を使うとプロセスごと落ちる
- 演算の前後で面を追跡する手段が無い（面の hash は一時オブジェクトのアドレス）。面の由来は meshtopo がメッシュから逆算する
- tessellate は面ごとに三角形を続けて出すが、三角形 → 面 の対応は返さない（find_face で確かめる。D64・D65）
- 処理中に GIL を手放さない（スレッドで並列にしても速くならない）

## 作業の決まり
- 確認方法（Python は上の venv）
  - `cd core && python -m pytest -q`（2026-11-14 に 208 passed, 3 skipped。スキップは形状なしのカーネル用）
  - `python scripts/ui_smoke.py <出力先>`（画面操作の検証と撮影。撮った画像は目で見て確かめる）
  - 性能: `cd core && python ../scripts/bench_solver.py`（400 変数でドラッグ 100ms 以内を `test_performance_budget` で監視）
  - 再生成の速さ: `cd core && python ../scripts/bench_regen.py`（--profile で内訳、--names で面・エッジの名前の書き出し）。
    面の対応づけ（meshtopo・usd_brep.py）を変えたら `AGENTCAD_CHECK_MATCH=1` で全テスト（新旧の実装を比べる。D65）
- git: 1 論点 1 コミット。仕様・判断・計画が変わるときは同じ変更で `docs/` も更新する。
  コミットメッセージは英語、ドキュメントとコード内コメントは日本語
- 新しい判断は `docs/DECISIONS.md` に D 番号を振って追記。計画と変更履歴は `docs/PLAN.md`
- 利用者への説明は日本語で簡潔に（利用者の希望）。報告には「確認できたこと / できていないこと」を明記する
- 起動・ビルド用のバッチファイルや便利スクリプト・インストーラは作らない（利用者がコマンドを打つ。手順は README に書く）
- 利用者に試してもらう手順は Windows のコマンドで書く。画面出力を `>` でファイルに向けない（スクリプトは自分でレポートを書く）

## よく使う入口
- 起動（利用者）: `run.bat --demo`（板のデモ）/ `run.bat --demo car`（車のデモ）。デスクトップ版は `cd app` → `npm run tauri dev`
  （Python の場所は `agentcad.config.json`。例は `agentcad.config.example.json`）
- 起動（開発で画面だけ見る）: `cd core && python -m cad.api --kernel prism --no-browser`
- 保存形式: `docs/SPEC.md` 8 章（`.usda`。定義は `agentcad:definition` の JSON 文字列。旧 `.ucpart/.ucasm` は開くだけ）
- API の一覧: `docs/SPEC.md` 9 章、実装は `core/cad/api/*.py` の `methods`
- 画面のデバッグ用フック: `window.__agentcad = { viewer, sketch, asm, drawing, app, refresh, ... }`
- ツールバー（D53）: 画面の自動操作でタブ内のボタンを押すときは `scripts/ui_smoke.py` の `cmd()` を使う
