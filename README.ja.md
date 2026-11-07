# t3-muse

[English](README.md) | 日本語

T3 Code を Meta Muse から扱うためのコンパニオン。必要なものを、
実際に要るものから1つずつ自前で実装していくリポジトリ。

## 構成図

```mermaid
flowchart LR
    User["ユーザー"]
    Muse["Muse"]
    subgraph Mac["Mac Studio"]
        Mailbox["ファイルメールボックス<br/>~/t3-tots-bridge<br/>inbox / outbox / done"]
        Bridge["bridge.py<br/>launchd 常駐・許可リスト検証"]
        Reader["t3r.py (読み取り)<br/>~/t3-muse-tools"]
        Writer["t3w.py (タスク依頼)<br/>~/t3-muse-tools"]
        T3CLI["t3 thread CLI<br/>T3 Code 同梱"]
        DB[("T3 Code 状態 DB<br/>~/.t3/userdata/statev2.sqlite")]
        T3["T3 Code"]
        Legacy["既存 t3-tots (Grok 用)<br/>~/t3-tots"]
    end
    User -->|"依頼・承認"| Claude
    Muse -->|"デバイスチャネルで<br/>リクエスト JSON を書く"| Mailbox
    Mailbox -->|"結果 JSON を返す"| Claude
    Bridge -->|"inbox をポーリング"| Mailbox
    Bridge -->|"許可したコマンドだけ実行"| Reader
    Bridge -->|"許可したコマンドだけ実行"| Writer
    Reader -->|"読み取り専用で開く"| DB
    Writer -->|"スレッド作成・送信"| T3CLI
    T3CLI -->|"ローカルセッション"| T3
    T3 -->|"読み書き"| DB
    Legacy -.->|"対象外 (触らない)"| DB
```

要点:

- Mac から Muse へ能動的に接続する経路はない。Muse がデバイスチャネル経由でファイルを置きに・取りに行く (メールボックス方式)
- ブリッジはネットワークを待ち受けず、許可リスト (`bridge-ping`、`t3r.py` の読み取り系、`t3w.py` のタスク依頼系) 以外は実行しない
- DB への直接アクセスは読み取り専用のみ。T3 への作業依頼は T3 純正 CLI 経由 (`t3w.py`) だけで、ユーザーの依頼に基づく場合に限る
- 既存の Grok 用 t3-tots 環境 (`~/t3-tots`) は同じ Mac で稼働しているが、本プロジェクトの対象外

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
