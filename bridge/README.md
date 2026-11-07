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

- 実行できるのは許可リストのみ：組み込み `bridge-ping` と、自前リーダー `t3r.py` の読み取り専用サブコマンド（`schema` / `health` / `status`）だけ。旧来の外部ツールは許可リストに無い
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

結果: `{"id": ..., "ok": true, "exit_code": 0, "stdout": "...", "stderr": "...", "finished_at": "..."}`
