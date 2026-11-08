# t3-muse のセットアップ

[English](setup.md) | 日本語

Muse にプロンプトを 1 つ渡すと、Muse が starter skill に従って Mac 上に t3-muse をセットアップする。作業のほとんどは Muse が自分で進め、本人にしかできない数か所でだけ止まって、何をすればよいかを伝える。

## 前提

- Mac で T3 Code が動いている
- Muse のアカウントがある
- Mac に Node.js 18 以上が入っている

## Muse に渡すプロンプト

次の文面を Muse とのチャットに貼り付ける:

```text
t3-muse をセットアップしてください。

リポジトリ https://github.com/mzkmnk-labs/t3-muse を取得し、その中の skills/t3-muse-starter/（SKILL.md と skill.yaml）を、私のカスタムスキル置き場 ~/workspace/custom-skills/t3-muse-starter/ に配置してください。

配置したら、その skill（T3 Muse Starter）の手順に従ってセットアップを進めてください。途中で私の操作が必要なところ（ターミナルで npx @mzkmnk-labs/t3-muse install を実行すること、トークン発行の確認に答えること、T3 Code で T3 Connect を有効にして MCP URL を渡すこと、接続を承認すること）は、その都度私に指示を出してください。承認や質問への判断は私が行います。
```

## プロンプトを渡した後に起きること

Muse は skill を配置して、その手順を進める。本人の操作が要るのは次の場面で、この順に来る。

### 1. インストーラーを実行し、トークン発行の確認に答える

Muse から、Mac のターミナルで次を実行するよう指示がある:

```sh
npx @mzkmnk-labs/t3-muse install
```

ブリッジとツールを配置し、2 つの LaunchAgent を load して、経路全体を確認する。初回は承認用トークンがまだないので、次のように尋ねられる:

```text
No t3a token found. Issue one now? [y/N]
```

`y` と答える。このトークンで、t3-muse は T3 Code の承認待ち・質問待ちへ本人の回答を中継する。インストーラーはトークンのスコープ、90 日の有効期間、ファイルの場所を出力するが、トークンの値は出力しない。

それ以外の答えでは、インストールは完了しない。準備ができたら `python3 ~/t3-muse-tools/t3a.py issue-token` を実行し、もう一度 `install` を実行する。

### 2. T3 Connect を有効にして MCP URL をコピーする

T3 Code の環境設定で T3 Connect を有効にする。環境の MCP URL をコピーして Muse に渡す。

URL は、T3 Connect 経由で接続しているクライアント (Web アプリなど) からコピーする。ホストの Mac 上でコピーすると LAN アドレスになることがあり、Muse からは届かず、サインインも完了できない。

### 3. 接続を承認し、アクセスモードを選ぶ

Muse がその URL をカスタムコネクタとして登録し、サインインを始める。T3 Code のアプリでそれを承認し、アクセスモードを選ぶ。コネクタ経由で Muse ができることはすべてこのモードが上限になるので、Muse に任せたいスレッドの起動を許すモードを選ぶ。

## 完了の目安

- メールボックス経由の `bridge-ping` が pong を返す
- `t3r health` が成功する
- `t3a pending` が空の一覧を返す
- MCP 経由の読み取り (プロジェクト一覧など) が成功する

最初の 3 つが通るとインストーラーは `install complete.` と出力する。Muse は 4 つすべてを確認して結果を報告する。

## 補足

**止め方。** ブリッジの LaunchAgent の plist を unload するか、ブリッジのフォルダ `~/t3-muse-bridge` を削除すると、すべて止まる。これが kill switch になる:

```sh
launchctl unload ~/Library/LaunchAgents/com.muse.t3bridge.plist ~/Library/LaunchAgents/com.muse.t3bridge-restarter.plist
```

**アンインストール。** `npx @mzkmnk-labs/t3-muse uninstall` は 2 つの LaunchAgent を unload して plist を削除し、データフォルダは残す。`npx @mzkmnk-labs/t3-muse uninstall --purge` は、確認のうえで `~/t3-muse-bridge` と `~/t3-muse-tools` も削除する。トークンのファイルを消しても T3 側のセッションは期限まで有効なまま残る。失効の手順はインストーラーが出力する。

**資格情報の期限。**

| 資格情報 | 有効期間 | 更新の方法 |
|---|---|---|
| MCP コネクタの grant | 約 30 日 | 接続をもう一度承認する |
| t3a の承認用トークン | 90 日 | `t3a renew` を実行する。Muse がブリッジ経由で行う |
