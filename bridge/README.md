# T3 Bridge (Muse 専用ブリッジ)

SSH over Tailscale の代替として作る、Mac Studio 常駐の実行ブリッジ。

## なぜこの方式か

- Muse の Tailscale トンネルは登録こそ Connected になるが、実通信（データプレーン）が確立しなかった（2026-11-07 に切り分け済み）
- 一方で Muse の Mac デバイスチャネル（ファイル読み書き）は動作が確認できている
- そこで「リクエストをファイルで渡し、結果をファイルで返す」メールボックス方式にした。ブリッジはネットワークを一切 listen しない

## 構成

```
Muse ──(デバイスチャネル: ファイル書込)──▶ ~/t3-tots-bridge/inbox/<id>.json
Mac の bridge.py (launchd 常駐) が検知 → 許可コマンドだけ実行
Muse ──(デバイスチャネル: ファイル読取)──◀ ~/t3-tots-bridge/outbox/<id>.json
処理済みリクエストは done/ に保管 (監査用)
```

## セキュリティ

- 実行できるのは許可リストのみ：組み込み `bridge-ping`、自前リーダー `t3r.py` の読み取り専用サブコマンド（`schema` / `health` / `status`）、自前ライター `t3w.py`（`start` / `send` / `show` / `list` / `add-project`）、回答中継 `t3a.py`（`issue-token` / `pending` / `answer`）。旧来の外部ツールは許可リストに無い
- `t3w.py` が唯一の書き込み経路。T3 純正 CLI 経由でスレッド作成とメッセージ送信を行うもので、ブリッジ側で引数の形（スレッド ID の形式、フラグ混入、文字数）を検証する。実行されるのはユーザーが Muse に依頼したリクエストだけ
- `t3a.py` は T3 の承認待ち・質問待ちへの回答を中継する。ブリッジ側で引数の形を検証する：`issue-token` と `pending` は引数なし。`answer` はリクエスト ID の形式に加え、`approve` / `deny` なら末尾の `--user-approved` が必須（無ければブリッジでもツールでも拒否）。それ以外の回答テキストは空でなく 4000 文字以内で、`-` で始まらず、`--user-approved` を付けない
- `--user-approved` は人が決めたことの記録であり、Muse が自分の判断で付けてはならない。証明ではなく約束なので、監査は done/ に残るリクエストで行う
- `t3a.py` のトークンは Mac の外に出ず、表示もされない。保存先はツールフォルダの 0600 のファイル `.t3a-token` だけで、CLI の stdout をファイルへ直接流すため、ブリッジの出力にも outbox / done にも現れない。接続先はループバックのみ
- Muse はユーザーが決めた内容を中継するだけで、自分の判断では何も承認しない
- 環境変数の許可はエントリごと：`t3a.py` には `T3M_T3CLI` と `T3M_T3_HOME` だけ、`t3r.py` / `t3w.py` には従来どおり `T3M_DB` と `T3M_T3CLI` だけを渡す
- 任意のシェルコマンドは実行できない。SSH のようなフルシェル権限にはならない
- ネットワークの待ち受けポートなし。入力はローカルフォルダのファイルだけ
- リクエスト/結果はすべて done/ と outbox/ に残るので後から監査できる
- 止め方: `launchctl unload ~/Library/LaunchAgents/com.muse.t3bridge.plist`、またはフォルダごと削除

## 導入

リポジトリ内の plist は個人パスを含まないよう `__HOME__` プレースホルダで書かれている。
Mac 側でチェックアウト（ghq 管理なら worktree）から:

1. `mkdir -p ~/t3-tots-bridge/inbox ~/t3-tots-bridge/outbox ~/t3-tots-bridge/done`
2. `cp bridge/bridge.py ~/t3-tots-bridge/`
3. `sed "s|__HOME__|$HOME|g" bridge/com.muse.t3bridge.plist > ~/Library/LaunchAgents/com.muse.t3bridge.plist`
4. `launchctl load ~/Library/LaunchAgents/com.muse.t3bridge.plist`
5. Muse が `bridge-ping` リクエストで疎通確認

## リクエスト形式

```json
{"id": "20261107-001", "argv": ["python3", "t3r.py", "health"], "env": {"T3M_DB": "..."}, "timeout": 120}
```

タスク依頼（書き込み）の例:

```json
{"id": "20261107-002", "argv": ["python3", "t3w.py", "start", "/path/to/project", "依頼内容"], "timeout": 120}
```

承認待ち・質問待ちへの回答の例（トークンは最初に `issue-token` で一度だけ発行する。詳細と失効手順は `README.ja.md`）:

```json
{"id": "20261107-003", "argv": ["python3", "t3a.py", "issue-token"], "timeout": 120}
{"id": "20261107-004", "argv": ["python3", "t3a.py", "pending"], "timeout": 120}
{"id": "20261107-005", "argv": ["python3", "t3a.py", "answer", "<request-id>", "deny", "--user-approved"], "timeout": 120}
{"id": "20261107-006", "argv": ["python3", "t3a.py", "answer", "<request-id>", "回答テキスト"], "timeout": 120}
```

結果: `{"id": ..., "ok": true, "exit_code": 0, "stdout": "...", "stderr": "...", "finished_at": "..."}`
