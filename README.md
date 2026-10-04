# Copilot Usage Visualizer

Copilot CLI / VS Code Copilot Chat のローカルログと使用履歴DBから、使用量を集計するツールです。CLI、デスクトップGUI、オフラインHTML、JSON、Markdownで確認できます。

公開repository名は **CodingAIUsageVisualizer** ですが、現在の対象は **GitHub Copilotのみ** です。製品名・Python package名・コマンド名はCopilot Usage Visualizerのままです。GitHubまたはMicrosoftの公式製品ではなく、両社による承認・保証・サポートを受けていません。

## 前提環境

- Pythonの導入条件：3.11以上。
- 今回の導入・CLI/HTML検証対象：Apple M1（arm64）、macOS Tahoe 26.6.2、Python 3.14.7。
- 他のOS・CPU・Python版は未検証です。Pythonの導入条件は、すべての組合せでの動作保証を意味しません。
- GUIにはデスクトップの表示環境が必要です。公開候補のGUI実画面確認は公開前に行い、Release notesに結果を記載します。
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

Releaseのソースarchiveを使う場合は、展開したディレクトリでvenv作成から実行してください。版を固定して取得したい場合は、clone後に公開済みtag（初回は `v0.1.0`）へ切り替えてからinstallします。

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

GUIは次で起動します。

```sh
copilot-usage-gui
```

`copilot-usage gui` でも起動できます。設定欄で入力・解析DB・レポート出力先・期間・換算レートを確認してから、scan、表示更新、HTML exportを行います。既定の期間は起動月、換算レートは160です。GUIの設定が別の入力を指していないか、全体読み直し前にも確認してください。

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

本ツールはローカルで解析し、解析結果を外部サービスへ送る機能を持ちません。入力ログ本文は読み取り時に処理しますが、raw prompt・response・tool output・元の絶対パスを解析DBやレポートに保存しない設計です。パスの代わりにhashと表示用aliasを使います。

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
