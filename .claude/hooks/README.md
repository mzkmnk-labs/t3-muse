# Skill 監査フック

`skills/t3-muse-starter/SKILL.md` の陳腐化を検知する、repo 内部の道具です。skill は `bridge/` や `tools/` の挙動を記述していますが、そちらの変更で skill も直されるとは限らず、静かに腐ります。

## 何をするか

現在のブランチと `origin/main` の merge-base からの変更集合（コミット済み、ステージ済み、未ステージの追跡対象変更）を計算します。対象領域が変更されているのに `SKILL.md` が変更集合に含まれない状態をドリフトとして報告します。

- 対象領域：`bridge/`、`tools/`、`installer/`、`docs/`、`README.md`、`README.ja.md`
- 対象外：`skills/`、`.claude/`（この監査自体と登録設定）、テスト（`test_*`、`*_test.*`、`tests/` 以下）

領域テーブルは `skill_audit.py` の `SKILL_SURFACE` の1か所にあります。

## いつ発火するか

Claude Code の規約どおり、スクリプトは `.claude/hooks/` に置き、`.claude/settings.json` から `$CLAUDE_PROJECT_DIR/.claude/hooks/skill_audit.py` として登録しています。Claude Code はフックをセッション開始時に読み込むため、追加や変更は次のセッションから効きます。

| イベント | 条件 | 動き |
| --- | --- | --- |
| PostToolUse（Bash） | コマンドに `git commit` か `gh pr create` を含む | ドリフトがあれば、ファイル一覧と更新の指示を Claude の文脈に追加する |
| Stop | `stop_hook_active` が false | ドリフトがあれば、ターンの終了を1回だけ止めて更新を促す |

どちらも助言であり、コミットや PR 作成そのものは止めません。想定外のエラーでは何も出さず exit 0 で抜けます。登録コマンド末尾の `|| true` も同じ目的です。スクリプトの無いブランチへ切り替えたセッションでは python が exit 2 を返し、そのままだと Stop がいつまでも止まり続けます。

T3 の claudeAgent セッションと `claude -p` で発火を確認しています。新しい worktree を対話的に開く場合は、先に Claude Code の信頼ダイアログが出ます。

## 手動で実行する

```sh
python3 .claude/hooks/skill_audit.py --check
```

exit 0 はドリフトなし、exit 1 はドリフトあり、exit 2 は使い方の誤りか、`origin/main` が無いなどで監査できなかった場合です。

## 知っておくこと

- `git fetch` はしません。手元の `origin/main` を基点にするので、古いと main に入った変更までブランチの変更として数えることがあります。
- コマンドの判定は文字列の部分一致です。`git -C <dir> commit` のような形は一致しないので、その場合は Stop で気づくことになります。
- 本当に skill に影響がない変更でも、そのブランチでは `SKILL.md` を変更しない限りドリフトのままです。Stop はターンごとに1回止めます。その場合は影響がない旨を PR 説明に書いて、そのまま終えてください。
