# Kukai Bot 現在仕様（技術者向け）

最終更新: 2026-07-05（段階的参加者限定チャンネルの追加）

## 1. 全体構成
- 実装言語: Python 3.11+（開発・テスト環境は Python 3.13）
- フレームワーク: `discord.py`（`app_commands` ベース）
- DB: PostgreSQL（Alembic マイグレーション管理）
  - ローカル単体テストは in-memory SQLite を使用
  - PostgreSQL運用手順は `docs/operations.md` を参照
- レイヤ: `cogs`（UI/コマンド） / `services`（業務ロジック） / `repositories`（DBアクセス） / `ui`（View/Modal）

## 2. 句会ステート
- 主な状態:
  - `draft`
  - `entry_open` / `entry_closed`
  - `submission_open` / `submission_closed`
  - `waiting_publish`
  - `selecting_open` / `selecting_closed`
  - `results`
  - `ended`
  - `paused`
  - `cancelled`
- `waiting_results` は実運用フローから除外（`selecting_closed -> results`）
- 遷移ロジック: `bot/state_machine/transitions.py`
- 句会作成直後の初期状態:
  - `entry_enabled=true` の場合は `entry_open`
  - `entry_enabled=false` の場合は `submission_open`
  - `draft` はロールバック/インポート用の状態として残す

## 3. 句会作成ウィザード
- ステップ:
  1. 基本情報
  2. エントリー設定
  3. 締切設定
  4. 投句設定
  5. 選句設定
  6. 公開・結果設定
  7. ボイス句会設定
  8. 通知設定
  9. 確認
- 基本情報:
  - 新規チャンネル/既存チャンネル切替
  - 新規時はカテゴリ選択可
  - 新規時チャンネル名は別モーダルで設定可（未設定時は句会名）
  - 閲覧モードを `公開` / `参加受付後は参加者限定` から選択可能
- 締切表示:
  - エントリー無効時はエントリー締切を非表示
- `/kukai create-bulk`:
  - 行形式テキストから句会を一括作成
  - GUIウィザードと併存し、同じ `kukai_service.create_kukai()` を使用
  - `preset_id` 指定時は選句プリセットを句会ラベルへ展開
  - `label=` 行がある場合は `preset_id` より優先
  - `channel=current/new/<#channel_id>` に対応
  - `channel_visibility_policy=public|public_until_participation_close` に対応
  - `voice_enabled`, `voice_channel`, `voice_start_at`, `voice_end_at` でボイス句会イベントを作成
  - `reminder=` 行で通知回ごとの時刻・通知先・対象・mention有無を指定
- GUIウィザード:
  - サーバー既定プリセットがある場合、開始時点で選句設定に自動適用
  - 選句設定ステップではプリセット選択に加えて、選句種別を直接入力可能
    - 書式: `名前,点数,rank,最小数,最大数,コメントモード`
    - 例: `特選,2,1,0,1,optional`
    - 直接入力した場合は句会固有の「カスタム」選句仕様として保存
  - ボイス句会設定ステップでボイス/ステージチャンネル、開始日時、終了日時を設定
  - 通知設定ステップで `event,offset,destination,target,mention` 形式の通知を複数登録
- GUIボタン配置:
  - 同一行に赤系（`danger`）ボタンと緑系（`success`）ボタンが並ぶ場合、赤系を最も左、緑系を最も右に配置する
  - 例: `キャンセル` / `却下` / `削除` / `取消` は左側、`次へ` / `作成` / `承認` / `決定` / `追加` は右側
  - 既存のDiscordメッセージに表示済みのボタン配置は自動更新されない。新しく開いたGUI、または再投稿・編集されたメッセージから新配置になる

## 3b. サーバーハブ・管理パネル
- サーバーハブ:
  - `/guild portal setup` でサーバー管理者が `句会案内` チャンネルを作成または再利用し、常駐ポータルボタンを投稿する
  - `/guild portal repost` で保存済みポータルチャンネルへボタンを再投稿する
  - `GuildSettings.portal_channel_id` にポータルチャンネルIDを保存する
  - ポータルボタンは `persistent view` と固定 `custom_id` を使用し、Bot再起動後も押下に応答する
  - ボタン:
    - `句会を作成`: 作成権限を確認して既存の句会作成ウィザードを ephemeral で開く。ポータルメッセージ自体は編集しない
    - `句会一覧`: このサーバーの開催中・招集中句会を `/list` と同じEmbed生成処理で ephemeral 表示
    - `自分の状況`: `/check` と同じ参加状況Embedを表示。複数候補がある場合は本人専用セレクトを出す
    - `参加の記録`: 対象、範囲、表示軸、俳号、表示件数を選び、`/record me` / `/record user` と同じEmbedとMarkdownをephemeral表示する。表示件数は正整数入力で、空欄なら全件を表示対象にする
      - EmbedにはDiscord上限まで投句・得点・選句ラベル・選んだ句・公開可能な作者を表示し、選評・総評と収まらない詳細は全件Markdownに収録する
      - `participation_record_visibility=private` では本人だけを対象にする
      - `guild_public` では同一サーバーの他参加者を選択できる。他人の記録は結果公開済みの現在サーバー内句会だけを表示する
- 句会管理パネル:
  - `/kukai panel [kukai_id]` で句会管理者が private admin thread に管理パネル入口を投稿する
  - 句会作成ウィザード完了後にも、可能なら管理パネル入口を自動投稿する
  - 入口ボタンは `persistent view` と固定 `custom_id` を使用し、Bot再起動後も押下に応答する
  - パネル押下時にも `permission_service.is_kukai_admin()` で権限を再確認する
  - 現在のパネル操作:
    - 状態更新
    - 一時停止
    - 再開
    - 中止（確認UIあり）
    - 現在状態に合わせた操作ボタン再投稿
    - 投句一覧PDFを開催チャンネルへ送信（投句一覧公開後）
    - 結果PDFを開催チャンネルへ送信（結果公開後）
    - 作者を公開（作者公開方式が手動の場合のみ）
  - PDF送信は本人専用の確認画面で記名・無記名を選ぶ。無記名が初期値で、記名は結果公開後かつ作者公開済みの場合だけ選択できる
  - `次へ進める` は現在状態・次状態・実行される副作用を表示し、確認後に既存 `/kukai proceed` と共通の進行ヘルパーを実行する
  - 条件未達がある場合は未達状況を表示し、確認後のみ進行する

## 4. 進行通知
- ステージ開始時に開催チャンネルへ Embed 通知
- 通知内容:
  - 句会名
  - 開始ステージ
  - 直近ステージに対応する締切のみ（例: 投句開始時は投句締切）
  - 選句開始時は句会情報表示と同じ句数・選句数サマリ
- 状態遷移通知:
  - エントリー締切、投句締切、選句締切、一時停止、再開、中止なども開催チャンネルへ通知
- 通知に操作ボタンを付与:
  - エントリーする / 投句する / 選句する
  - ボタンはコマンド案内ではなく実処理を実行（モーダル/GUI表示）
  - 公開チャンネルに残る入口ボタンは `persistent view` と固定 `custom_id` を使用し、Bot再起動後も押下に応答する
  - 既存の古いメッセージ（永続View化前に投稿されたボタン）は対象外。新コードで投稿されたメッセージから再起動耐性を持つ
  - `エントリーする` ボタンは Discord の初回応答期限内にモーダルを出すため、ボタン押下時にはDB参照を行わず、モーダル提出時に句会状態・重複・俳号重複を検証する
  - `投句する` / `選句する` ボタンはDB上の現在状態を再検証して、受付中でなければ「現在は受付中ではありません」などのエラーを返す
  - 手動進行と scheduler 自動進行は共通のステージ通知処理を使う。半自動/全自動で投句締切から選句受付へ進んだ場合も、選句開始通知に `選句する` ボタンを付与する
- 手動ボタン投稿:
  - `/kukai button kind:<current|entry|submission|selecting|result> [kukai_id]`
  - 句会管理者のみ実行可能
  - 開催チャンネルへ操作ボタンを再投稿する
  - `current` は現在状態に応じて、エントリー/投句/選句/結果のいずれかを投稿
  - `result` は結果公開後（`results` / `ended`）のみ投稿可能で、既存の `結果を見る` persistent view を使用する
- 管理者向け内部通知:
  - 句会ごとに private thread を必要時に自動作成する
  - スレッドは句会チャンネル配下に作成し、句会作成者・追加句会管理者を参加させる
  - 新規作成する管理スレッド名は開催チャンネル名に `-admin` を付けた名前にする
  - `kukais.admin_thread_id` に保存し、削除/取得不能時は再作成を試みる
  - 一般参加者には見せない運用警告、未達者詳細、未達のまま進行した記録を送信する
  - private thread 作成や送信に失敗した場合は作成者DM、さらに失敗時は句会チャンネルで管理者mentionにフォールバックする

## 5. コマンドの `kukai_id` 省略
- 全コマンドで `kukai_id` は省略可能（`int | None = None`）
- 解決ルール:
  - `kukai_id` 指定時はそのIDを優先
  - 省略時は「同一チャンネルの進行中句会」を探索（`ENDED` / `CANCELLED` 除外）
  - 候補 0 件/複数件はエラーで明示
- スレッド対応:
  - スレッド内でコマンドを使用した場合、`interaction.channel_id`（スレッドID）ではなく親チャンネルIDを使って解決する
  - `bot/utils/channel.py` の `effective_channel_id()` で透過的に処理

## 5b. 句会チャンネル閲覧モード
- `Kukai.channel_visibility_policy`:
  - `public`: 現在通り、Discord サーバー/チャンネル権限に従って閲覧できる
  - `public_until_participation_close`: エントリー導線が必要な段階は公開し、締切到達後に参加者限定へ切り替える
- `public_until_participation_close` の限定化タイミング:
  - v1では新規チャンネル作成時のみ指定可能。既存チャンネル利用時は指定不可
  - エントリー締切がある場合は `entry_close_at` 到達後
  - エントリー締切がない場合、またはエントリー制なしの場合は `submission_close_at` 到達後
  - scheduler の `entry_close` / `submission_close`、および `/kukai proceed` の手動進行時に権限同期を試みる
- 参加者限定化時の権限:
  - `@everyone` の `view_channel=False`
  - Bot 自身に `view_channel/send_messages/manage_channels/read_message_history`
  - 句会作成者、追加句会管理者、承認済みエントリーに `view_channel/send_messages/read_message_history`
  - Discord サーバー管理者は Discord 側権限に従う
- エントリー状態との同期:
  - 限定化後の `/entry join`、承認、承認一括、却下、取消、削除、締切後申請承認/却下で個別 overwrite を付与/削除する
  - 権限同期に失敗してもエントリー状態更新は成功扱いとし、管理者へ `/kukai visibility-sync` による再同期を促す
- `/kukai visibility-sync [kukai_id]`:
  - 句会管理者向けの手動再同期コマンド
  - `public` の句会では no-op
  - `public_until_participation_close` では、現在の DB 状態を正として開催チャンネル権限を再同期する
- 作成後に閲覧モードを変更するコマンドは未実装
- 既存チャンネルの参加者限定化は未実装。既存 overwrite の保存・復元が必要なため将来課題とする

## 6. 句会情報表示
- `/kukai info`:
  - 句会作成時の案内に近い形式で、基本情報を表示
  - `現在の状態` 欄は常に維持
  - 表示項目:
    - 句会名
    - 説明
    - 題
    - 現在の状態
    - 句数
    - エントリー締切（エントリー有効時）
    - 投句締切
    - 選句締切
    - ボイス句会（設定がある場合）
  - ボイス句会は `VoiceSession` を明示取得し、開始日時・場所・終了日時を表示

## 7. エントリー
- `/entry join`:
  - 句会にエントリーする
  - `kukai_id` 省略時は同一チャンネルの進行中句会から自動解決
  - 俳号未入力時は Discord の表示名を使用
- 公開チャンネルの `エントリーする` ボタン:
  - ボタン押下時は即時に俳号入力モーダルを表示
  - 実際のエントリー登録可否はモーダル提出時に `/entry join` と同じサービス層で判定
  - 受付終了・重複参加・俳号重複などはモーダル提出後に ephemeral エラーとして返す
- `/entry cancel`:
  - エントリー受付期間中のみ、自分のエントリーを取消
- 承認制:
  - `entry_approval=true` の句会では通常エントリーも `pending`
  - 管理者は `/entry approve` / `/entry reject` で承認・却下
  - `/entry approve-all` で承認待ちエントリーを一括承認
- 締切後エントリー（状態ベース判定）:
  - **状態が `entry_closed` の場合のみ**「締切後エントリー」と判定する（時刻ベース判定は廃止）
  - `ENTRY_OPEN` 状態では、`entry_close_at` が過去であっても通常承認される
  - `entry_approval=false` でも `entry_closed` 状態での申請は `pending` として登録
  - 句会チャンネルへ句会作成者・追加管理者をメンションして通知
  - 承認された場合のみ、以後の投句・選句対象になる
- 承認通知:
  - `/entry approve user:...` および承認セレクトメニューの両方で送信
  - 句会チャンネルへメンションなしで `［俳号］さんの参加が承認されました` と送信
  - 却下時もメンションなしで `［俳号］さんの参加が却下されました` と送信
- `/entry list`:
  - 管理者向け。`pending` / `approved` / `rejected` / `withdrawn` で絞り込み可能
- `/entry remove`:
  - 管理者向け。エントリー締切後にエントリーを削除
- `/kukai status`:
  - 句会管理者向け。句会内容そのものは表示せず、進捗だけを表示
  - エントリー者: 承認済/承認待ち/却下/取消をアイコン付きで表示
  - 投句状況: 承認済参加者ごとに投句数を表示し、必要投句数未満は `⚠️`
  - 選句状況: 承認済参加者ごとに選句ラベル別件数、コメント数、作者コメント数、総評有無を表示
  - 選句ラベルの `min_count` 未満は `⚠️` と不足ラベルを表示
  - エントリー制なしの場合は投句・選句・総評の記録があるユーザーを対象に表示
- `/kukai proceed`:
  - 投句受付中または選句受付中に条件未達の参加者がいる場合、進行前に実行者だけへ ephemeral 警告を表示する
  - 警告には未達者と不足内容を表示し、`それでも進める` / `キャンセル` を選べる
  - `それでも進める` を選んだ場合のみ進行し、管理者スレッドへ「条件未達のまま手動進行した」記録を送信する

## 8. 投句UI
- `/submit` で投句GUIを表示
- GUIは `編集` ボタンのみを表示し、編集モーダルで現在の投句全体を同期する
  - 入力欄は1つで、1行を1句として扱う
  - 既存行を書き換えるとその句を変更する
  - 行を追加すると投句を追加する
  - 行を削除すると投句を削除する
  - 入力欄を空にして送信すると、登録済みの投句をすべて削除する
  - 空行は無視する
  - 1句は500文字まで
  - モーダル本文欄全体はDiscordの入力欄制約により最大4000文字
  - `submission_max` が設定されている句会では、編集後の総投句数が上限を超える場合は1句も更新せずエラーにする
  - 編集後総数の上限超過エラーは `投句上限を超えています（上限N句、X句超過）。` と表示する
  - `submission_max` が無制限の場合、GUI独自の句数上限は設けない。ただしモーダル本文欄全体の4000文字制約と1句500文字制約は受ける
- `/submit-bulk`:
  - 1行1句で一括投句（句数上限は `kukai.submission_max` に従う。無制限設定なら上限なし）
  - 句会ID省略時は同一チャンネルの進行中句会から自動解決
  - 受付状態、参加承認、投句上限は `submission_service.submit()` と同じ制約
- 登録完了通知:
  - 投句登録後に「登録しました」通知を表示
  - GUI編集後に「更新しました」通知を表示
  - 現在登録済みの投句内容（抜粋）を併記

## 9. 選句UI
- 句プルダウンの末尾に `総評` を追加
- `総評` 選択時は選種別も総評モードへ切替
- 旧「総評ボタン」は廃止
- `/select-bulk`:
  - 行形式で複数の選句・総評・取消を一括処理
  - 書式: `番号=ラベル|コメント`
  - 総評: `overall=総評本文`
  - 取消: `番号=clear`
  - 番号は公開済み投句番号、ラベルは句会内の選句ラベル名で解決
  - `作者コメント` は自句に対してのみ登録可能
- 作者コメント:
  - 自句のみ設定可能
  - ラベル `作者コメント`（必須コメント）
  - 選句ラベル定義に無い場合は選句開始時に補完
- 登録完了通知:
  - 選評登録後に「登録しました」通知を表示
  - 現在登録済みの選評/総評内容（抜粋）を併記
- DB命名:
  - 現行スキーマでは `select_comments.select_id` を使用
  - 旧エクスポート互換のため、import時のみ `vote_id` 形式も受理する

## 10. 結果表示
- `/result`:
  - 表示切替UI（点数順 / 番号順 / 作者別）
  - 総評ページを同View内で表示
- 自動結果公開:
  - `kukai proceed` で `results` 到達時、および scheduler の自動進行時に、
    `/result` と同じ切替Viewをチャンネル送信
  - チャンネルに投稿される「結果を見る」ボタンは `persistent view` と固定 `custom_id` を使用し、Bot再起動後も押下に応答する
  - 起動時にはDB上の対象句会に対して公開入口Viewを再登録する。メッセージ後編集用の `message_id` 管理は未実装
- 表記:
  - `pt` は `点` に統一
  - 作者コメントは結果表示に反映（番号順・作者別にも表示）
  - 作者名、選評者名、総評者名は俳号を優先して表示
- 順位:
  - 点数降順で並べる
  - 同点時は選句ラベルの `rank_priority` が小さい順に、各ラベルの得票数を比較
  - 完全同点は同順位

## 11. 選句プリセット
- `/select-preset list` / `/select-preset add` / `/select-preset label *` / `/select-preset gui` を提供
- `/select-preset bulk`:
  - 行形式テキストからプリセットを一括作成・更新
  - 対応項目: `name`, `points_enabled`, `set_default`, `label`
  - `label` 書式:
    - `label=名前,点数,rank,最小数,最大数,コメントモード`
    - rank省略: `label=名前,点数,最小数,最大数,コメントモード`
  - `rank` は任意の整数を受け入れる。小さいほど結果同点時の優先度が高い
  - `rank` 省略時はラベル定義順で自動採番
  - `作者コメント` はプリセット登録不可。句会ラベル展開時に `rank_priority=999` で補完
- ウィザード step 5 の「選句種別を直接入力」モーダル:
  - 書式: `名前,点数,最小数,最大数,コメントモード`（5フィールド、rank列なし）
  - rank はリスト順に自動付番される
- プリセットJSON:
  - `points_enabled`
  - `labels[]`: `label`, `point`, `rank_priority`, `min_count`, `max_count`, `comment_mode`
  - 旧JSONに `rank_priority` が無い場合は読み込み時に定義順で補完
- 句会作成後の選句ルール差し替え:
  - `/kukai edit select_rule_config:...` で句会固有の選句ラベルを差し替える
  - `select_rule_config=gui` でモーダル入力を開く
  - 直接入力の対応項目: `preset_id`, `points_enabled`, `label`
  - `preset_id` と `label` は同時指定不可
  - `preset_id` 指定時はプリセット定義の `points_enabled` とラベルを適用する
  - `label` 指定時は `points_enabled` を明示指定可能（未指定時は `true`）
  - 選句開始前状態（`draft` / `entry_open` / `entry_closed` / `submission_open` / `submission_closed` / `waiting_publish`）のみ差し替え可能
  - 既存の選句・総評データが残っている場合は確認UIを表示し、承認時のみそれらを削除して差し替える
  - `作者コメント` は句会ラベル展開時に自動補完される

## 11b. 通知プリセット
- `/notify-preset list` / `/notify-preset add` / `/notify-preset bulk` / `/notify-preset delete` / `/notify-preset set-default` を提供
- `/notify-preset bulk`:
  - 行形式テキストから通知プリセットを一括作成・更新
  - 対応項目: `name`, `set_default`, `entry`
  - `entry` 書式: `event,offset,destination,target,mention`（`/kukai notify replace` と同じ書式）
  - 同名プリセットが存在する場合は上書き
- ギルドに既定プリセットが設定されている場合:
  - 句会作成ウィザード step 8 で自動適用される
- ウィザード step 8:
  - 通知プリセットがある場合、プリセット選択ドロップダウンが表示される
  - プリセットを選ぶと通知設定が一括ロードされる
  - 手動入力（「通知を入力」ボタン）と併用可能

## 12. 行形式一括コマンド
- 共通仕様:
  - `key=value` 形式
  - 空行と `#` で始まるコメント行は無視
  - bool は `true/false`, `on/off`, `yes/no`, `1/0` を受理
  - 日時は既存のJST入力形式（例: `2026-05-20 23:59`）
  - 無制限は `∞`, `unlimited`, `none`, `null` を受理
  - エラー時は原因行を表示
  - 通知先は `kukai`, `dm`, `mention`, `<#channel_id>` を受理
  - 通知先 `admin` は句会の管理者 private thread を表す
  - 通知対象は `all`, `incomplete`, `admin`
- `reminder` 書式:
  - `reminder=event,offset,destination,target,mention`
  - `event`: `entry_close`, `submission_close`, `selecting_close`, `voice_start`
  - `offset`: `24h`, `30m`, `1d6h` など
  - `destination=dm` は対象者へDM送信
  - `destination=mention` は句会チャンネルへ対象者mention付きで通知
  - `destination=admin` は管理者 private thread へ通知し、`target=incomplete` では未達状況を併記する
- `/kukai create` と `/kukai create-bulk`:
  - `/kukai create` は引数なしのGUIウィザード起動コマンド
  - CLI風に全項目を指定して作成する場合は `/kukai create-bulk config:` を使う
  - `config` には複数行の `key=value` テキストを貼り付ける
  - 必須項目:
    - `title`
    - `submission_close_at`
    - `selecting_close_at`
    - `entry_enabled=true` の場合は `entry_close_at` も必須
  - 主な任意項目とデフォルト:
    - `channel=current`（`current` / `new` / `<#channel_id>`）
    - `channel_visibility_policy=public`
    - `entry_enabled=true`
    - `entry_approval=false`
    - `min_participants=0`
    - `submission_min=1`
    - `submission_max=5`（`∞`, `unlimited`, `none`, `null` で無制限）
    - `submission_overflow=false`
    - `submission_mode=manual`（`manual` / `semi_auto` / `full_auto`）
    - `selecting_mode=manual`（`manual` / `semi_auto` / `full_auto`）
    - `publish_mode=manual`（`manual` / `auto`）
    - `result_mode=manual`（`manual` / `auto`）
    - `author_reveal=true`
    - `author_reveal_zero=true`
    - `voice_enabled=false`
  - `channel=new` の場合:
    - `channel_name` 未指定時は `title` からチャンネル名を生成
    - `category_id` で作成先カテゴリを指定可能
    - `channel_visibility_policy=public_until_participation_close` は `channel=new` の場合のみ指定可能
  - 選句ラベル:
    - `label=名前,点数,rank,最小数,最大数,コメントモード`
    - rank省略: `label=名前,点数,最小数,最大数,コメントモード`
    - `comment_mode` は `none` / `optional` / `required`
    - デフォルト選句ラベルの選評はすべて `optional`
    - `label=` がある場合は `preset_id` より優先
    - `label=` も `preset_id` も無い場合はデフォルト選句ラベルを使用
- `/select-preset bulk` 例:
```text
name=標準
points_enabled=true
set_default=true
label=特選,2,1,0,1,optional
label=並選,1,2,0,5,optional
label=予選,0,3,0,∞,optional
```
- `/kukai create` 例:
```text
/kukai create
```
- `/kukai create-bulk` 最小構成例:
```text
/kukai create-bulk config:
title=ミニ句会
theme=月
channel=current
entry_enabled=false
submission_close_at=2026-06-10 23:59
selecting_close_at=2026-06-12 23:59
```
- `/kukai create-bulk` プリセット使用例:
```text
/kukai create-bulk config:
title=夏の句会
theme=夕立
channel=new
channel_name=summer-kukai
category_id=123456789012345678
entry_enabled=false
submission_close_at=2026-07-10 23:59
selecting_close_at=2026-07-12 23:59
submission_min=1
submission_max=5
preset_id=1
reminder=submission_close,24h,kukai,all,false
reminder=selecting_close,1h,mention,incomplete,true
```
- `/kukai create-bulk` 全項目例:
```text
/kukai create-bulk config:
title=春の句会
theme=桜
description=春季定例句会です
channel=current
channel_visibility_policy=public_until_participation_close
entry_enabled=true
entry_approval=false
min_participants=0
entry_close_at=2026-06-01 23:59
submission_close_at=2026-06-03 23:59
selecting_close_at=2026-06-05 23:59
submission_min=1
submission_max=5
submission_overflow=false
submission_mode=manual
selecting_mode=manual
publish_mode=manual
result_mode=manual
author_reveal=true
author_reveal_zero=true
label=特選,2,1,0,1,optional
label=並選,1,2,0,5,optional
label=予選,0,3,0,∞,optional
voice_enabled=true
voice_channel=<#123456789012345678>
voice_start_at=2026-06-06 21:00
voice_end_at=2026-06-06 22:00
reminder=entry_close,24h,kukai,all,false
reminder=submission_close,24h,kukai,all,false
reminder=selecting_close,1h,mention,incomplete,true
reminder=voice_start,30m,dm,all,false
```
- `/select-bulk` 例:
```text
1=特選|景が鮮やかです
4=並選
7=clear
overall=全体に春らしい句が多かったです
```

## 13. 設定更新通知
- `/kukai edit` 実行後、開催チャンネルに設定更新通知を送信
- 変更内容を差分形式で表示（例: `最大投句数: 3 → 6`）
- 変更された項目のみ表示（非変更項目は非表示）
- 締切変更時は再スケジュール実施を明記
- 句会名変更時、開催チャンネル名が旧句会名由来の名前と一致している場合はチャンネル名も更新
- 編集対象:
  - 基本情報: `title`, `theme`, `description`
  - 締切: `entry_close_at`, `submission_open_at`, `submission_close_at`, `selecting_close_at`
  - エントリー設定: `entry_approval`, `entry_mode`, `min_participants`
  - 投句設定: `submission_min`, `submission_max`, `submission_max_unlimited`, `submission_overflow`, `submission_mode`
  - 選句・公開設定: `selecting_mode`, `publish_mode`, `result_mode`, `select_rule_config`
  - 作者公開設定: `author_publication_mode`, `author_reveal`, `author_reveal_zero`
- 作者公開設定:
  - `author_publication_mode=with_result` は作者公開済み扱い（`author_reveal=true`）にする
  - `author_publication_mode=manual` は作者未公開（`author_reveal=false`）に戻す
  - `author_publication_mode=never` は作者未公開に戻し、`author_reveal_zero=true` に補正する
  - `author_reveal=true` を明示した場合、現在 `never` なら `manual` に切り替えて作者公開済みにする
  - 既にDiscordへ投稿済みの過去メッセージは巻き戻さない。以後の結果表示は現在のDB設定に従う
- `select_rule_config`:
  - 他の編集項目と同時指定不可
  - `select_rule_config=gui` でモーダル入力を開く
  - モーダル/直接入力の本文は行形式（例: `preset_id=3` または複数の `label=...`）

## 14. 通知スケジュール整合
- ステージ進行時・設定更新時に通知ジョブを再評価
- 現在ステージより前イベントの通知はキャンセル（`fired=True` 扱い）
- 対象イベント:
  - `entry_close`
  - `submission_close`
  - `selecting_close`
  - `voice_start`
- `/kukai notify`:
  - `/kukai notify list` で登録済み通知を表示
  - `/kukai notify replace config` で通知設定を一括差し替え（`set` から改名）
  - `/kukai notify restore` でデフォルト通知へ戻す（`reset` から改名）
  - `replace` の `config` は1行1件の `event,offset,destination,target,mention`
  - 差し替え・リセット時は既存ジョブをキャンセルし、通知スケジュールを再登録
- `entry_close` 通知:
  - エントリー制句会では、通知Embedに参加者一覧を追加
  - 一覧は承認済と承認待ちのエントリーを対象に、俳号または表示名で表示
- 自動進行時の未達処理:
  - 半自動の投句/選句締切では、全員が条件を満たしている場合のみ自動進行する
  - 半自動で未達がある場合は状態を維持し、管理者スレッドへ未達者詳細、句会チャンネルへ個人を特定しない停止通知を送る
  - 全自動の投句/選句締切では、未達があっても進行し、管理者スレッドへ未達警告と「未達のまま進行した」記録を送る
  - 前段階が手動確認待ちで止まっている状態で次の自動期限が来た場合、管理者スレッドへ警告を送る
  - 投句締切から自動で選句受付へ進んだ場合、投句一覧投稿に加えて `選句する` ボタン付きの選句開始通知を送信する

## 15. コマンド同期
- 起動時:
  - グローバル同期
  - グローバル運用時はギルドスコープの同名コマンドを掃除（重複表示防止）

## 16. PDF生成

### コマンド
```
/pdf submission [kukai_id] [show_author] [theme] [public]
/pdf result     [kukai_id] [show_author] [theme] [public]
```

### アクセス制御
- 基本的に誰でも実行可能
- `public=True` 時はチャンネルへ投稿（句会管理者のみ）
- `public=False`（デフォルト）は自分だけに見える ephemeral 返信

### `show_author` の制限
- 投句一覧PDFでは、句会ステートが `results` または `ended` の場合のみ `show_author=True` が有効
- 投句一覧PDFでは、それ以前のステートでは `show_author` の指定に関わらず強制的に `False`（無記名）
- 句会の作者公開設定も反映する
  - `author_reveal=false` の場合は、投句一覧PDF・結果PDFとも作者非公開
  - `author_reveal=true` かつ `author_reveal_zero=false` の場合は、作者別合計点が0点以下の作者を非公開
  - `author_reveal=true` かつ `author_reveal_zero=true` の場合は、0点以下の作者も公開

### ファイル名
- 投句一覧: `submission_{kukai_id}_{named|anonymous}.pdf`
- 結果: `result_{kukai_id}_{named|anonymous}.pdf`

### 日付表示
- `submission_close_at`（投句締切）を優先し、なければ `entry_close_at`（参加締切）をJSTで表示
- defaultテーマでは、句会名中の回数などの連続数字と日付の連続数字を `\rensuji{...}` でまとめて縦中横表示する

### 参加者判定
- エントリー制あり:
  - `entry_approval=true` の場合は `approved` エントリーのみ
  - `entry_approval=false` の場合は `pending` / `approved` エントリー
- エントリー制なし:
  - `kukai_participants` に保存された投句・選句・総評参加者プロファイル
- 表示名は俳号を優先し、俳号がなければサーバー表示名、取得不能時は `UID:{user_id}` を使う

### テンプレートシステム
- テーマ単位で管理: `bot/templates/pdf/{theme}/`
- `theme.toml` にフォント・用紙設定を記述
- `.tex.j2` は Jinja2 テンプレート。`{{ var | tex }}` でユーザー入力をTeXエスケープ
- カスタム `.sty` ファイルは `bot/templates/pdf/` 直下に配置（コンパイル時に自動コピー）
- ユーザー入力中の絵文字は `tex` フィルタで `\emoji{...}` に包み、テンプレート側の絵文字フォントで描画する
- defaultテーマの絵文字フォントは `Noto Color Emoji` を優先し、存在しない場合は `Segoe UI Emoji` にフォールバックする
- Dockerイメージには `fonts-noto-color-emoji` を含める
- 投句一覧・結果とも、フッター中央に `現在ページ/総ページ` 形式のページ番号を表示する

### 投句一覧レイアウト（defaultテーマ）
- `ltjtarticle` クラス、A4横置き横書き
- `tabular` をページ単位で分割
- 列構成: №（1列）/ 選者（5列、空欄）/ 俳句（26列、`\kintou` 均等割り）/ 作者（5列）/ 予備（5列）
- ヘッダー: 句会名・兼題・日付・参加者一覧

### 結果レイアウト（defaultテーマ）
- `ltjtarticle` クラス、A4縦組み縦書き
- 順位・得点・投句番号・俳句本文・作者
- 各句の作者行の後に、ラベル別の得票サマリ（ラベル、点数、得票数、選者名）を全列挙する
- その後に選評本文をラベル別に表示する。選評本文前のラベル行はラベル名のみ表示する
- 総評は各句の選評とは混ぜず、結果一覧の末尾に独立した総評セクションとして表示する

### 環境変数
| 変数 | 説明 | デフォルト |
|------|------|----------|
| `LUALATEX_BIN` | LuaLaTeX実行パス（空で機能無効） | `lualatex` |
| `PDF_MAX_CONCURRENT` | 同時コンパイル数 | `2` |
| `PDF_COMPILE_TIMEOUT` | タイムアウト秒数 | `60` |
| `PDF_SERVE_BASE_URL` | 25MB超過時の公開URL | （空） |
| `PDF_SERVE_DIR` | 公開ディレクトリ | `/srv/pdfs` |

### 運用注意
- PDFフォントやTeX依存パッケージを変更した場合は、botコンテナの再起動だけでは反映されない。`docker compose up -d --build` または `docker compose build bot && docker compose up -d bot` でイメージを再ビルドする
- 既に生成済みのPDFは自動更新されない。変更後に `/pdf submission` または `/pdf result` を再実行して新しいPDFを生成する
