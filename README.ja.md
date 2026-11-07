# t3-muse

[English](README.md) | 日本語

T3 Code を Meta Muse から扱うためのコンパニオン。必要なものを、
実際に要るものから1つずつ自前で実装していくリポジトリ。

## 構成図

![構成図。Mac Studio の外では、ユーザーが Muse に依頼・承認を送り、Muse がデバイスチャネルでファイルメールボックスにリクエスト JSON を書く。Mac Studio の中では、メールボックスが Muse に結果 JSON を返す。bridge.py は inbox をポーリングし、t3r.py と t3w.py に対して許可したコマンドだけ実行する。t3r.py は T3 Code 状態 DB を読み取り専用で開く。t3w.py は t3 thread CLI を通じてスレッド作成・送信を行い、CLI はローカルセッションで T3 Code につながる。T3 Code は DB を読み書きする。既存 t3-tots (Grok 用) から DB への線は対象外 (触らない)。別に、メールボックスの restart-trigger に置いたファイルで launchd が restart-bridge.sh を起動し、ブリッジのジョブだけを再起動する。](docs/architecture.svg)

要点:

- Mac から Muse へ能動的に接続する経路はない。Muse がデバイスチャネル経由でファイルを置きに・取りに行く (メールボックス方式)
- ブリッジはネットワークを待ち受けず、許可リスト (`bridge-ping`、`t3r.py` の読み取り系、`t3w.py` のタスク依頼系) 以外は実行しない
- DB への直接アクセスは読み取り専用のみ。T3 への作業依頼は T3 純正 CLI 経由 (`t3w.py`) だけで、ユーザーの依頼に基づく場合に限る
- 既存の Grok 用 t3-tots 環境 (`~/t3-tots`) は同じ Mac で稼働しているが、本プロジェクトの対象外
- ブリッジは自分自身を再起動できないため、別の launchd ジョブが担当する。`~/t3-muse-bridge/restart-trigger` にファイルを置くと `restart-bridge.sh` が動き、ブリッジのジョブだけを再起動する。ブリッジ本体に `launchctl` の権限は増えない

開発フロー: 変更はブランチ → PR → CI (GitHub Actions) → squash マージ。
`.github/workflows/` のファイルだけは Muse の GitHub App の権限外のため、専用 PAT 経由で push する。

## 現在の構成

| パス | 内容 |
|---|---|
| `bridge/` | Mac 常駐ブリッジ。Muse のデバイスチャネル経由のファイルメールボックスで、許可リストのコマンドだけを実行する (設計は `bridge/README.md`) |
| `tools/` | Mac 側の自前ツール群。`t3r.py` は T3 Code の状態 DB を読み取り専用で引く最小リーダー (`schema` / `health` / `status`)。`t3w.py` は T3 純正 `t3 thread` CLI 経由でタスク依頼を行うライター (`start` / `send` / `show` / `list` / `add-project`) |

## 開発の進め方

- まずブリッジを Mac Studio で動かし、疎通 (bridge-ping) を確認する
- 次に読み取り専用の最小経路を通す
- スキルは必要になったものから、実動作に合わせて1つずつ書く
