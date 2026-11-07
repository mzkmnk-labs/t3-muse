# t3-muse

[English](README.md) | 日本語

T3 Code を Meta Muse から扱うためのコンパニオン。必要なものを、
実際に要るものから1つずつ自前で実装していくリポジトリ。

## 構成図

![構成図。Mac Studio の外では、ユーザーが Muse に依頼・承認を送り、Muse がデバイスチャネルでファイルメールボックスにリクエスト JSON を書く。Mac Studio の中では、メールボックスが Muse に結果 JSON を返す。bridge.py は inbox をポーリングし、t3r.py、t3w.py、t3a.py に対して許可したコマンドだけ実行する。t3r.py は T3 Code 状態 DB を読み取り専用で開く。t3w.py は t3 CLI を通じてスレッド作成・送信を行い、CLI はローカルセッションで T3 Code につながる。t3a.py は T3 Code の承認待ち・質問待ちへのユーザーの回答を、t3 CLI で一度だけ発行したトークンを使ってループバックの WebSocket で中継する。T3 Code は DB を読み書きする。既存 t3-tots (Grok 用) から DB への線は対象外 (触らない)。](docs/architecture.svg)

要点:

- Mac から Muse へ能動的に接続する経路はない。Muse がデバイスチャネル経由でファイルを置きに・取りに行く (メールボックス方式)
- ブリッジはネットワークを待ち受けず、許可リスト (`bridge-ping`、`t3r.py` の読み取り系、`t3w.py` のタスク依頼系、`t3a.py` の回答中継系) 以外は実行しない
- DB への直接アクセスは読み取り専用のみ。T3 への作業依頼は T3 純正 CLI 経由 (`t3w.py`) だけで、ユーザーの依頼に基づく場合に限る。T3 の承認待ち・質問待ちへの回答は `t3a.py` だけで、ユーザーが決めた場合に限る
- 既存の Grok 用 t3-tots 環境 (`~/t3-tots`) は同じ Mac で稼働しているが、本プロジェクトの対象外

開発フロー: 変更はブランチ → PR → CI (GitHub Actions) → squash マージ。
`.github/workflows/` のファイルだけは Muse の GitHub App の権限外のため、専用 PAT 経由で push する。

## 現在の構成

| パス | 内容 |
|---|---|
| `bridge/` | Mac 常駐ブリッジ。Muse のデバイスチャネル経由のファイルメールボックスで、許可リストのコマンドだけを実行する (設計は `bridge/README.md`) |
| `tools/` | Mac 側の自前ツール群。`t3r.py` は T3 Code の状態 DB を読み取り専用で引く最小リーダー (`schema` / `health` / `status`)。`t3w.py` は T3 純正 `t3 thread` CLI 経由でタスク依頼を行うライター (`start` / `send` / `show` / `list` / `add-project`)。`t3a.py` は T3 の承認待ち・質問待ちへのユーザーの回答を中継する (`issue-token` / `pending` / `answer`) |

## 承認と質問への回答

T3 Code の公開 MCP / CLI ではランタイムリクエストに回答できない。そこで `t3a.py` は、アプリの UI 自身が使う経路であるローカルサーバーのオーケストレーション WebSocket API を使う。セッショントークンの権限は `orchestration:read` と `orchestration:operate` に限る。

```sh
python3 t3a.py issue-token                        # 最初に一度だけ。出力はメタデータのみ
python3 t3a.py pending                            # 人の回答を待っているリクエストをすべて表示
python3 t3a.py answer <request-id> approve --user-approved
python3 t3a.py answer <request-id> deny --user-approved
python3 t3a.py answer <request-id> "青い方を使って"
```

結果は stdout に JSON で出る。エラーは固定のコードを含む JSON 1 行を stderr に出す (`NO_TOKEN`、`T3_NOT_RUNNING`、`USER_APPROVAL_REQUIRED` など。一覧は `tools/t3a.py` の冒頭にある)。使い方の誤りは終了コード 2。

**`--user-approved` のルール。** 承認への回答は `approve` か `deny` そのもので、かつ `--user-approved` が必須。満たさなければブリッジとツールの両方が拒否する。このフラグは人が決めたことを記録するもので、Muse が自分の判断で付けてはならない。これは証明ではなく記録された約束である。ツールには誰が決めたかを確認できないので、リクエストは監査用にブリッジの `done/` に残る。`approve` が許可するのはそのリクエスト 1 件だけ (T3 の `accept`) で、セッション全体や恒久の許可にはならない。

**制限。** 複数の質問を持つリクエストは 1 つのテキストでは答えられないため `MULTI_QUESTION` で拒否する。アプリで回答すること。複数選択の質問では、`answer` で選べる選択肢は 1 つだけ。回答できるのは `pending` で `answerable: true` のものに限る。

**トークンの期限と再発行。** トークンの有効期限は 90 日で、切れると T3 Code が拒否する (`TOKEN_REJECTED`)。`issue-token` は既存のファイルを上書きしない。再発行・失効の手順:

```sh
~/.t3/bin/t3 auth session list                    # t3-muse のセッション ID を確認
~/.t3/bin/t3 auth session revoke <session-id>
rm ~/t3-muse-tools/.t3a-token
python3 t3a.py issue-token                        # 再発行するときだけ
```

**セキュリティ。**

- トークンは Mac の外に出ない。`t3a.py` はループバックアドレスにだけ接続し、`server-runtime.json` のホストがローカルでなければ拒否する (`T3_NOT_LOCAL`)
- トークンは表示されない。CLI の stdout は 0600 のファイルへ直接流し、CLI の stderr は捨てる。送信は `Authorization` ヘッダーだけで、コマンドラインや URL には載せない。メールボックスとその `done/` にも残らない
- Muse はユーザーが決めた内容を中継するだけで、自分の判断では何も承認しない
- トークンは実際の権限を持つ。2 つのスコープで許される操作 (スレッドの開始を含む) はすべて可能なので、ファイルがコピーされた恐れがあれば失効させること

## 開発の進め方

- まずブリッジを Mac Studio で動かし、疎通 (bridge-ping) を確認する
- 次に読み取り専用の最小経路を通す
- スキルは必要になったものから、実動作に合わせて1つずつ書く
