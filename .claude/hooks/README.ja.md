# Skill 監査フック

[English](README.md) | 日本語

Muse 用の3つの skill、`skills/t3-muse-starter/SKILL.md`（セットアップ）、`skills/t3-muse-pm/SKILL.md`（日常の運用）、`skills/t3-muse-bridge/SKILL.md`（ブリッジの使い方）が、記述しているドキュメントや実装と静かに食い違っていくのを防ぐための repo 内部の道具です。skill は bridge やツール、インストーラ、図、README、セットアップ手順の挙動を記述しています。しかし、そちらを変えても skill まで直されるとは限りません。対象の面が変わったのにどの skill も直っていない状態をドリフトと呼び、コミットと PR 作成の時点と、ターンの終わりに Claude へ知らせます。

## 何をするか

現在のブランチと `origin/main` の merge-base からの変更集合を計算します。コミット済み、ステージ済み、未ステージの、追跡対象の変更が含まれます。対象領域が変更されているのに、3つの `SKILL.md` のいずれも変更集合に含まれない状態を、ドリフトとして報告します。3つのうち少なくとも1つが変更されていれば、skill を更新したものとして扱います。対象領域は3つの skill で共有しているので、あるパスがどの skill に属するかは監査では判定せず、変更する人に任せます。

- 対象領域：`bridge/`、`tools/`、`installer/`、`docs/`、`README.md`、`README.ja.md`、`setup.md`、`setup.ja.md`
- 対象外：`skills/`、`.claude/`（この監査自体と登録設定）、テスト（`test_*`、`*_test.*`、`tests/` 以下）

領域テーブルは `skill_audit.py` の `SKILL_SURFACE` の1か所にあり、保護対象の3ファイルはその隣の `SKILL_PATHS` です。`skill.yaml` だけの変更は数えません。

## いつ発火するか

Claude Code の規約どおり、スクリプトは `.claude/hooks/` に置き、`.claude/settings.json` から `$CLAUDE_PROJECT_DIR/.claude/hooks/skill_audit.py` として登録しています。セッション中に設定ファイルを編集すると、Claude Code のファイル監視が拾ってそのまま反映します。反映されない場合はセッションを開き直してください。

| イベント | 絞り込み | 動き |
| --- | --- | --- |
| PostToolUse（Bash） | `if: "Bash(*git *commit*)"`、`--hook post-tool commit` | ドリフトがあれば、ファイル一覧とリポジトリの決まりを Claude の文脈に追加する |
| PostToolUse（Bash） | `if: "Bash(*gh pr create*)"`、`--hook post-tool pr-create` | 同上 |
| Stop | なし | `stop_hook_active` が false でドリフトがあれば、同じ内容を渡してターンを1回だけ続けさせる |

### コマンドの絞り込み

以前は全 Bash 呼び出しで python を起動し、スクリプトの中でコマンド文字列の部分一致を見ていました。いまは handler の `if` で、Claude Code が python を起動する前に絞り込みます。`if` には1つの handler に1つのルールしか書けないので、コミット用と PR 作成用に handler を2つ置いています。

- パターンの先頭の `*` は、前に付くラッパーのためです。実測では、`Bash(gh pr create *)` は `wrapper gh pr create` のようにラッパーを前に付けた形に一致しませんでした。`Bash(git commit *)` も `timeout 30 git commit` や `nohup git commit` に一致しませんでした。ドキュメントにある permission rule のラッパー除去は、`if` では効きません。
- `*git *commit*` は、旧実装の部分一致が取りこぼしていた `git -C <dir> commit`、`git -c k=v commit`、`git --no-pager commit` も拾います。`/usr/bin/git commit`、`sh -c 'git commit ...'`、`nohup git commit` も一致します。どれも 2.1.319 で実測しました。
- スクリプトも、同じパターンでコマンド全文を確かめます。ドキュメントのとおり `if` は best-effort で、`$()` やバッククォートを含むコマンドでは、プログラム名より多くを指定したパターンの handler がすべて起動します。`$PWD` のような変数を含むコマンドでも起動することがあります。Claude の標準的なコミット `git commit -m "$(cat <<'EOF' ... )"` もこれに当たります。各 handler は自分のファミリー名を渡し、スクリプトはそのファミリーのパターンだけを見ます。そのため、1回のコミットで出る監査は1回です。
- パターン表はスクリプトの `AUDITED_COMMANDS` にあります。`settings.json` の `if` と一致していることはテストで確かめています。ファミリー名を省いた `--hook post-tool` は、全ファミリーを見ます。

Claude Code 2.1.319 で実測した挙動です。

| コマンド | 起動した handler | 出力 |
| --- | --- | --- |
| `git add -A && git commit -m "$(cat <<'MSG' ... )"` | commit | 監査1回 |
| `git -C "$PWD" commit ...` | commit、pr-create | commit 側だけが監査を出す |
| `gh pr create --fill`、`wrapper gh pr create --fill` | pr-create | 監査1回 |
| `echo $(date)` | commit、pr-create | どちらも無言 |
| `echo "$HOME"`、`git status`、`ls` | なし | なし |
| `git status; echo commit` | なし（サブコマンド単位で判定される） | なし |

### 埋まらない穴

次の場合は PostToolUse では気づけず、Stop で気づくことになります。

- git の alias（`git ci` など）や `gh api` による PR 作成、`make` などの別プログラムの中で行われるコミット。
- 失敗した Bash 呼び出し。PostToolUse は成功したツール呼び出しにしか発火せず、失敗は PostToolUseFailure になります。`git commit ... && git push` で push が失敗すると、コミットは済んでいても監査は出ません。
- 逆に `git log --grep commit` や `echo "git commit"` のような無関係なコマンドでも監査が走ることがあります。ドリフトが実際にあるときに事実を伝えるだけで、何も止めません。
- 1つのコマンドにコミットと PR 作成の両方があると、メッセージは2回出ます。

## 助言型と fail-open

- どちらのイベントも助言だけです。PostToolUse はツールの実行後に走るので、コミットや PR 作成そのものは止めません。
- Stop は `decision: "block"` ではなく `hookSpecificOutput.additionalContext` を返します。ドキュメントは、設計どおりに動いて助言を返すフックにこちらを勧めています。ターンを続ける点とループ防止は `block` と同じです。hook error ではなく、hook の追加コンテキストとして Claude に届きます（T3 のセッションでは「Stop hook additional context」と表示され、トランスクリプトの `hookErrors` は空でした）。続いたターンが終わるときの Stop には `stop_hook_active: true` が付くので、そこでは黙ります。ターンを続けるのは1ターンにつき1回です。
- 想定外のエラーでは何も出さず、exit 0 で抜けます。不正な `--hook` 引数も同じ扱いです。
- 登録コマンド末尾の `|| true` も同じ目的です。スクリプトの無いブランチへ切り替えると、python は exit 2 を返します。PostToolUse では exit 2 の stderr が Claude に見えてしまいます。Stop でも、現行の Claude Code は stdout が空で stderr がファイル不在を示す exit 2 を止めないエラーとして扱いますが、`|| true` で両方とも exit 0 にそろえています。
- `|| true` のためにシェル形式（`args` なし）で登録しています。パスは `"$CLAUDE_PROJECT_DIR/..."` と引用符で囲んでいます。ファミリー名を引数にするのは、シェル形式のコマンドに `*` を書くと sh が展開してしまうからです。
- `timeout` は 60 秒です。git の呼び出しは1回あたり最大 10 秒で、最大 3 回です。既定の 600 秒より短くし、万一固まってもターンの終わりを長く待たせません。タイムアウトした出力は捨てられ、何も起きません。
- `onFailure` は付けていません。既定で処理は続行され、Stop には効かない設定だからです。

## 必要な Claude Code

`if` は 2.1.85 から、Stop の `additionalContext` は 2.1.163 から使えます。`if` の判定はその後の版でも何度か直されているので（2.1.163、2.1.243 など）、この README の表は 2.1.319 での実測として読んでください。

T3 の claudeAgent ランタイムと `claude -p` で発火を確認しています。新しい worktree を対話的に開く場合は、先に Claude Code の信頼ダイアログが出ます。

## 手動で実行する

```sh
python3 .claude/hooks/skill_audit.py --check
```

exit 0 はドリフトなし、exit 1 はドリフトありです。exit 2 は、使い方の誤りか、`origin/main` が無いなどで監査できなかった場合です。

CI でも、PR では Ubuntu ジョブの最後のステップで同じチェックを実行します。こちらも助言だけで、ドリフト（exit 1）や監査できなかった場合（exit 2）は警告の注釈として出し、ジョブは失敗させません。

## 知っておくこと

- `git fetch` はしません。手元の `origin/main` を基点にするので、古いと main に入った変更までブランチの変更として数えることがあります。
- 監査するのは、スクリプトが置かれたチェックアウト、つまりセッションを始めたプロジェクトです。セッションの途中で worktree に入っても `$CLAUDE_PROJECT_DIR` は元の場所を指すので、監査対象は移りません。T3 はセッションを worktree の中で始めるので、この差は出ません。
- 本当にどの skill にも影響がない変更でも、そのブランチでは3つの `SKILL.md` のいずれかを変更しない限りドリフトのままです。Stop はターンごとに1回続けさせます。その場合は影響がない旨を PR 説明に書いて、そのまま終えてください。
