# copilot-usage CLI bundle（Agent / runtime 配置用）

2026-08-01 以降の Copilot CLI / VS Code Chat ログから、直接記録された AI Credits を集計します。
既定 DB は `output/copilot-usage.db`。既知の旧形式DBは明示的な全件scanで再生成してください。原本のない行は戻りません。
対象期間以前の token 履歴も新 DB には含めません。対象外期間は coverage 表示で区別します。
直接値が欠けた record は token を保持し、credits と金額を不明 (`NULL` / `-`) と警告で示します。
部分合計は取得済み値の小計で、`missing_credits_count` が欠落件数です。モデル ID はログの表記を維持します。
CLIは履歴DBと累積イベントを照合し、確定した部分をrequest単位のモデル・UTC利用日へ置き換えます。請求額の完全な再現を保証するものではありません。

Copilot / Copilot CLI のローカルログから AI Credits 使用量（ログの直接値）を集計するための、自己完結 CLI バンドルです。Python runtime 不要の単体バイナリ・一括実行スクリプトを同梱しています。GUI は使わず、scan → summary / Agent 可読 report（Markdown / JSON）/ HTML report までを非対話で完走させることを想定します。

複数rootはbinaryの `scan` へ `--cli-log-root` / `--chat-storage-root` を繰り返して渡します。
各CLI rootの親の履歴DBを使い、全入力を同じDBへ合算します。`--cli-history-db` の明示指定は異なるCLI rootが0〜1個の場合だけ使えます。
存在しないrootや入力履歴と同じ出力DBは保存前に拒否します。繰り返し指定は「最後だけ有効」ではなく合算です。
同じpathの重複は除きますが、別ファイルの同じCLI sessionは既存規則で除外します。
通常scanで対象から外したrootの保存済みデータは残り、`--full`は現在指定した全rootだけで再構築します。
`run_pipeline`の引数は単一rootのままなので、複数rootはbinaryを直接呼んでください。

Agent が使用量を機械的に読む場合は、軽量な `summary` JSON だけでなく、HTML report と同等の情報を装飾なしで持つ `report` command（Markdown / JSON）を使えます。`summary`（情報が軽量）と HTML（装飾が多い）の中間で、Agent が読みやすい出力です。

## バンドル構成

```text
copilot-usage-cli/
├── copilot-usage            # CLI 単体バイナリ（OS / arch 依存）
├── README.md                # このファイル
├── run_pipeline.sh          # 一括実行（macOS / Linux）
└── run_pipeline.ps1         # 一括実行（Windows / PowerShell）
```

`run_pipeline` は binary が同一ディレクトリに同梱されている前提で、追加設定なしに動作します。

## 配置

このバンドルディレクトリごと、利用したい場所へコピーして使います。例:

```bash
cp -R copilot-usage-cli ~/Applications/copilot-usage-cli
# 以降は配置先で実行する
~/Applications/copilot-usage-cli/run_pipeline.sh
```

agent skill から呼ぶ場合も、このディレクトリを skill に同梱し `run_pipeline.sh`（または binary）を起点にします。binary だけを別の場所へ置く場合は、環境変数 `COPILOT_USAGE_BIN` で実行体を明示できます。

## 一括実行（推奨）

`run_pipeline` は次を順に実行します。

1. `scan`（存在する log root のみ対象）
2. `summary`（JSON）/ `report`（Markdown・JSON）/ `export-html`（offline HTML）— `--mode` で選択

引数なしで既定値（当月・USD/JPY=160・OS 既定 log root）で完走します。

```bash
# macOS / Linux：当月分を集計し summary.json と HTML report を出力（既定 mode=both）
./run_pipeline.sh

# summary だけが欲しい Agent 用途
./run_pipeline.sh --from 2026-08-01 --to 2026-08-30 --mode summary

# Agent 可読 report（Markdown）を出力
./run_pipeline.sh --from 2026-08-01 --to 2026-08-30 --mode markdown

# Agent 可読 report（JSON）を明細込みで出力
./run_pipeline.sh --from 2026-08-01 --to 2026-08-30 --mode json --detail

# summary + html + markdown + json をまとめて出力
./run_pipeline.sh --mode all

# 全体読み直し（scan 由来データを reset。schema は保持）
./run_pipeline.sh --full
```

```powershell
# Windows / PowerShell
./run_pipeline.ps1 -From 2026-08-01 -To 2026-08-30 -Mode markdown
./run_pipeline.ps1 -Mode json -Detail
```

### オプション / 環境変数

| 引数 | 環境変数 | 既定値 |
| --- | --- | --- |
| `--db` | — | `<out>/copilot-usage.db` |
| `--out` | `COPILOT_USAGE_OUT` | `./output` |
| `--cli-log-root` | `COPILOT_USAGE_CLI_LOG_ROOT` | `~/.copilot/session-state` |
| `--chat-storage-root` | `COPILOT_USAGE_CHAT_STORAGE_ROOT` | OS 既定の `workspaceStorage` |
| `--from` / `--to` | `COPILOT_USAGE_FROM` / `COPILOT_USAGE_TO` | 当月初〜当月末 |
| `--rate` | `COPILOT_USAGE_RATE` | `160` |
| `--mode` | `COPILOT_USAGE_MODE` | `both`（`summary` / `html` / `markdown` / `json` / `both` / `all`） |
| `--detail` | `COPILOT_USAGE_DETAIL` | off（markdown/json report に明細を含めるか） |
| `--full` | — | 差分 scan |
| `--bin`（環境変数のみ） | `COPILOT_USAGE_BIN` | 同梱 binary |

実行体は `COPILOT_USAGE_BIN` → 同梱 `copilot-usage` → PATH 上の `copilot-usage` の順で解決します。

### 出力

通常scanは、変更のないCopilot CLIイベントファイルの本文読込を省略し、変更されたファイルを全体から読み直します。履歴DBとVS Code Chatは毎回読みます。初回と解析規則を更新した後は一度読み直し、`--full`は常に指定入力をすべて再解析します。scanの保存件数が0でも保存済みの使用量は残ります。ファイルの更新情報まで同じ状態に戻した変更を再評価する場合は、全体読み直しを使ってください。特にWindowsでは、同じサイズで書き換えて更新時刻を戻した場合は変更を検出できないことがあります。

- `<out>/copilot-usage.db` — 正規化済み usage を保持する SQLite DB（再実行で差分更新）
- `<out>/summary.json` — `--mode summary|both|all` 時の軽量集計 JSON（最小の機械可読入力。文字コードは UTF-8（BOM なし）で、Windows PowerShell 5.1・PowerShell 7・`run_pipeline.sh` で共通）
- `<out>/copilot-usage-report.md` — `--mode markdown|all` 時の Agent 可読 Markdown report（HTML 同等情報・装飾なし）
- `<out>/copilot-usage-report.json` — `--mode json|all` 時の Agent 可読 構造化 JSON report（HTML 同等情報）
- `<out>/copilot-usage-report.html` — `--mode html|both|all` 時の offline HTML report

`report`（markdown/json）は HTML report と同じ集計（meta / totals / model / daily / confidence / diagnostics）を持ち、数値は HTML / summary と一致します。明細（workspace/session/run 単位）は既定で省略し、`--detail` 指定時のみ含めます。JSON report は HTML 用の rich データ由来で、`summary.json`（`by_model` を持つ軽量 JSON）とは schema が異なる別 contract です。

`<out>`（既定 `./output`）は実行時のカレントディレクトリ基準で解決します。出力先を固定したい場合は `--out`（または `COPILOT_USAGE_OUT`）で絶対パスを指定してください。

## 個別コマンドで実行する場合

スクリプトを使わず Agent が直接コマンドを組み立てる場合は次の順で呼びます。

```bash
BIN=./copilot-usage
DB=./output/copilot-usage.db

"$BIN" scan           --db "$DB" --cli-log-root ~/.copilot/session-state
"$BIN" summary        --db "$DB" --from 2026-08-01 --to 2026-08-30 --format json
# Agent 可読 report（Markdown は stdout 既定、JSON はファイルへ）
"$BIN" report         --db "$DB" --from 2026-08-01 --to 2026-08-30 --usd-jpy-rate 160
"$BIN" report         --db "$DB" --from 2026-08-01 --to 2026-08-30 --usd-jpy-rate 160 \
                      --format json --detail --output ./output/copilot-usage-report.json
"$BIN" export-html    --db "$DB" --from 2026-08-01 --to 2026-08-30 \
                      --output ./output/copilot-usage-report.html --usd-jpy-rate 160
```

`report` は `--format markdown`（既定）/ `--format json` を選べ、`--output` 省略時は stdout、指定時はファイルへ書き出します。明細は `--detail` 指定時のみ含めます。

`scan` は `--cli-log-root`、`--chat-storage-root`、`--cli-history-db` のいずれか一つでも実行できます。

## Agent 利用上の注意

- `summary` は stdout に JSON のみを出力するため、そのままパースできます（`run_pipeline` は `summary.json` にも保存）。
- 使用量を詳しく読むには `report --format json`（構造化・機械処理向け）または `report --format markdown`（人間 / Agent 可読）を使います。`--output` 省略時は stdout に出力するため、Agent はそのまま取り込めます。明細が不要なら既定（`--detail` なし）のままで十分です。
- 失敗時は exit code 非 0 と stderr メッセージを返します（`scan` で log root が無い場合など）。Agent 側で exit code を確認してください。
- `import-task-manifest` は未実装で、実行すると exit code `2` を返します。
- raw prompt / response / tool output / raw absolute path は DB・summary・report（markdown/json）・HTML のいずれにも出力しません。path 情報は hash + basename alias に限定されます。
- macOS の Gatekeeper では未署名バイナリの初回起動がブロックされることがあります。必要に応じて `xattr -dr com.apple.quarantine copilot-usage` で quarantine 属性を外してください。

診断は解析したファイルの全期間を対象とするため、2026-08-01 より前の警告と非加算の
未終了警告も含みます。使用量の対象期間・画面の期間フィルターとは範囲が異なります。

## CLI累積値の読み方

履歴と照合できない部分は、全期間で最後の完全なセッション累計を選び、そのイベント日へ帰属する。
`Unattributed session total`はモデル未配賦の直接総額で、特定モデルへの推測配賦ではない。
`model_key: null`、`attribution_scope: session_total`で識別する。
`copilot_cli_token_reference`はtoken参照行で、credits欠落ではない。
`cli_usage_not_finalized`があっても選択済み総額は加算済み。後続利用が未反映の可能性を示す。
GitHub請求期間との完全一致を意味しない。

既知の旧形式DBは `--full` で原本から再生成する。通常scanは旧形式を変更せず再生成を案内し、未知・混在DBはfullでも拒否する。
ログ由来でない旧検証記録なども破棄されるため、必要なデータは事前に退避する。
日別表には未配賦額を別列、グラフには別系列として表示する。円グラフの未配賦区分もモデルではない。

### request履歴DBと会話圧縮

`--cli-log-root` を指定すると、その親の `session-store.db` を読み取り専用で自動検出します。
明示する場合は `--cli-history-db <path>` を使います。この指定はrequest取り込みと圧縮照合の両方に優先します。
ChatだけのscanではCopilot homeを自動探索しません。GUIでは既存のCLI log rootから同じ自動検出を使います。

```bash
# イベントがないセッションも残存履歴から回収する
copilot-usage scan --db output/copilot-usage.db --cli-history-db ~/.copilot/session-store.db --full
# 両入力を照合して全体を読み直す
copilot-usage scan --db output/copilot-usage.db --cli-log-root ~/.copilot/session-state --full
```

Chatも再収集する場合は `--chat-storage-root` を指定してください。全件scanは指定された入力だけで作り直します。
同じhomeの自動検出は既存の `run_pipeline` にも適用されます。別の履歴DBを明示する場合は上記scanを直接使います。

履歴内の保存重複を除き、累積値・時刻・圧縮の一対一対応を確認できたsessionは履歴の直接値へ置き換えます。
圧縮もrequest履歴から1回だけ数え、元の累積・token参照・圧縮イベントを重ねて加算しません。
日付はrequestのUTC日へ変わります。イベントだけの値は従来の候補日に残します。
履歴だけのsessionは取得済み小計で、全履歴が揃った保証ではありません。未対応形式や曖昧な重複は加算せず診断します。

JSON明細の `observed_nano_aiu` は観測額、`reported_nano_aiu` は採用額です。観測額を総額へ再加算しないでください。
`coverage` / `history_coverage` とsource・confidenceで取得元と照合状態を確認できます。
必須4種tokenと任意reasoning tokenの欠落は別に扱い、未取得値を実測0として表示しません。

自動検出した履歴が失われると、通常scanでもその入力範囲を現在の情報で再判定し、過去小計が減り得ます。
明示した履歴が読めない場合は保存前に失敗します。入力DBと出力DBは別ファイルにしてください。
現行形式は `010_cli_request_history_baseline`。009を含む既知の旧形式は明示的な全件scanで再生成します。
原イベントがなくてもDBに履歴が残れば回収できますが、両方を失った使用量は復元できません。

## GUI で CLI の DB を開く

GUI の未保存時の既定 DB は利用者別のアプリ directory にあり、CLI の `./output/copilot-usage.db` とは別です。CLI を実行した directory から `copilot-usage gui --db output/copilot-usage.db` を実行するか、GUI の DB 欄へそのパスを指定してください。
