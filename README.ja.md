# t3-muse

[English](README.md) | 日本語

T3 Code を Meta Muse から扱うためのコンパニオン。

## はじめ方

自分の Muse に、次の一文を送る:

```text
https://raw.githubusercontent.com/mzkmnk-labs/t3-muse/main/setup.ja.md を読んで、t3-muse をセットアップして。
```

[setup.ja.md](setup.ja.md) は Muse が読むためのセットアップ指示書である。Muse はこれを読んでセットアップを進め、人の操作が必要な場面ではそのつど手を止めてチャットで伝える。送る前に、Mac で T3 Code が動いていて、Node.js 24 以上が入っていることを確かめておく。

次の操作は本人にしかできない。Muse はその場面になると、それぞれを頼んでくる:

- **インストーラーの実行。** Muse に示されたコマンド `npx @mzkmnk-labs/t3-muse install` を、Mac のターミナルで実行する。初回のインストールでは、t3a トークンを発行するか尋ねられる。このトークンは、T3 Code の承認待ち・質問待ちへ自分の回答を中継するためのもので、答えるのは本人である。
- **MCP URL のコピー。** T3 Code の環境設定で T3 Connect を有効にし、T3 Connect 経由で接続しているクライアント (Web アプリなど) から環境の MCP URL をコピーして Muse に渡す。ホストの Mac 上でコピーした URL は LAN アドレスになることがあり、Muse からは届かない。
- **サインインの承認。** Muse がコネクタの OAuth サインインを始めたら、T3 Code のアプリでそれを承認し、アクセスモードを選ぶ。コネクタ経由で Muse ができることは、すべてこのモードが上限になる。

Muse がこれらを代わりに行うことはなく、承認や T3 Code の質問への回答を自分で決めることもない。インストーラーを自分で実行するときは、[手動セットアップ](#手動セットアップ) を参照。

## 構成図

![構成図。ユーザーはチャットで Muse と対話し、Muse は PM として 2 つの経路でタスクを T3 Code に dispatch する。経路1 (操作系・既定): Muse はカスタムコネクタ custom.t3code を通って T3 Connect リレー (HTTPS、OAuth 認可コード＋動的登録、30 日セッション、モード上限あり) に接続し、リレーは環境の公式 MCP サーバー (/mcp) を経由して T3 Code サーバーを操作する。この経路でスレッドの起動・送信・読み取り・一覧を行う。watcher はブリッジでの読み取りが失敗したとき、このスレッド一覧にフォールバックする。経路2 (ファイルメールボックス・ブリッジ): Muse がペアデバイス経由で Mac Studio 上のファイルメールボックスにリクエスト JSON を書き、メールボックスが Muse に結果 JSON を返す。bridge.py は inbox をポーリングし、t3r.py、t3w.py、t3a.py に対して許可したコマンドだけ実行する。t3r.py は MCP にない DB 参照を担い、T3 Code 状態 DB を読み取り専用で開く。watcher は 2 分ごとにメールボックス経由で t3r.py の status を取得する。t3a.py はユーザーがチャットで決めた回答だけを、t3 CLI で発行・更新するトークンを使ってループバックの WebSocket で T3 Code の承認待ち・質問待ちへ中継する。承認には --user-approved が必須で、MCP の t3_pending_request は質問への回答のみで承認には答えられないため、この経路を使う。t3w.py は MCP が使えないときのフォールバックで、t3 CLI を通じてスレッド作成・送信を行い、CLI はローカルセッションで T3 Code につながる。T3 Code は DB を読み書きする。別に、メールボックスの restart-trigger に置いたファイルで launchd が番人の restart-bridge.sh を起動し、ブリッジのジョブだけを再起動する。](docs/architecture.ja.svg)

ユーザーは製品である Muse と対話し、Muse が PM としてタスクを T3 Code に dispatch する。経路は 2 つある。

**経路1: 操作系 (既定)。** Muse → カスタムコネクタ `custom.t3code` → T3 Connect リレー → 環境の公式 MCP サーバー (`/mcp`) → T3 Code サーバー。リレーは HTTPS で、OAuth 認可コード＋動的登録、30 日セッション、モード上限がある。スレッドの起動・送信・読み取り・一覧はこの経路で行う。watcher は MCP のスレッド一覧をフォールバックとしてだけ使う (後述の「完了の監視」)。

**経路2: ファイルメールボックス・ブリッジ。** Muse → ペアデバイス経由のファイルメールボックス `~/t3-muse-bridge` → ブリッジデーモン (launchd 常駐、許可リスト制) → 次のツールを実行する:

- `t3r.py`: `statev2.sqlite` を読み取り専用で参照する。watcher はこの `status` をポーリングする
- `t3a.py`: 承認・質問への回答を中継する。中継するのはユーザーがチャットで決めた内容だけで、承認には `--user-approved` が必須
- `t3w.py`: MCP が使えないときのフォールバック

番人の `restart-bridge.sh` は、`restart-trigger` にファイルが置かれるとデーモンを再起動する。

**役割分担の理由。** 公式 MCP の `t3_pending_request` は質問への回答のみで、権限承認には答えられない。そのため承認は `t3a.py` が担う。DB の直接参照は MCP にないため `t3r.py` が担う。

**完了の監視。** dispatch した作業が進行中のあいだ、Muse は watcher の cron を 2 分ごとに動かす。各回はブリッジ経由で `t3r status` を取得して watcher の状態ファイルと比較し、新しい完了、新しい失敗、新しく発生した承認待ち・質問待ちだけを報告してから、見た内容を保存する。watcher は純粋なポーリングで、`t3_thread_wait` を保持しない。

- watcher を生かし続けるのは Muse 自身で、チャットの外に常駐の監視役は置かない。dispatch、follow-up の送信、T3 の状況報告の前に、メインのチャットが watcher が有効か確かめ、無効なら再び有効にして、状態ファイルにまだ載っていないか終了済みの対象スレッドを、実行中・未報告として書き込む。起動直後はまだ run が記録されておらず、status が idle を返すことがある。この印で、watcher がそのスレッドを idle と取り違えないようにする
- 終了を報告済みのスレッドが、follow-up などで再び実行されたら、新しいインカーネーションとして扱う。watcher は報告済みの印を外し、次の完了、失敗、承認待ち・質問待ちを通常どおり報告する
- 完了したスレッドについては、status の `branch` を使い、そのブランチの PR を GitHub で引く
- ブリッジでの読み取りが失敗したとき、または status の source が `legacy` のときは、MCP のスレッド一覧にフォールバックする。両方とも失敗したときは何も報告せず、一時的なエラーを誤報にしない
- 何も実行中でなく、何も報告しなかった回を idle と数える。idle が 15 回 (約 30 分) 連続すると watcher は自動で無効になり、Muse の次の確認で再び有効になる

要点:

- メールボックス経路では、Mac から Muse へ能動的に接続する手段はない。Muse がペアデバイス経由でファイルを置きに・取りに行く
- ブリッジはネットワークを待ち受けず、許可リスト (`bridge-ping`、`t3r.py` の読み取り系、`t3w.py` のタスク依頼系、`t3a.py` の回答中継系) 以外は実行しない
- DB への直接アクセスは読み取り専用のみ。T3 への作業依頼は公式 MCP が既定で、MCP が使えないときだけ T3 純正 CLI 経由 (`t3w.py`) を使う。どちらもユーザーの依頼に基づく場合に限る。権限承認への回答は `t3a.py` だけ。T3 の承認待ち・質問待ちへの回答は、ユーザーが決めた場合に限る
- ブリッジは自分自身を再起動できないため、別の launchd ジョブが担当する。`~/t3-muse-bridge/restart-trigger` にファイルを置くと `restart-bridge.sh` が動き、ブリッジのジョブだけを再起動する。ブリッジ本体に `launchctl` の権限は増えない

開発フロー: 変更はブランチ → PR → CI (GitHub Actions) → squash マージ。
`.github/workflows/` のファイルだけは Muse の GitHub App の権限外のため、専用 PAT 経由で push する。

## 現在の構成

| パス | 内容 |
|---|---|
| `bridge/` | Mac 常駐ブリッジ (経路2)。ペアデバイス経由のファイルメールボックスで、許可リストのコマンドだけを実行する (設計は `bridge/README.md`) |
| `tools/` | Mac 側の自前ツール群で、ブリッジから実行する (経路2)。`t3r.py` は T3 Code の状態 DB を読み取り専用で引く最小リーダー (`schema` / `health` / `status`)。`t3w.py` は MCP が使えないときに T3 純正 `t3 thread` CLI 経由でタスク依頼を行うライター (`start` / `send` / `show` / `list` / `add-project`)。`t3a.py` は T3 の承認待ち・質問待ちへのユーザーの回答を中継する (`issue-token` / `renew` / `pending` / `answer`) |
| `installer/`、`package.json` | npm パッケージ `@mzkmnk-labs/t3-muse`。ブリッジとツールを Mac に導入する、依存パッケージなしの Node CLI (`install` / `status` / `uninstall`) |
| `skills/t3-muse-starter/` | t3-muse をまっさらな状態からセットアップするための Muse 用カスタム skill。ブリッジ、MCP コネクタ、動作確認を扱う。[setup.ja.md](setup.ja.md) で Muse が最初に配置し、最後の手順で PM skill を配置する |
| `skills/t3-muse-pm/` | t3-muse を日々運用するための Muse 用カスタム skill。Muse が PM、T3 Code が実装を担当し、dispatch、承認・質問の中継、監視、マージ、保守を扱う |

## 手動セットアップ

セットアップ中は、その場面になると Muse がインストーラーの実行を頼んでくる。この節は、インストーラーを自分で実行するためのものである。Muse の案内なしに Mac 側を導入・更新するとき、または導入の状態を確かめる・取り除くときに使う。インストーラーが扱うのは Mac 側、つまりブリッジとツールだけである。MCP の接続と skill の配置は引き続き Muse で行うので、導入した後に [はじめ方](#はじめ方) の一文を Muse に送る。

```sh
npx @mzkmnk-labs/t3-muse install            # ファイルの配置、2 つの LaunchAgent の load、動作確認
npx @mzkmnk-labs/t3-muse status             # 状態の報告。トークンの中身は出さない
npx @mzkmnk-labs/t3-muse uninstall          # unload と plist の削除。データは残す
npx @mzkmnk-labs/t3-muse uninstall --purge  # 確認のうえ、2 つのデータフォルダも削除
```

`npm install` の時点では何も動かず、変更はこれらのサブコマンドからだけ起きる。`install` が成功するのは、メールボックス経由の `bridge-ping`、`t3r health`、`t3a pending` がすべて通ったときだけなので、T3 Code が起動していて、承認用トークンがあることが前提になる。

新しいマシンにはトークンファイルがまだない。ターミナルで実行すると、`install` は `No t3a token found. Issue one now? [y/N]` と尋ねる。`y` のときだけメールボックス経由の `t3a.py issue-token` で発行し、`t3a pending` をやり直す。発行した内容 (スコープと 90 日の有効期間)、ファイルの場所、失効の手順を出力するが、トークンの値は出力しない。

それ以外の答えでは何も変わらない。CI やスクリプトなど、標準入力がターミナルでない実行でも何も変わらず、尋ねることもしない。どちらも `install` は終了コード 1 で終わるので、自分でトークンを発行して、もう一度 `install` を実行する:

```sh
python3 ~/t3-muse-tools/t3a.py issue-token
```

トークンが期限切れや失効の場合、`install` は `TOKEN_REJECTED` で止まり、トークンには触れない。既存の資格情報の差し替えは確認 1 回より重い操作なので、尋ねることもしない。`t3a.py renew` で置き換える。

`install` の再実行は安全で、更新もこれで行う。配布ファイルは置き換えるが、トークンと監査用の履歴には触れない。inbox にリクエストが残っている間は、再起動で二重に実行されるため何も変更しない。インストーラーを使わずにファイルを配置する手順は `bridge/README.md` にある。

## 承認と質問への回答

T3 Code の公開 MCP / CLI ではランタイムリクエストに回答できない。そこで `t3a.py` は、アプリの UI 自身が使う経路であるローカルサーバーのオーケストレーション WebSocket API を使う。セッショントークンの権限は `orchestration:read` と `orchestration:operate` に限る。

```sh
python3 t3a.py issue-token                        # 最初に一度だけ。出力はメタデータのみ
python3 t3a.py renew                              # トークンを置き換える。出力はメタデータのみ
python3 t3a.py pending                            # 人の回答を待っているリクエストをすべて表示
python3 t3a.py answer <request-id> approve --user-approved
python3 t3a.py answer <request-id> deny --user-approved
python3 t3a.py answer <request-id> "青い方を使って"
```

結果は stdout に JSON で出る。エラーは固定のコードを含む JSON 1 行を stderr に出す (`NO_TOKEN`、`T3_NOT_RUNNING`、`USER_APPROVAL_REQUIRED` など。一覧は `tools/t3a.py` の冒頭にある)。使い方の誤りは終了コード 2。

**`--user-approved` のルール。** 承認への回答は `approve` か `deny` そのもので、かつ `--user-approved` が必須。満たさなければブリッジとツールの両方が拒否する。このフラグは人が決めたことを記録するもので、Muse が自分の判断で付けてはならない。これは証明ではなく記録された約束である。ツールには誰が決めたかを確認できないので、リクエストは監査用にブリッジの `done/` に残る。`approve` が許可するのはそのリクエスト 1 件だけ (T3 の `accept`) で、セッション全体や恒久の許可にはならない。

**制限。** 複数の質問を持つリクエストは 1 つのテキストでは答えられないため `MULTI_QUESTION` で拒否する。アプリで回答すること。複数選択の質問では、`answer` で選べる選択肢は 1 つだけ。回答できるのは `pending` で `answerable: true` のものに限る。

**トークンの期限と更新。** トークンの有効期限は 90 日で、切れると T3 Code が拒否する (`TOKEN_REJECTED`)。`renew` はブリッジ経由でトークンを置き換える。期限が切れた後でも使える。T3 Code が起動している必要がある。

`renew` は新しいトークンをトークンファイルと同じフォルダの 0600 の一時ファイルへ発行し、そのトークンで pending 一覧を取得してから rename でトークンファイルを置き換え、最後に旧セッションを失効させる。置き換え前に失敗すれば、トークンファイルは元のまま残る。置き換えが済めば終了コードは 0 で、新しい `expires_at`、古いセッションが 1 つ見つかったときは `previous_expires_at`、そして次のいずれかを出力する:

| `old_session` | 意味 |
| --- | --- |
| `revoked` | 失効済みで、改めて取得した `t3 auth session list` からも消えている |
| `none_active` | 有効な古い `t3-muse` セッションがなく、失効させるものがなかった |
| `left_active` | 何も失効させていない。理由と手動の手順は `note` にある |

失効させるのは、`subject` と `client.label` がともに `t3-muse` で、新しいトークンの発行前から一覧にあり、新しいセッションがちょうど 1 つ増え、古いものがちょうど 1 つ残っている場合だけ。旧トークンからセッションを読み取れるときは、それとも一致しなければならない。一覧から新しいセッションを特定できないとき、`expires_at` は null になる。

`issue-token` は既存のファイルを上書きしない。`renew` を実行できないときやトークンが不要になったときは、手動で失効させる:

```sh
~/.t3/bin/t3 auth session list                    # t3-muse のセッション ID を確認
~/.t3/bin/t3 auth session revoke <session-id>
rm ~/t3-muse-tools/.t3a-token
python3 t3a.py issue-token                        # 再発行するときだけ
```

`left_active` の後は最初の 2 行だけを実行し、最後に発行された `t3-muse` セッションは残す。これが新しいトークンのセッションである。

**セキュリティ。**

- トークンは Mac の外に出ない。`t3a.py` はループバックアドレスにだけ接続し、`server-runtime.json` のホストがローカルでなければ拒否する (`T3_NOT_LOCAL`)
- トークンは表示されない。CLI の stdout は 0600 のファイルへ直接流し、CLI の stderr は捨てる。送信は `Authorization` ヘッダーだけで、コマンドラインや URL には載せない。メールボックスとその `done/` にも残らない
- Muse はユーザーが決めた内容を中継するだけで、自分の判断では何も承認しない
- トークンは実際の権限を持つ。2 つのスコープで許される操作 (スレッドの開始を含む) はすべて可能なので、ファイルがコピーされた恐れがあれば失効させること

## 開発の進め方

- まずブリッジを Mac Studio で動かし、疎通 (bridge-ping) を確認する
- 次に読み取り専用の最小経路を通す
- スキルは必要になったものから、実動作に合わせて1つずつ書く
