# iconnect-scheduler

自動投稿を**狙った時刻に**動かすための ticker。

GitHubのスケジュール実行(cron)は発火自体が遅れる。`dai916/iconnect-threads` での
実測(scheduleイベント300件)では、cron設定時刻からの遅延が

| cron時刻(UTC) | 遅延中央値 | p90 | 最大 |
|---|---|---|---|
| 22〜23時台 | 約50分 | 約2時間 | 7.8時間 |
| 11〜15時台 | 約2時間 | 約4時間 | 10時間 |

あり、AM枠(08:15狙い)が09:30〜10:30、PM枠(22:00狙い)が翌01:30前後に着地していた。
遅延はrun作成の時点で起きている(run作成時刻＝ジョブ開始時刻)ため、ジョブを速くしても
ワークフローを分けても解決しない。

このリポジトリは**公開リポジトリのActions実行が無料**であることを使い、20分おきに
起きて「投稿時刻が近ければその時刻まで待ってから `workflow_dispatch` で叩く」役をやる。
待ち時間ぶんランナーを占有するが、公開リポジトリなので課金されない。

- 対象: `dai916/iconnect-threads` の `post-<曜日>-<枠>.yml`
- 狙う時刻: AM 08:15 / PM 22:00 (JST) — `scripts/dispatch_threads.py` の `SLOTS`
- 起動が遅れて投稿時刻を過ぎていた場合も、4時間以内なら即叩く(取り返し)
- その枠が今日すでに実行済みなら何もしない(二重投稿防止)

クリニックの原稿・データ・認証情報はこのリポジトリには入れないこと。

## セットアップ(PATの登録)

1. https://github.com/settings/personal-access-tokens/new でfine-grained PATを作る
   - Repository access: **Only select repositories** → `dai916/iconnect-threads`
   - Permissions → Repository permissions → **Actions: Read and write**
   - Expiration: 1年など(切れると定刻投稿が止まり、遅延したcron任せに戻る)
2. このリポジトリの Settings → Secrets and variables → Actions → New repository secret
   - Name: `THREADS_DISPATCH_TOKEN`
   - Secret: 1で作ったトークン
3. Actions タブ → Threads Scheduler (ticker) → Run workflow → `dry_run` にチェックを
   入れて実行し、ログの判定内容を確認する(実際には叩かない)

PAT未設定のあいだ ticker は何もせず正常終了する。投稿は iconnect-threads 側の
定時cron(遅延あり)が担保する。

## 運用メモ

- 投稿時刻を変えるときは `scripts/dispatch_threads.py` の `SLOTS` と、
  iconnect-threads 側の `AUTO_POST_README.md` の表を両方直す。
- iconnect-threads 側の定時cronは**フォールバック**として残してある。ticker が
  先に叩けば、遅れて発火したcronは「今日はもう投稿済み」と判断して何もしない。
- ticker が動いているかは Actions の実行ログで分かる。20分おきに緑になっていて、
  投稿時刻の前後だけ `post-*.yml を起動しました` が出ていれば正常。
