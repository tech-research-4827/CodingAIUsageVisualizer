# Coding AI Usage Visualizer

Copilot CLI / VS Code Copilot Chat のローカルログと使用履歴DBから、使用量を集計するツールです。CLI、デスクトップGUI、オフラインHTML、JSON、Markdownで確認できます。

製品名は **Coding AI Usage Visualizer** ですが、現在の解析対象は **GitHub Copilotのみ** です。Python package名（`copilot_usage_visualizer`）とコマンド名（`copilot-usage`、`copilot-usage-gui`）は従来のままです。GitHubまたはMicrosoftの公式製品ではなく、両社による承認・保証・サポートを受けていません。

## 前提環境

- Pythonの導入条件：3.11以上。
- 各版で確認したOS・CPU・Python版と未確認の条件は、その版のRelease notesに記載します。記載のない組合せは未検証です。Pythonの導入条件は、すべての組合せでの動作保証を意味しません。
- GUIにはデスクトップの表示環境が必要です。公開候補のGUI実画面は公開前に確認し、Release notesには確認した環境を記載します。
- 解析対象のCopilotログ・使用履歴DBがローカルに必要です。GitHubへのログインやAPI tokenを本ツールに渡す必要はありません。

## 導入

macOSのターミナルで、公開ソースを取得して通常installします。

```sh
git clone https://github.com/tech-research-4827/CodingAIUsageVisualizer.git
cd CodingAIUsageVisualizer
python3 -m venv .venv
source .venv/bin/activate
python -m pip install .
copilot-usage --version
```

Releaseのソースarchiveを使う場合は、展開したディレクトリでvenv作成から実行してください。版を固定して取得したい場合は、clone後に公開済みtag（例：`v0.3.0`）へ切り替えてからinstallします。

`requirements.txt` を使う場合もアプリ自体のinstallが必要です。

```sh
python -m pip install -r requirements.txt
python -m pip install .
```

requirementsのみでは `copilot-usage` / `copilot-usage-gui` は登録されません。開発依存やeditable installは不要です。

## 入力と出力

入力はご自身がアクセス・解析する権限を持つデータだけを指定し、所属組織の規則と適用される利用規約に従ってください。

| 入力 | 指定方法 | macOSでの一般的な場所 |
| --- | --- | --- |
| Copilot CLIセッション | `--cli-log-root` | `~/.copilot/session-state` |
| VS Code Chatセッション | `--chat-storage-root` | `~/Library/Application Support/Code/User/workspaceStorage` |
| Copilot CLI使用履歴DB | `--cli-history-db` | `~/.copilot/session-store.db` |

CLIログを指定し、履歴DBを省略すると、CLIログの親ディレクトリにある `session-store.db` を探します。明示指定した履歴DBが優先されます。CLIログ・Chat・履歴DBはいずれか一つからでも解析できます。VS Codeの派生版や独自の保存先を使う場合は実際の場所を指定してください。

`--cli-log-root` と `--chat-storage-root` は繰り返し指定し、すべての入力を合算できます。
履歴DBを省略した場合は各CLI rootの親から読みます。明示の `--cli-history-db` は、異なるCLI rootが0個または1個のときだけ使えます。
存在しない・ディレクトリでないrootや、履歴DBと同じ実体の出力DBは保存前に拒否します。
同じ入力pathは重複除去しますが、別のイベントファイルに同じCLI sessionがあれば集計から除外して診断します。
通常scanは指定から外したrootの保存済みデータを保持し、全体読み直しは現在指定したすべての入力だけで再構築します。
繰り返し指定は従来の「最後だけ有効」から「合算」に変わりました。

WindowsからWSL内のCopilot CLIの記録も `--cli-log-root` に指定できます（`\\wsl.localhost\<distribution>\home\<user>\.copilot\session-state` または `\\wsl$\...` の形式）。
履歴DBは共有越しに開かず、指定したdistribution内の `python3` から使用量の列だけを読み取り専用で取得します。
WSL内にはPython 3.10以上と標準の `sqlite3` モジュールが必要です。WSL内へ本ツールを導入する必要はありません。
取得は30秒で時間切れになります。停止中のWSLは読み取り時に起動するため、待ち時間が生じます。
取得に失敗すると、scan結果JSONの `history_warnings` とGUIの実行Logに確認手順を表示し、`cli_history_wsl_` で始まる診断コードを記録します。
明示した `--cli-history-db` を取得できない場合は保存前に失敗します。暗黙の履歴取得の失敗ではイベントの取り込みを続けますが、次に取得できるまで合計や日別内訳が変わり得ます。
WSLの読み取りの確認状況は、各版のRelease notesを参照してください。

通常の CLI の既定解析DBは、**コマンドを実行したディレクトリを基準とする** `output/copilot-usage.db` です。インストール先基準ではありません。`--db` で変更できます。出力DBは本ツール専用の新しいファイルにしてください。

**DB・レポートの出力先（CLIの `--db` / `--output`、GUIの設定欄）に、入力セッション、Copilotの使用履歴DB、その他の大切な既存ファイルを指定しないでください。レポートは指定先の既存ファイルを上書きします。** 入力と出力は別ディレクトリに分け、必要なデータをバックアップしてください。

`.gitignore` は `.venv/`、`output/`、一般的なDB拡張子などを除外します。Gitによる誤登録を減らすための設定で、上書き防止機能ではありません。別の出力先や独自拡張子を使う場合はご自身で管理してください。

## 使い方

以下の例の入力は、手元に存在するものだけを指定してください。

```sh
copilot-usage scan \
  --db output/copilot-usage.db \
  --cli-log-root ~/.copilot/session-state \
  --chat-storage-root ~/Library/"Application Support"/Code/User/workspaceStorage \
  --cli-history-db ~/.copilot/session-store.db

copilot-usage summary --db output/copilot-usage.db \
  --from 2026-09-01 --to 2026-09-29 --format json

copilot-usage report --db output/copilot-usage.db \
  --from 2026-09-01 --to 2026-09-29 --usd-jpy-rate 150 \
  --format markdown --output output/usage.md

copilot-usage report --db output/copilot-usage.db \
  --from 2026-09-01 --to 2026-09-29 --usd-jpy-rate 150 \
  --format json --detail --output output/usage.json

copilot-usage export-html --db output/copilot-usage.db \
  --from 2026-09-01 --to 2026-09-29 --usd-jpy-rate 150 \
  --output output/usage.html
```

日付と換算レートは目的に合わせて変更してください。レポートの出力先ディレクトリは事前に作成します。上の例ではscanが `output/` を作成します。`report` の `--output` を省くと標準出力へ表示します。終了コードは成功が0、通常の処理エラーが1、引数エラー・未実装コマンドが2です。

HTMLはJavaScriptとライセンス通知を一つのファイルに含み、CDNなしでブラウザ表示できます。JSONの `summary` と `report` は別の出力形式です。`report --detail` で明細を追加できます。

複数の環境でそれぞれscanした解析DBは、新しいDBへ統合できます。

```sh
copilot-usage merge \
  --input output/copilot-usage.db \
  --input other/copilot-usage.db \
  --output output/merged.db
```

`--input` は2個以上指定します。入力DBは読み取り専用で開き、本ツールの現行形式と構造が一致するものだけを受け付けます。
既存ファイルや入力と同じファイルを出力に指定すると拒否し、追記・上書きはしません。出力先のディレクトリは事前に作成してください。
複数の入力に同じsessionがある場合は、入力番号付きで報告します。CLIで両方の入力にイベントがあるsessionは集計から除外し、履歴だけが重なるsessionは未確定として加算しません。Chatの重なりは両方を加算します。
統合DBはレポート用の派生物です。統合DBとその入力を再びmergeすると重複します。各環境で更新してから、改めて統合してください。
入力にするDBは、scanやGUIで使用中のものを避け、使用を終えたものをコピーして使ってください。

GUIは次で起動します。

```sh
copilot-usage-gui
```

`copilot-usage gui` でも起動できます。設定欄で入力・解析DB・レポート出力先・期間・換算レートを確認してから、scan、表示更新、HTML exportを行います。既定の期間は起動月、換算レートは160です。GUIの設定が別の入力を指していないか、全体読み直し前にも確認してください。

CLI・Chatの入力欄はそれぞれ1行1パスです。Explorerの「パスのコピー」で付く囲み引用符と前後空白を除き、空行は無視します。
既定の場所が存在しない取得元は、入力欄が空欄で始まります。
空欄はその取得元を無効にし、カレントディレクトリは読みません。行を削除して保存済みデータも除くには全体読み直しを使います。
GUIのscanは別プロセスで実行し、下端に進捗を表示します。読み込み100%の後も保存・表示更新が続く場合があります。

「設定・アクション」タブの **設定を保存** で、入力・解析DB・レポート出力先・期間・換算レート・表示倍率（UI scale）・文字の大きさ（Font size）を保存し、次回の起動で復元します。
scan・表示更新・HTML export・終了では自動保存しません。起動時は項目ごとに、明示した起動引数、保存した設定、既定値の順で使います。
保存先は画面の「GUI 設定」欄（Settings file）に表示します。

| OS | 設定ファイル |
| --- | --- |
| macOS | `~/Library/Application Support/CopilotUsageVisualizer/gui-settings.json` |
| Windows | `%LOCALAPPDATA%\CopilotUsageVisualizer\gui-settings.json` |
| Linux | `$XDG_CONFIG_HOME/CopilotUsageVisualizer/gui-settings.json`（未設定などの場合は `~/.config/` 配下） |

配布した実行体とソース起動は同じOSアカウントの設定を共有し、実行体の隣や `.app` 内へは保存しません。Windows の `LOCALAPPDATA` は絶対パスの指定が必要です。
旧版の実行体の隣にある設定は自動では読みません。新しい保存先に設定がない場合だけ、旧 `gui-settings.json` を上表の場所へ手動コピーしてください。既存の設定を上書きせず、コピー前に元設定を保管してください。コピーしてもDBやHTMLは移動せず、保存済みパスをそのまま使います。

未保存時の GUI の DB・HTML は以下の `output/` 配下です。起動場所によって変わりません。

| OS | GUI の既定出力 directory |
| --- | --- |
| Windows | `%LOCALAPPDATA%/CopilotUsageVisualizer/output/` |
| macOS | `~/Library/Application Support/CopilotUsageVisualizer/output/` |
| Linux | `$XDG_DATA_HOME/CopilotUsageVisualizer/output/`（未設定・空・相対値は `~/.local/share/` 配下） |

通常 CLI の `output/copilot-usage.db` とは別です。CLI を実行した directory から `copilot-usage gui --db output/copilot-usage.db` を実行するか、GUI の DB 欄へ指定すると CLI の結果を開けます。
HTML は出力先欄、または export 完了 Log の `output` の全パスから開いてください。見つけやすい directory を出力先に選んで保存することもできます。

設定ファイルには、入力・出力の絶対パスが平文で入ります。使用量データやログ本文は入りません。設定ファイルを共有しないでください。
保存した期間は月が変わっても維持します。相対パスは保存時の作業ディレクトリを基準に絶対パスへ変換します。

表示倍率と文字の大きさ（100 / 115 / 125 / 150%）は、保存後の次回起動から反映します。表示倍率の「自動」は、WindowsではOSの表示倍率を使い、macOS・Linuxでは100%です。WSLgでは100〜250%から選んで保存してください。
大きくした結果、最小のウィンドウが画面に収まらない場合は、どちらかを下げてください。

期間はYYYY-MM-DDの直接入力のほか、日付の選択と「先月」「今月」「今日」で変更できます。集計への反映は表示更新で行います。

「セッション使用量」タブは、scanせずにCopilot CLIの記録を直接読み、一つのセッションのAI Credits・token・コンテキストの推移、エージェント別の内訳、監査詳細を表示します。
CLIログ欄のrootの親（例：`~/.copilot`）にある使用履歴DB・セッションのイベントファイル・GitHub Copilotアプリの `data.db`（存在する場合）を読み取り専用で開き、読んだ内容を保存しません。
自動更新（Auto refresh）は既定で無効です。WindowsからWSLの共有パスにある記録は、このタブでは読みません。解析DBや統合DBの内容の確認には使えません。

## セッション使用量の canvas・単独起動

公開 source の clone または Source code archive には、`extensions/copilot-session-usage/` が含まれます。Python の `pip install` や実行体への同梱ではなく、この directory を使う別の起動方法です。以下は取得した source の root で実行します。

App なしで使う場合は、利用者が導入した **Node.js 24 以上**とブラウザが必要です。Python package の導入は不要です。

```sh
node extensions/copilot-session-usage/standalone.mjs
```

既定のブラウザが開き、記録のある最新セッションを表示します。ブラウザを自動で開かない場合は `--no-browser` を付け、端末の URL を開いてください。タブを閉じても server は動き続けるため、起動した端末で **Ctrl+C** を押して終了します。再起動ごとに URL の port と token が変わります。

読み取り元は `COPILOT_HOME`、未設定なら `~/.copilot` です。`--home <directory>` で指定した場合はその1か所だけを読み、追加 source の保存設定は使いません。`--session <id>` で最初のセッションを指定できます。

Copilot App の canvas として使う場合は、Python 3.11 以上で次の配置ツールを実行します。既存の Python 導入環境を使えます。

```sh
python tools/install_canvas_extension.py --dry-run
python tools/install_canvas_extension.py
```

計画を確認してから配置してください。`COPILOT_HOME`（未設定なら `~/.copilot`）の `extensions/copilot-session-usage/` に実行素材だけを複製します。配置先を変える場合は両コマンドに `--copilot-home <directory>` を付けます。このツールの印がある既存配置は丸ごと更新します。別のツールで作った同名配置や symbolic link は置き換えません。

配置後、App で extension を再読み込みするか session を再起動し、`Session usage`（canvas ID `session-usage`）を開きます。App の対応する extension 機構と SDK が必要です。SDK はホストが解決するため、npm から別に導入しません。パネルを閉じるとパネル状態を破棄し、App の session 終了時に extension process が止まります。実際に確認した App/CLI 版・OS と未確認の条件は、各版の Release notes を参照してください。

単独起動では macOS・Windows・Linux の既定ブラウザを使います。WSL 内で起動して Windows ブラウザを使う経路もあり、localhost 転送が無効な WSL では開けない場合があります。Windows から WSL の記録を Sources で追加する場合は、その distribution に SQLite 標準モジュール付き Python 3.10 以上が必要です。WSL の App `data.db` は読みません。これらの前提条件と、各環境での実測済み範囲は同じ意味ではありません。

CLI の `session-store.db`・`events.jsonl` と、存在すれば App の `data.db` を読み取り専用で読みます。Copilot の内部形式に依存するため、更新で一部を読めなくなったり、記録の欠落・重複等により表示が不完全になったりします。合計は使用履歴から取得できた小計で、累積イベントや App の合計は参考比較です。請求額の正本として使わないでください。

単独起動と canvas の server は `127.0.0.1` だけで待ち受け、起動ごとの token と Host を検査します。画面資材・グラフは同梱され、CDN は使いません。使用量の解析結果は保存しません。本文・tool 入出力・要約・作業 directory 等は表示用データの対象にしない設計です。

Sources で追加した読み取り元は、canvas と単独起動で共有するローカル設定に保存します。Windows は `%APPDATA%/copilot-session-usage/sources.json`、macOS は `~/Library/Application Support/copilot-session-usage/sources.json`、Linux は `$XDG_CONFIG_HOME/copilot-session-usage/sources.json`（未設定なら `~/.config/` 配下）です。利用者のパスやユーザー名が含まれ得るため設定を共有しないでください。設定画面のパスや読み取り元の表示名は利用者の環境から生成する値で、完全匿名の表示ではありません。表示用 API と canvas の action が返す要約も、共有前に内容を確認してください。

## 集計の意味と既知の制約

通常scanは、変更のないCopilot CLIイベントファイルの本文読込を省略し、変更されたファイルを全体から読み直します。履歴DBとVS Code Chatは毎回読みます。初回と解析規則を更新した後は一度読み直し、`--full`は常に指定入力をすべて再解析します。scanの保存件数が0でも保存済みの使用量は残ります。ファイルの更新情報まで同じ状態に戻した変更を再評価する場合は、全体読み直しを使ってください。特にWindowsでは、同じサイズで書き換えて更新時刻を戻した場合は変更を検出できないことがあります。

- 対象は2026-08-01以降の記録です。それより前の履歴の互換取り込みは提供しません。
- Creditsはログ・使用履歴にある直接値を使います。欠落は0や推定額ではなく不明として扱い、小計と欠落件数を表示します。
- CLIは使用履歴と累積イベントを照合します。確定できた部分はrequestのモデル・UTC利用日、残りは採用した累積イベントを使います。日別・月別集計がGitHubの請求期間と一致する保証はありません。
- 後から追記された途中・終了時の累積使用量は、イベントファイルの変更として通常scanで再評価します。更新情報まで同じ状態に戻した書き換えは検出できないため、次節の注意に従って全体読み直しを行います。
- ログ削除・ローテーション・履歴DBの欠落・形式変更により取得できない使用量があります。表示額は請求書・支払額の正本として使わないでください。
- モデルの推定帰属は `*` 等で区別します。モデル帰属の推定はCreditsそのものの推計ではありません。
- `import-task-manifest` は未実装です。

## 更新とDB再生成

更新前にRelease notesの互換性・DB再生成・対応環境の注意を確認します。公開済みtagを指定して更新する例です（`vX.Y.Z` は実在するtagへ置き換えます）。

```sh
git fetch --tags
git switch --detach vX.Y.Z
source .venv/bin/activate
python -m pip install .
copilot-usage --version
```

ソースarchiveから導入した場合は新しい版を別ディレクトリへ展開して導入し、既存DBを使うなら `--db` で同じ場所を指定します。通常installなのでソースの取得だけでは更新されません。

既知の旧形式DBは、通常scanや参照では変更せず、再生成を案内します。全体読み直しは本ツールの派生DBを削除・再構成する操作です。未知の形式・別アプリのDBは全体読み直しでも拒否します。

**全体読み直しでは以前取り込んだすべての入力（CLIログ、Chatの保存先、明示した使用履歴DB）を再指定してください。省いた入力の集計は、原本が残っていてもDBから消えます。** GUIでは同じ入力設定を維持して「全体読み直し」を行ってください。ただしGUIには使用履歴DBの明示指定欄がありません。CLIで独自の `--cli-history-db` を指定して取り込んだDBは、GUIで全体読み直しせず、CLIでその履歴DBも再指定してください。

```sh
copilot-usage scan --full \
  --db output/copilot-usage.db \
  --cli-log-root ~/.copilot/session-state \
  --chat-storage-root ~/Library/"Application Support"/Code/User/workspaceStorage \
  --cli-history-db ~/.copilot/session-store.db
```

元のログ・履歴DBが失われたデータは再生成できません。古いDB内だけに残る集計や独自の検証記録も失われ得ます。必要なら入力と解析DBを別途退避してください。本ツールは自動バックアップしません。再生成で入力ログやCopilotの履歴DBを移行・復元することはありません。

## データの扱い

本ツールはローカルで解析し、解析結果を外部サービスへ送る機能を持ちません。入力ログ本文は読み取り時に処理しますが、raw prompt・response・tool output・元の絶対パスを解析DBやレポートに保存しない設計です。パスの代わりにhashと表示用aliasを使います。例外として、GUIの設定ファイルには利用者が設定欄に入力したパスを保存します（前述）。

alias、モデル、日時、使用量から業務情報が分かることはあるため、DBやレポートの共有前に内容を確認してください。匿名化の完全性は保証しません。CopilotやVS Code自体の通信・同期設定は本ツールとは別です。

## ライセンス・第三者ソフトウェア

本プロジェクトは [MIT License](LICENSE) です。Copyright (c) 2026 tech-research-4827。

実行依存のDear PyGuiとPlotlyはMIT、Jinja2はBSD-3-Clauseです。依存ライブラリはそれぞれのライセンスに従い、本プロジェクトのMITがそれらを再許諾するものではありません。

セッション使用量 extension の Node.js とホスト提供の `@github/copilot-sdk` は実行環境側の依存で、その本体を本配布に同梱しません。SDK は [GitHub 提供の MIT License](https://github.com/github/copilot-sdk/blob/main/LICENSE)、Node は [本体と同梱依存の各許諾](https://github.com/nodejs/node/blob/main/LICENSE) に従います。extension の画面・SVG 描画は本プロジェクトの実装で、npm の第三者 package を同梱していません。[ホストの extension 導入方式](https://github.com/github/copilot-sdk/blob/main/nodejs/docs/extensions.md) と [App の canvas 案内](https://docs.github.com/en/copilot/how-tos/github-copilot-app/working-with-canvas-extensions) も参照してください。

生成HTMLはPlotly.jsとその第三者コードを含みます。[第三者ライセンス原稿](src/copilot_usage_visualizer/report/third_party_licenses.txt)をHTMLにも同梱しています。HTMLを再配布するときも通知を保持してください。通知と同梱コードを揃えるためPlotlyは6.8.0に固定しています。独自に依存を置き換える場合は、対応するライセンス表記も確認してください。

## 免責事項

本ソフトウェアは現状のまま提供されます。正確性、完全性、特定目的への適合性、継続的な動作、GitHubの請求額との一致を保証しません。

適用法令で許される最大限の範囲で、作者および著作権者は、本ソフトウェアの使用または使用不能により生じたセッション情報その他のデータの消失・破損、業務中断、逸失利益その他の損害について責任を負いません。ただし、故意または重大な過失による損害その他法令上免責が認められない責任は、この限りではありません。詳細は [LICENSE](LICENSE) を参照してください。

入力ログ・使用履歴DBは読み取り用として扱う設計ですが、データが破損しないことを保証するものではありません。前述の出力先分離・バックアップ・全体読み直しの注意を確認してください。

## 問い合わせと貢献

不具合報告・要望は [GitHub Issues](https://github.com/tech-research-4827/CodingAIUsageVisualizer/issues) で受け付けます。版番号、OS・CPU・Python版、再現手順を記載してください。生のセッションログ、prompt・response、tokenや秘密情報、個人を特定するパスは投稿せず、合成した再現例を使ってください。

PRによる提案も受け付けますが、公開repositoryのPRは直接mergeせず、開発側で確認・反映して次回の公開へ含めます。ご自身が提供する権利を持つ変更をMITで提案してください。採用した貢献は公開ハンドルでRelease notesに記録し、必要な第三者の著作権表示は保持します。

## バージョン

初回版は `v0.1.0`。0.xの間は互換な修正・文書修正をPATCH、機能追加・互換性を壊す変更をMINOR更新とし、破壊的変更はRelease notesに明記します。1.0以降の破壊的変更はMAJOR更新です。公開済みtagを付け替えたり、同じ版番号で内容を差し替えたりしません。

## 実行体のビルドとデスクトップ起動

公開 source の clone、または Release の Source code archive を展開した directory で実行します。OS・CPU ごとにその環境上でビルドしてください。ビルド済みバイナリはこの source 配布には含めません。
Python 3.11 以上と pip、対象 OS/CPU に対応する Dear PyGui・PyInstaller の依存が必要です。Python の最低版表記と全 OS/CPU の動作確認は同じ意味ではありません。Linux では glibc、GUI/display session、OpenGL と Dear PyGui が使うシステムライブラリが必要です。異なる Linux や古い OS へ持ち出す場合はその環境でも確認してください。

上流 Dear PyGui 2.3.1 は Windows x86-64、macOS ARM64、Linux x86-64 / ARM64 向けのwheelを配布しています ([配布一覧](https://pypi.org/project/dearpygui/2.3.1/))。選んだPython版のwheelが必要で、Intel Mac・Windows ARM・32bit等を確認済みとはしません。Linuxは [上流のGPU条件](https://dearpygui.readthedocs.io/en/latest/about/repo-info.html) の OpenGL 3 を満たす必要があります。これらは依存の前提であり、本アプリの実測結果は各版のRelease notesに記録します。

macOS / Linux:

```sh
python3 -m venv .venv
.venv/bin/python -m pip install ".[build]"
bash scripts/build_executables.sh
```

Windows (PowerShell):

```powershell
py -m venv .venv
.venv/Scripts/python -m pip install ".[build]"
./scripts/build_executables.ps1
```

PowerShell の script 実行が環境の規則で制限されている場合は、組織の規則を確認するか `scripts\build_executables.bat` を使ってください。全体の実行policyを変更する手順は不要です。
ビルドは `dist/` 内の既存生成物を更新します。利用者のDB・出力・設定をその directory へ同梱して配らないでください。

| OS | GUI | CLI |
| --- | --- | --- |
| Windows | `dist/copilot-usage-gui.exe` | `dist/copilot-usage.exe` |
| macOS | `dist/Coding AI Usage Visualizer.app` (依存を束内に同梱) | `dist/copilot-usage` |
| Linux | `dist/copilot-usage-gui`、`.desktop`、`usage-icon.png` | `dist/copilot-usage` |

`dist/copilot-usage-cli/` はCLI・README・pipelineをまとめたbundleです。directoryごとコピーすればPythonなしで使えます。[CLI bundleの説明](packaging/public/cli_bundle_README.md)を参照してください。CLIにもGUI依存と、SQL・offline HTML・第三者通知を同梱します。

Windows は Explorer から `.exe` を開きます。macOS は `.app` 全体を利用者の Applications 等へコピーし、Finder から開き、Dock へドラッグして登録できます。`.app` 内の実行体だけを取り出さないでください。
macOS の外部配布向け Developer ID 署名・公証は行っていません。初回起動が止められる場合は、入手元を確認して OS の当該アプリだけの起動許可操作に従ってください。OS 全体の保護を無効にしないでください。

Linux は実行権限を保ち、実行体とPNGを配置した後にランチャーを生成し直します。build時の `.desktop` はその時点の `dist/` を指し、移動後の場所を自動追跡しません。

```sh
chmod +x /path/to/copilot-usage-gui
.venv/bin/python packaging/desktop_launcher.py --executable /path/to/copilot-usage-gui --icon /path/to/usage-icon.png --output ./io.github.tech-research-4827.CodingAIUsageVisualizer.desktop
mkdir -p ~/.local/share/applications
cp ./io.github.tech-research-4827.CodingAIUsageVisualizer.desktop ~/.local/share/applications/
```

パスに空白・引用文字がある場合はshellの引数を引用してください。ランチャーは実際の絶対パスを使います。`%`・`=`・改行を含む配置先は未対応として拒否するため、別の配置先を選んでください。生成先が既存の場合はそのランチャーを更新します。
アプリ一覧から起動するか、デスクトップへコピーしてその環境の「起動を許可」操作を行ってください。登録・許可操作はデスクトップ環境により異なります。
WSL では Linux 内でビルドし、Linux ファイルシステム上の clone と venv を使ってください。Windows の checkout/venv を `/mnt/c` 経由で共用しません。GUI は WSLg 等の表示環境が必要です。WSLg の Start メニューに登録される場合も、native Linux のデスクトップ確認とは別です。Windows Explorer から Linux 実行体を直接開く方法ではありません。

生成・起動の確認済み OS/CPU と未確認条件は、その版の Release notes を参照してください。source起動や `--help` の成功だけを生成GUIの実画面確認とは扱いません。
