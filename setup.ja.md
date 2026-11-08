# t3-muse のセットアップ

[English](setup.md) | 日本語

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
- **トークン発行の確認への回答。** 初回のインストールでは `No t3a token found. Issue one now? [y/N]` と尋ねられる。`y` で t3a トークンが発行されること、このトークンで T3 Code の承認待ち・質問待ちへユーザーの回答を中継すること、トークンの値は出力されないことを伝える。トークンは通常このプロンプトで発行し、答えるのはユーザー本人で、自分のターミナルで答える。Muse からはそのターミナルが見えないので、プロンプトが表示されたか、何と答えたかをユーザーに確認する。
  - **`y` と答えた場合。** インストーラーがトークンを発行し、`t3a pending` をもう一度確認する。
  - **それ以外を答えた場合。** それは「発行しない」という答えで、インストールは `t3a pending` で `NO_TOKEN` になって止まる。その場で Muse がトークンを発行してはならない。セットアップに必要なら、理由を改めて説明して聞き直す。
  - **プロンプトが表示されなかった場合。** インストーラーをターミナルなしで実行したときなどで、インストールは `t3a pending` で `NO_TOKEN` になって止まる。トークンを発行してよいか、チャットでユーザーに尋ねる。

  後の 2 つの場合に先に進むのは、ユーザーがチャットで発行に同意してからに限る。そのうえで、ユーザーがターミナルで `python3 ~/t3-muse-tools/t3a.py issue-token` を実行してもう一度 `install` を実行するか、T3 Muse Starter の手順どおりに Muse がブリッジ経由で `t3a issue-token` を実行して `t3a pending` を確認する。
- **T3 Connect の有効化と MCP URL の受け渡し。** T3 Code の環境設定で T3 Connect を有効にし、環境の MCP URL をコピーして渡すよう頼む。コピーは、T3 Connect 経由で接続しているクライアント (Web アプリなど) から行ってもらう。ホストの Mac 上でコピーした URL は LAN アドレスになることがあり、Muse からは届かず、サインインも完了できないと注意する。
- **接続の承認とアクセスモードの選択。** URL をカスタムコネクタとして登録してサインインを始めたら、T3 Code のアプリでそれを承認し、アクセスモードを選ぶよう頼む。コネクタ経由で Muse ができることはすべてこのモードが上限になるので、セットアップで行うスレッドの起動を許すモードが必要だと伝える。

これらの操作をユーザーの代わりに行わない。承認や T3 Code の質問への回答も、自分では決めない。Muse が実行してよいのはブリッジ経由のトークン発行だけで、それもユーザーがチャットで同意したときに限る。

## 完了の目安

- メールボックス経由の `bridge-ping` が pong を返す
- `t3r health` が成功する
- `t3a pending` が空の一覧を返す
- MCP 経由の読み取り (プロジェクト一覧など) が成功する
- T3 Muse PM と T3 Muse Bridge の 2 つの skill が、ユーザーのカスタムスキル置き場の `~/workspace/custom-skills/t3-muse-pm/` と `~/workspace/custom-skills/t3-muse-bridge/` にある

結果をユーザーに報告する。

## ユーザーに伝えておくこと

**止め方。** t3-muse は 2 つの経路で T3 Code に届き、止め方は経路ごとに別になる。片方を止めても、もう片方は動き続ける。

- **ブリッジ。** 2 つの LaunchAgent を unload すると、ブリッジの daemon と再起動役が止まる。これがブリッジの kill switch になる:

  ```sh
  launchctl unload ~/Library/LaunchAgents/com.muse.t3bridge.plist ~/Library/LaunchAgents/com.muse.t3bridge-restarter.plist
  ```

  plist は `~/Library/LaunchAgents` に残るので、次のログインで launchd がまた load する。止めたままにするには、下のとおりアンインストールする。ブリッジのフォルダ `~/t3-muse-bridge` を削除するだけでは止まらない。動いている daemon は inbox を見張り続け、フォルダがまたできれば、そこに書かれた依頼を実行する。先に unload し、フォルダが不要ならその後で削除する。
- **MCP コネクタ。** grant は T3 Code 側にあり、ブリッジ側の操作では変わらない。ユーザーが T3 Code の設定で接続済みのクライアントを取り消すか、T3 Connect を無効にして止める。T3 Connect を無効にすると、Web アプリなど T3 Connect 経由のほかのクライアントも切れる。これは T3 Code 自体での操作で、t3-muse の中から行うものではない。

**アンインストール。** `npx @mzkmnk-labs/t3-muse uninstall` は 2 つの LaunchAgent を unload して plist を削除し、データフォルダは残す。`npx @mzkmnk-labs/t3-muse uninstall --purge` は、ユーザーの確認のうえで `~/t3-muse-bridge` と `~/t3-muse-tools` も削除する。トークンのファイルを消しても T3 側のセッションは期限まで有効なまま残る。失効の手順はインストーラーが出力する。

**資格情報の期限。**

| 資格情報 | 有効期間 | 更新の方法 |
|---|---|---|
| MCP コネクタの grant | 約 30 日 | ユーザーにもう一度接続を承認してもらう |
| t3a の承認用トークン | 90 日 | ブリッジ経由で `t3a renew` を実行する |
