# Kukai Bot 現在仕様（技術者向け）

最終更新: 2026-05-20（5件改善）

## 1. 全体構成
- 実装言語: Python 3.11+（開発・テスト環境は Python 3.13）
- フレームワーク: `discord.py`（`app_commands` ベース）
- DB: SQLite（Alembic マイグレーション管理）
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
- 締切表示:
  - エントリー無効時はエントリー締切を非表示
- `/kukai create-bulk`:
  - 行形式テキストから句会を一括作成
  - GUIウィザードと併存し、同じ `kukai_service.create_kukai()` を使用
  - `preset_id` 指定時は選句プリセットを句会ラベルへ展開
  - `label=` 行がある場合は `preset_id` より優先
  - `channel=current/new/<#channel_id>` に対応
  - `voice_enabled`, `voice_channel`, `voice_start_at`, `voice_end_at` でボイス句会イベントを作成
  - `reminder=` 行で通知回ごとの時刻・通知先・対象・mention有無を指定
- GUIウィザード:
  - サーバー既定プリセットがある場合、開始時点で選句設定に自動適用
  - 選句設定ステップではプリセット選択に加えて、選句種別を直接入力可能
    - 書式: `名前,点数,rank,最小数,最大数,コメントモード`
    - 例: `特選,2,1,0,1,none`
    - 直接入力した場合は句会固有の「カスタム」選句仕様として保存
  - ボイス句会設定ステップでボイス/ステージチャンネル、開始日時、終了日時を設定
  - 通知設定ステップで `event,offset,destination,target,mention` 形式の通知を複数登録

## 4. 進行通知
- ステージ開始時に開催チャンネルへ Embed 通知
- 通知内容:
  - 句会名
  - 開始ステージ
  - 直近ステージに対応する締切のみ（例: 投句開始時は投句締切）
- 通知に操作ボタンを付与:
  - エントリーする / 投句する / 選句する
  - ボタンはコマンド案内ではなく実処理を実行（モーダル/GUI表示）

## 5. コマンドの `kukai_id` 省略
- 全コマンドで `kukai_id` は省略可能（`int | None = None`）
- 解決ルール:
  - `kukai_id` 指定時はそのIDを優先
  - 省略時は「同一チャンネルの進行中句会」を探索（`ENDED` / `CANCELLED` 除外）
  - 候補 0 件/複数件はエラーで明示
- スレッド対応:
  - スレッド内でコマンドを使用した場合、`interaction.channel_id`（スレッドID）ではなく親チャンネルIDを使って解決する
  - `bot/utils/channel.py` の `effective_channel_id()` で透過的に処理

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
- `/entry cancel`:
  - エントリー受付期間中のみ、自分のエントリーを取消
- 承認制:
  - `entry_approval=true` の句会では通常エントリーも `pending`
  - 管理者は `/entry approve` / `/entry reject` で承認・却下
- 締切後エントリー（状態ベース判定）:
  - **状態が `entry_closed` の場合のみ**「締切後エントリー」と判定する（時刻ベース判定は廃止）
  - `ENTRY_OPEN` 状態では、`entry_close_at` が過去であっても通常承認される
  - `entry_approval=false` でも `entry_closed` 状態での申請は `pending` として登録
  - 句会チャンネルへ句会作成者・追加管理者をメンションして通知
  - 承認された場合のみ、以後の投句・選句対象になる
- 承認通知:
  - `/entry approve user:...` および承認セレクトメニューの両方で送信
  - 句会チャンネルへ承認対象ユーザーだけをメンション
  - ロール・everyone は mention しない
- `/entry list`:
  - 管理者向け。`pending` / `approved` / `rejected` / `withdrawn` で絞り込み可能
- `/entry remove`:
  - 管理者向け。エントリー締切後にエントリーを削除
- `/kukai status`:
  - 句会管理者向け。句会内容そのものは表示せず、進捗だけを表示
  - エントリー者: 承認済/審査待ち/却下/取消をアイコン付きで表示
  - 投句状況: 承認済参加者ごとに投句数を表示し、必要投句数未満は `⚠️`
  - 選句状況: 承認済参加者ごとに選句ラベル別件数、コメント数、作者コメント数、総評有無を表示
  - 選句ラベルの `min_count` 未満は `⚠️` と不足ラベルを表示
  - エントリー制なしの場合は投句・選句・総評の記録があるユーザーを対象に表示

## 8. 投句UI
- `/submit` で投句GUIを表示
- 追加は一括モーダル方式（1回最大5句）
- 最大投句数を超えない入力枠数を自動調整
- `/submit-bulk`:
  - 1行1句で一括投句（句数上限は `kukai.submission_max` に従う。無制限設定なら上限なし）
  - 句会ID省略時は同一チャンネルの進行中句会から自動解決
  - 受付状態、参加承認、投句上限は `submission_service.submit()` と同じ制約
- 注記表示:
  - Embed本文とfooterに「GUIでは一度に5句まで」注記を表示（上限超過時/無制限時）
- 登録完了通知:
  - 投句登録後に「登録しました」通知を表示
  - 投句変更後に「変更しました」通知を表示
  - 投句削除後に「削除しました」通知を表示
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

## 10. 結果表示
- `/result`:
  - 表示切替UI（点数順 / 番号順 / 作者別）
  - 総評ページを同View内で表示
- 自動結果公開:
  - `kukai proceed` で `results` 到達時、および scheduler の自動進行時に、
    `/result` と同じ切替Viewをチャンネル送信
- 表記:
  - `pt` は `点` に統一
  - 作者コメントは結果表示に反映（番号順・作者別にも表示）
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
  - 通知対象は `all`, `incomplete`, `admin`
- `reminder` 書式:
  - `reminder=event,offset,destination,target,mention`
  - `event`: `entry_close`, `submission_close`, `selecting_close`, `voice_start`
  - `offset`: `24h`, `30m`, `1d6h` など
  - `destination=dm` は対象者へDM送信
  - `destination=mention` は句会チャンネルへ対象者mention付きで通知
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
    - `entry_enabled=true`
    - `entry_approval=false`
    - `min_participants=0`
    - `submission_min=1`
    - `submission_max=3`（`∞`, `unlimited`, `none`, `null` で無制限）
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
  - 選句ラベル:
    - `label=名前,点数,rank,最小数,最大数,コメントモード`
    - rank省略: `label=名前,点数,最小数,最大数,コメントモード`
    - `comment_mode` は `none` / `optional` / `required`
    - `label=` がある場合は `preset_id` より優先
    - `label=` も `preset_id` も無い場合はデフォルト選句ラベルを使用
- `/select-preset bulk` 例:
```text
name=標準
points_enabled=true
set_default=true
label=特選,2,1,0,1,none
label=並選,1,2,0,5,optional
label=予選,0,3,0,∞,none
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
submission_max=3
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
entry_enabled=true
entry_approval=false
min_participants=0
entry_close_at=2026-06-01 23:59
submission_close_at=2026-06-03 23:59
selecting_close_at=2026-06-05 23:59
submission_min=1
submission_max=3
submission_overflow=false
submission_mode=manual
selecting_mode=manual
publish_mode=manual
result_mode=manual
author_reveal=true
author_reveal_zero=true
label=特選,2,1,0,1,none
label=並選,1,2,0,5,optional
label=予選,0,3,0,∞,none
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
  - 一覧は承認済と審査待ちのエントリーを対象に、俳号または表示名で表示

## 15. コマンド同期
- 起動時:
  - グローバル同期
  - グローバル運用時はギルドスコープの同名コマンドを掃除（重複表示防止）
