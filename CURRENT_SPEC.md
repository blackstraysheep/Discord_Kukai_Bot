# Kukai Bot 現在仕様（技術者向け）

最終更新: 2026-05-16

## 1. 全体構成
- 実装言語: Python 3.11
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

## 3. 句会作成ウィザード
- ステップ:
  1. 基本情報
  2. エントリー設定
  3. 締切設定
  4. 投句設定
  5. 選句設定
  6. 公開・結果設定
  7. 確認
- 基本情報:
  - 新規チャンネル/既存チャンネル切替
  - 新規時はカテゴリ選択可
  - 新規時チャンネル名は別モーダルで設定可（未設定時は句会名）
- 締切表示:
  - エントリー無効時はエントリー締切を非表示
- `/kukai create_bulk`:
  - 行形式テキストから句会を一括作成
  - GUIウィザードと併存し、同じ `kukai_service.create_kukai()` を使用
  - `preset_id` 指定時は選句プリセットを句会ラベルへ展開
  - `label=` 行がある場合は `preset_id` より優先
  - `channel=current/new/<#channel_id>` に対応
  - `voice_enabled`, `voice_channel`, `voice_start_at`, `voice_end_at` でボイス句会イベントを作成
  - `reminder=` 行で通知回ごとの時刻・通知先・対象・mention有無を指定
- GUIウィザード:
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
- 省略対象:
  - `/entry join`, `/entry cancel`
  - `/submit`, `/submit_bulk`
  - `/select`, `/select_bulk`
  - `/check`
  - `/result`
- 解決ルール:
  - `kukai_id` 指定時はそのIDを優先
  - 省略時は「同一チャンネルの進行中句会」を探索
  - 候補 0 件/複数件はエラーで明示

## 6. 投句UI
- `/submit` で投句GUIを表示
- 追加は一括モーダル方式（1回最大5句）
- 最大投句数を超えない入力枠数を自動調整
- `/submit_bulk`:
  - 1行1句で最大20句まで一括投句
  - 句会ID省略時は同一チャンネルの進行中句会から自動解決
  - 受付状態、参加承認、投句上限は `submission_service.submit()` と同じ制約
- 注記表示:
  - Embed本文とfooterに「GUIでは一度に5句まで」注記を表示（上限超過時/無制限時）
- 登録完了通知:
  - 投句登録後に「登録しました」通知を表示
  - 現在登録済みの投句内容（抜粋）を併記

## 7. 選句UI
- 句プルダウンの末尾に `総評` を追加
- `総評` 選択時は選種別も総評モードへ切替
- 旧「総評ボタン」は廃止
- `/select_bulk`:
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

## 8. 結果表示
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

## 9. 選句プリセット
- `/preset list` / `/preset add` / `/preset label *` / `/preset gui` を提供
- `/preset bulk`:
  - 行形式テキストからプリセットを一括作成・更新
  - 対応項目: `name`, `points_enabled`, `set_default`, `label`
  - `label` 書式:
    - `label=名前,点数,rank,最小数,最大数,コメントモード`
    - rank省略: `label=名前,点数,最小数,最大数,コメントモード`
  - `rank` は小さいほど結果同点時の優先度が高い
  - `rank` 省略時はラベル定義順で自動採番
  - `作者コメント` はプリセット登録不可。句会ラベル展開時に `rank_priority=999` で補完
- プリセットJSON:
  - `points_enabled`
  - `labels[]`: `label`, `point`, `rank_priority`, `min_count`, `max_count`, `comment_mode`
  - 旧JSONに `rank_priority` が無い場合は読み込み時に定義順で補完

## 10. 行形式一括コマンド
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
- `/preset bulk` 例:
```text
name=標準
points_enabled=true
set_default=true
label=特選,2,1,0,1,none
label=並選,1,2,0,5,optional
label=予選,0,3,0,∞,none
```
- `/kukai create_bulk` 例:
```text
title=春の句会
theme=桜
channel=current
entry_enabled=false
submission_close_at=2026-05-20 23:59
selecting_close_at=2026-05-22 23:59
submission_min=1
submission_max=3
preset_id=1
voice_enabled=true
voice_channel=<#123456789012345678>
voice_start_at=2026-05-23 21:00
voice_end_at=2026-05-23 22:00
reminder=submission_close,24h,kukai,all,false
reminder=selecting_close,1h,mention,incomplete,true
reminder=voice_start,30m,dm,all,false
```
- `/select_bulk` 例:
```text
1=特選|景が鮮やかです
4=並選
7=clear
overall=全体に春らしい句が多かったです
```

## 11. 設定更新通知
- `/kukai edit` 実行後、開催チャンネルに設定更新通知を送信
- 変更内容を差分形式で表示（例: `最大投句数: 3 → 6`）
- 変更された項目のみ表示（非変更項目は非表示）
- 締切変更時は再スケジュール実施を明記

## 12. 通知スケジュール整合
- ステージ進行時・設定更新時に通知ジョブを再評価
- 現在ステージより前イベントの通知はキャンセル（`fired=True` 扱い）
- 対象イベント:
  - `entry_close`
  - `submission_close`
  - `selecting_close`

## 13. コマンド同期
- 起動時:
  - グローバル同期
  - グローバル運用時はギルドスコープの同名コマンドを掃除（重複表示防止）
