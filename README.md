# Coding AI Usage Visualizer

Copilot CLI / VS Code Copilot Chat のローカルログと使用履歴DBから、使用量を集計するツールです。CLI、デスクトップGUI、オフラインHTML、JSON、Markdownで確認できます。

製品名は **Coding AI Usage Visualizer** ですが、現在の解析対象は **GitHub Copilotのみ** です。Python package名（`copilot_usage_visualizer`）とコマンド名（`copilot-usage`、`copilot-usage-gui`）は従来のままです。GitHubまたはMicrosoftの公式製品ではなく、両社による承認・保証・サポートを受けていません。

## 前提環境

- Pythonの導入条件：3.11以上。
- 今回の導入・CLI/HTML検証対象：Apple M1（arm64）、macOS Tahoe 26.6.2、Python 3.14.7。
- 他のOS・CPU・Python版は未検証です。Pythonの導入条件は、すべての組合せでの動作保証を意味しません。
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

Releaseのソースarchiveを使う場合は、展開したディレクトリでvenv作成から実行してください。版を固定して取得したい場合は、clone後に公開済みtag（例：`v0.2.0`）へ切り替えてからinstallします。

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
WSLの読み取りは公開前の検証対象（前提環境のmacOS）に含まれません。

既定の解析DBは、**コマンドを実行したディレクトリを基準とする** `output/copilot-usage.db` です。インストール先基準ではありません。`--db` で変更できます。出力DBは本ツール専用の新しいファイルにしてください。

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
scanは同期処理のため、多数・低速のrootでは画面が操作待ちになる時間が延びる場合があります。

「設定・アクション」タブの **設定を保存** で、入力・解析DB・レポート出力先・期間・換算レート・表示倍率（UI scale）・文字の大きさ（Font size）を保存し、次回の起動で復元します。
scan・表示更新・HTML export・終了では自動保存しません。起動時は項目ごとに、明示した起動引数、保存した設定、既定値の順で使います。
保存先は画面の「GUI 設定」欄（Settings file）に表示します。

| OS | 設定ファイル |
| --- | --- |
| macOS | `~/Library/Application Support/CopilotUsageVisualizer/gui-settings.json` |
| Windows | `%LOCALAPPDATA%\CopilotUsageVisualizer\gui-settings.json` |
| Linux | `$XDG_CONFIG_HOME/CopilotUsageVisualizer/gui-settings.json`（未設定などの場合は `~/.config/` 配下） |

設定ファイルには、入力・出力の絶対パスが平文で入ります。使用量データやログ本文は入りません。設定ファイルを共有しないでください。
保存した期間は月が変わっても維持します。相対パスは保存時の作業ディレクトリを基準に絶対パスへ変換します。

表示倍率と文字の大きさ（100 / 115 / 125 / 150%）は、保存後の次回起動から反映します。表示倍率の「自動」は、WindowsではOSの表示倍率を使い、macOS・Linuxでは100%です。WSLgでは100〜250%から選んで保存してください。
大きくした結果、最小のウィンドウが画面に収まらない場合は、どちらかを下げてください。

期間はYYYY-MM-DDの直接入力のほか、日付の選択と「先月」「今月」「今日」で変更できます。集計への反映は表示更新で行います。

「セッション使用量」タブは、scanせずにCopilot CLIの記録を直接読み、一つのセッションのAI Credits・token・コンテキストの推移、エージェント別の内訳、監査詳細を表示します。
CLIログ欄のrootの親（例：`~/.copilot`）にある使用履歴DB・セッションのイベントファイル・GitHub Copilotアプリの `data.db`（存在する場合）を読み取り専用で開き、読んだ内容を保存しません。
自動更新（Auto refresh）は既定で無効です。WindowsからWSLの共有パスにある記録は、このタブでは読みません。解析DBや統合DBの内容の確認には使えません。

## 集計の意味と既知の制約

- 対象は2026-08-01以降の記録です。それより前の履歴の互換取り込みは提供しません。
- Creditsはログ・使用履歴にある直接値を使います。欠落は0や推定額ではなく不明として扱い、小計と欠落件数を表示します。
- CLIは使用履歴と累積イベントを照合します。確定できた部分はrequestのモデル・UTC利用日、残りは採用した累積イベントを使います。日別・月別集計がGitHubの請求期間と一致する保証はありません。
- 終了していないCLIセッションなどでは、通常scanで後着情報が十分に反映されない場合があります。回避する場合は、次節の注意に従って全体読み直しを行います。
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
