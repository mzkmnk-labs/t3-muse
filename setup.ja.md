# t3-muse のセットアップ

[English](setup.md) | 日本語

このファイルは Muse が読むためのセットアップ指示書である。t3-muse をセットアップするには、自分の Muse に次の一文を送る:

```text
https://raw.githubusercontent.com/mzkmnk-labs/t3-muse/main/setup.ja.md を読んで、t3-muse をセットアップして。
```

あとは Muse がセットアップを進め、人の操作が必要な場面ではチャットで伝える。

ここから先は Muse に向けた指示である。

## 前提

始める前に、次をユーザーに確認する:

- ユーザーの Mac で T3 Code が動いている
- ユーザーが Muse のアカウントを持っている
- Mac に Node.js 24 以上が入っている

## 手順

### 1. starter skill を配置する

リポジトリ https://github.com/mzkmnk-labs/t3-muse を取得し、その中の `muse/skills/t3-muse-starter/` (`SKILL.md` と `skill.yaml`) を、ユーザーのカスタムスキル置き場 `~/workspace/custom-skills/t3-muse-starter/` に配置する。

### 2. skill に従う

配置した skill (T3 Muse Starter) の手順に従ってセットアップを進める。

### 3. 本人にしかできない操作をユーザーに伝える

次の場面ではそのつど手を止め、何をすればよいかをチャットでユーザーに具体的に伝えて、操作を待つ。

- **インストーラーの実行。** Mac のターミナルで `npx @mzkmnk-labs/t3-muse install` を実行するよう頼む。ブリッジとツールを配置し、2 つの LaunchAgent を load して、メールボックス経由で `bridge-ping`、`t3r health`、`t3a pending` を確認する。
- **トークン発行の確認への回答。** 初回のインストールでは `No t3a token found. Issue one now? [y/N]` と尋ねられる。`y` で t3a トークンが発行されること、このトークンで T3 Code の承認待ち・質問待ちへユーザーの回答を中継すること、トークンの値は出力されないことを伝える。答えるのはユーザーである。それ以外の答えではインストールは完了しないので、ユーザーが `python3 ~/t3-muse-tools/t3a.py issue-token` を実行した後に、もう一度 `install` を実行してもらう。
- **T3 Connect の有効化と MCP URL の受け渡し。** T3 Code の環境設定で T3 Connect を有効にし、環境の MCP URL をコピーして渡すよう頼む。コピーは、T3 Connect 経由で接続しているクライアント (Web アプリなど) から行ってもらう。ホストの Mac 上でコピーした URL は LAN アドレスになることがあり、Muse からは届かず、サインインも完了できないと注意する。
- **接続の承認とアクセスモードの選択。** URL をカスタムコネクタとして登録してサインインを始めたら、T3 Code のアプリでそれを承認し、アクセスモードを選ぶよう頼む。コネクタ経由で Muse ができることはすべてこのモードが上限になるので、セットアップで行うスレッドの起動を許すモードが必要だと伝える。

これらの操作をユーザーの代わりに行わない。承認や T3 Code の質問への回答も、自分では決めない。

## 完了の目安

- メールボックス経由の `bridge-ping` が pong を返す
- `t3r health` が成功する
- `t3a pending` が空の一覧を返す
- MCP 経由の読み取り (プロジェクト一覧など) が成功する
- T3 Muse PM と T3 Muse Bridge の 2 つの skill が、ユーザーのカスタムスキル置き場の `~/workspace/custom-skills/t3-muse-pm/` と `~/workspace/custom-skills/t3-muse-bridge/` にある

結果をユーザーに報告する。

## ユーザーに伝えておくこと

**止め方。** ブリッジの LaunchAgent の plist を unload するか、ブリッジのフォルダ `~/t3-muse-bridge` を削除すると、すべて止まる。これが kill switch になる:

```sh
launchctl unload ~/Library/LaunchAgents/com.muse.t3bridge.plist ~/Library/LaunchAgents/com.muse.t3bridge-restarter.plist
```

**アンインストール。** `npx @mzkmnk-labs/t3-muse uninstall` は 2 つの LaunchAgent を unload して plist を削除し、データフォルダは残す。`npx @mzkmnk-labs/t3-muse uninstall --purge` は、ユーザーの確認のうえで `~/t3-muse-bridge` と `~/t3-muse-tools` も削除する。トークンのファイルを消しても T3 側のセッションは期限まで有効なまま残る。失効の手順はインストーラーが出力する。

**資格情報の期限。**

| 資格情報 | 有効期間 | 更新の方法 |
|---|---|---|
| MCP コネクタの grant | 約 30 日 | ユーザーにもう一度接続を承認してもらう |
| t3a の承認用トークン | 90 日 | ブリッジ経由で `t3a renew` を実行する |
