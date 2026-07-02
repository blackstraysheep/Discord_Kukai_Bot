# kukai_bot 実装進捗・引継ぎ資料

作成日: 2026-05-14

---

## 概要

Discord完結型句会管理Bot。Python 3.13 / discord.py 2.x / SQLAlchemy 2.x async / aiosqlite / APScheduler 3.x。

**現在の状態**: Phase 1〜10(品質強化) 完了 + コマンド体系再編済み + 追加改善5件済み。一括入力コマンド対応、句会情報表示改善、締切後エントリー承認フロー（状態ベース）、通知プリセット、通知管理コマンド、句会作成時の選句カスタム入力、管理者向け進捗確認、句会作成直後の受付開始を追加済み。テスト 96件すべてパス。

---

## 完了済みフェーズ

### Phase 1 — 基盤
- `pyproject.toml`, `Dockerfile`, `docker-compose.yml`
- `bot/settings.py` — pydantic-settings（BOT_TOKEN, DATABASE_URL, DATA_DIR, LOG_LEVEL, DEV_GUILD_IDS）
- `bot/database.py` — async engine, sessionmaker, `get_session()` コンテキストマネージャ
- `bot/models/` — 全15テーブル（Kukai, KukaiAdmin, SelectLabel, Entry, Submission, PublishedSubmission, Select, SelectComment, OverallSelectComment, NotificationSchedule, NotificationLog, NotificationPreset, GuildSettings, SelectRuleTemplate, VoiceSession）
- `alembic/versions/0001_initial.py` — 初期マイグレーション
- `alembic/versions/0007_notification_presets.py` — 通知プリセットテーブル追加
- `bot/main.py` — KukaiBot クラス、Cog一括ロード、APScheduler起動

### Phase 2 — State Machine + Kukai CRUD
- `bot/state_machine/states.py` — KukaiState（StrEnum, 13状態）
- `bot/state_machine/transitions.py` — FORWARD遷移表、`next_state(kukai)` でentry_enabled/publish_mode/result_mode考慮
- `bot/state_machine/machine.py` — StateMachine: `proceed()`, `pause()`, `resume()`, `cancel()`, `jump()`
- `bot/services/kukai_service.py` — create/get/list/proceed/pause/resume/cancel/jump
- `bot/repositories/kukai_repo.py` — is_admin など
- `bot/cogs/kukai_cog.py` — `/kukai list/info/create/proceed/pause/resume/cancel/publish/rollback/edit`
- `bot/utils/` — text.py, datetime_utils.py（JST↔UTC変換）, embed_builder.py
- `bot/services/permission_service.py` — can_create_kukai / is_kukai_admin
- **テスト**: `test_kukai_service.py`（8件）

### Phase 3 — エントリー
- `bot/repositories/entry_repo.py`
- `bot/services/entry_service.py` — enter/approve/reject/withdraw/list_entries
  - エントリー締切後は自動承認せず `pending` として登録し、管理者承認を必須化
- `bot/ui/entry_manage_view.py` — EntryManageView（承認/却下ボタン）
- `bot/cogs/entry_cog.py` — `/entry join/cancel/list/approve/reject/remove`
  - 締切後エントリー申請時は句会チャンネルで句会作成者・追加管理者へ通知
  - エントリー承認時は句会チャンネルで承認対象ユーザーだけに通知
- **テスト**: `test_entry_service.py`（15件）

### Phase 4 — 投句
- `bot/repositories/submission_repo.py`
- `bot/services/submission_service.py` — submit/edit/delete_submission/publish/rollback_publish
  - `publish()`: 未投句者のdiscard処理、PublishedSubmissionへのランダム番号割当
  - `rollback_publish()`: 公開取消・投句復元・選句リセット
- `bot/ui/submission_view.py` — SubmissionView（追加/編集/削除）, RollbackView
- `bot/cogs/submission_cog.py` — `/submit kukai_id`
- `/kukai publish`, `/kukai rollback` を kukai_cog.py に追加
- **テスト**: `test_submission_service.py`（15件）

### Phase 5 — 選句
- `bot/repositories/select_repo.py`
- `bot/services/select_service.py` — cast_select/remove_select/set_overall_comment/list_selects_for_selector
  - cast_select: 自句禁止・max_count制限・comment_mode対応・既存票の上書き（upsert）
- `bot/ui/select_view.py` — SelectView（1句ずつ表示、ラベルSelect、コメントModal、前後ナビ）
- `bot/cogs/select_cog.py` — `/select kukai_id`, `/select_bulk selections [kukai_id]`
- `bot/cogs/check_cog.py` — `/check kukai_id`（エントリー/投句/選句の状況一覧）
- **テスト**: `test_select_service.py`（11件）

### Phase 6 — 結果表示
- `bot/services/result_service.py` — compute_results()
  - SubmissionResult / LabelSelects データクラス
  - スコア降順ソート、タイブレーク（rank_priority順のラベル数比較）、同点同順位
- 選句プリセットの `rank_priority` を結果同点判定へ反映
- `bot/cogs/result_cog.py` — `/result kukai_id [format:score/number/author]`
  - RESULTS状態で公開（非ephemeral）、それ以前は管理者のみプレビュー（ephemeral）
  - 6000文字上限によるページ分割
- **テスト**: `test_result_service.py`（6件）

### Phase 7 — Scheduler + 通知
- `bot/scheduler/setup.py` — init_scheduler / get_scheduler / has_scheduler
- `bot/scheduler/jobs.py` — notification_job / deadline_job / _all_submitted / _all_selected / _notify_channel / _notify_admins
  - `notification_job(schedule_id)`: NotificationScheduleを取得しリマインダー送信
  - 通知回ごとに `channel_id` と `mention` を持ち、DM/チャンネル/mention付き通知を切替可能
  - `voice_start` 通知イベントに対応
  - `entry_close` 通知ではエントリー参加者一覧（承認済/承認待ち）をEmbedに表示
  - `deadline_job(kukai_id, event_type)`: manual/semi_auto/full_autoに応じた自動進行
- `bot/repositories/notification_repo.py`
- `bot/services/notification_service.py` — schedule_kukai_jobs / cancel_kukai_jobs
  - 句会作成時にデフォルトスケジュール（24h前通知 × 2）を自動生成
  - `replace_notification_schedules()` でカスタム通知を一括置換
  - APSchedulerの`date`トリガーでジョブ登録
- `bot/cogs/kukai_cog.py` 更新: create/pause/resume/cancelにスケジューラ連携、通知管理コマンドを `/kukai notify` サブグループとして統合
  - `/kukai notify list/replace/restore`（旧 `notification_cog.py` の `/notification list/set/reset`）
- `bot/main.py` 更新: setup_hookでinit_scheduler + set_bot + scheduler.start()

### Phase 8 — 句会作成ウィザード
- `bot/ui/wizard/wizard_state.py` — WizardState dataclass（TTL 15分）+ インメモリレジストリ（get/set/clear_wizard）
- `bot/ui/wizard/base.py` — goto_step() ディスパッチャ（STEP_COUNT=9）、cancel_wizard()
- `bot/ui/wizard/step_basic.py` — Step 1:句会名/題/説明（Modal）
- `bot/ui/wizard/step_entry.py` — Step 2: エントリー設定（Select × 2）
- `bot/ui/wizard/step_schedule.py` — Step 3: 投句締切/選句締切（Modal + JST検証）
- `bot/ui/wizard/step_submission.py` — Step 4: 投句設定（Select + 詳細Modalで最低/最大投句数）
- `bot/ui/wizard/step_select_rule.py` — Step 5: 選句プリセット選択、句会ごとの選句数・コメント設定、選句種別の直接入力
- `bot/ui/wizard/step_publish.py` — Step 6: 公開/結果/作者設定（Select × 3）
- `bot/ui/wizard/step_voice.py` — Step 7: ボイス句会の時間・場所設定
- `bot/ui/wizard/step_notify.py` — Step 8: 通知回ごとの時間・通知先・対象・mention設定
- `bot/ui/wizard/step_confirm.py` — Step 9: 確認・作成（チャンネル作成 → create_kukai → schedule_kukai_jobs）
- `bot/services/kukai_service.py` 更新: create_kukai()にウィザード設定パラメータ追加
- `/kukai create` をウィザード起動に差し替え（権限チェック後、step 1を送信）
  - サーバー既定プリセットがある場合はウィザード開始時に自動適用

### Phase 9 — Export / Import + /kukai edit + ギルド設定（コア実装）
- `bot/services/export_service.py` 追加
  - `export_payload()` — 句会設定/entries/submissions/published_submissions/selects/select_comments/overall_comments/results/通知情報をJSON化
  - `import_payload()` — エクスポートJSONを同一guildへ復元（ID再採番マッピング）
  - `payload_to_json()`, `payload_to_csv()` を実装
- `bot/cogs/admin_cog.py` 実装
  - `/guild settings` — create_role と role/user ID リストの確認・更新（このCogに残留）
  - 句会管理操作は `/kukai admin` サブグループへ移動済み（後述のコマンド再編参照）
- `bot/services/kukai_service.py` 拡張
  - `add_kukai_admin()`, `remove_kukai_admin()`
  - `edit_kukai()` — title/theme/description/締切/submission設定/publish_mode/result_mode/author_revealを更新
  - 状態制約: `SELECTING_OPEN` 以降は submission設定変更不可
- `bot/cogs/kukai_cog.py` 実装
  - `/kukai edit` を実装（権限チェック、JST日時パース、更新、締切変更時のジョブ再登録）

### 一括入力コマンド対応
- `bot/utils/bulk_parser.py` 追加
  - 行形式 `key=value` パーサ
  - bool / int / 無制限値 / JST日時 / 選句ラベル定義の正規化
  - `reminder=event,offset,destination,target,mention` の正規化
  - エラー時は原因行を明示
- `bot/cogs/preset_cog.py`
  - `/preset bulk config` を追加
  - `label=名前,点数,rank,最小数,最大数,コメントモード` に対応
  - `rank` 省略時は定義順で自動採番
- `bot/services/select_rule_service.py`
  - プリセットJSONに `rank_priority` を保存
  - 既存JSONの `rank_priority` 未設定データは読み込み時に補完
  - `作者コメント` は句会展開時に `rank_priority=999` 固定
- `bot/cogs/kukai_cog.py`
  - `/kukai create-bulk config` を追加（`create_bulk` → kebab-case）
  - `channel=current/new/<#channel_id>`、`preset_id`、`label=` に対応
  - `voice_enabled` / `voice_channel` / `voice_start_at` / `voice_end_at` に対応
  - `reminder=` による通知カスタマイズに対応
  - `/kukai info` は作成時案内と同等の基本情報、現在の状態、句数、各締切、ボイス句会情報を表示
  - `/kukai edit` で句会名を変更した際、チャンネル名が旧句会名由来ならチャンネル名も更新
- `bot/cogs/select_cog.py`
  - `/select-bulk selections [kukai_id]` を追加（`select_bulk` → kebab-case）
  - `番号=ラベル|コメント`, `overall=...`, `番号=clear` に対応
- `bot/scheduler/jobs.py`
  - 自動投句締切時に公開対象0件で `waiting_publish` に残らないよう補正
- `bot/ui/submission_view.py`
  - 投句の登録・変更・削除時に、登録時と同じ形式で現在の投句一覧を通知

### コマンド体系再編（2026-05-20）

**変更の動機**: コマンド名の一貫性欠如（snake_case混在、`_admin` 単発、グループ境界の不整合）を解消。

- **命名規則**: 複合語はすべて kebab-case 統一（`create_bulk` → `create-bulk` 等）
- **参加者コマンド**: `/submit`, `/select`, `/check`, `/result` はトップレベルで完結
- `/kukai list` → トップレベル `/list`, `/kukai info` → トップレベル `/info`
- `/kukai_admin` グループ解体 → `/kukai admin` サブグループ（`kukai_cog.py` 内）
  - `/kukai admin status|add|remove|export|import`
- `/kukai status` を追加（管理者向け進捗表示。admin サブグループではなく `/kukai` 直下）
- `/notification` グループ解体 → `/kukai notify` サブグループ（`kukai_cog.py` 内）
  - `/kukai notify list|replace|restore`（`set` → `replace`, `reset` → `restore`）
  - `notification_cog.py` は削除
- `/preset` → `/select-preset`（`/notify-preset` との対称性）
- 全管理コマンドで `kukai_id: int | None = None` + `resolve_kukai_in_channel` に統一
- スレッド内コマンド対応: `bot/utils/channel.py` の `effective_channel_id()` を全Cogに適用
- **権限ラベル**: `【管理者】` → `【句会管理者】` / `【作成権限者】` / `【サーバー管理者】`

### 追加改善（2026-05-20）

1. **submit-bulk 上限撤廃**: 固定20句制限を削除。`kukai.submission_max` のみで制御（無制限設定なら上限なし）。
2. **rank 変更**:
   - `bulk_parser.py` / `select_rule_service.py` の `min_value=1` / `rank < 1` 制約を削除し任意整数を受け入れ
   - ウィザード step5「選句種別を直接入力」を5フィールド書式（`名前,点数,最小,最大,コメント`）に変更。rank はリスト順で自動付番
3. **通知プリセット新規実装**:
   - `bot/models/notification_preset.py` — `NotificationPreset` モデル（JSON列でエントリ保存）
   - `bot/repositories/notification_preset_repo.py` — CRUD
   - `bot/services/notification_preset_service.py` — list/create/delete/set_default
   - `bot/cogs/notify_preset_cog.py` — `/notify-preset list|add|bulk|delete|set-default`
   - `alembic/versions/0007_notification_presets.py` — マイグレーション
   - ウィザード step8 にプリセット選択ドロップダウンを追加。既定プリセットがあれば自動適用
4. **締切後エントリー判定を状態ベースに変更**: `is_late_entry_request()` を `state == ENTRY_CLOSED` のみで判定。`entry_close_at` 時刻との比較を廃止。

---

## テスト状況

```
tests/test_bulk_parser.py          6件  ✅
tests/test_entry_service.py      15件  ✅
tests/test_kukai_info_embed.py    1件  ✅
tests/test_kukai_service.py       9件  ✅
tests/test_notification_phase10.py 6件 ✅
tests/test_phase9_services.py     5件  ✅
tests/test_preset_service.py      6件  ✅
tests/test_result_cog.py          5件  ✅
tests/test_result_service.py      7件  ✅
tests/test_select_rule_service.py 5件  ✅
tests/test_select_service.py      12件  ✅
tests/test_submission_service.py 19件  ✅
                              -------
合計                            96件  全パス
```

実行: `py -m pytest`

---

## ディレクトリ構成（現在）

```
kukai_bot/
├── alembic/
│   ├── env.py
│   └── versions/0001_initial.py
├── bot/
│   ├── main.py
│   ├── settings.py
│   ├── database.py
│   ├── models/
│   │   ├── base.py
│   │   ├── kukai.py          # Kukai, KukaiAdmin
│   │   ├── select_rule.py    # SelectLabel, SelectRuleTemplate
│   │   ├── entry.py
│   │   ├── submission.py     # Submission, PublishedSubmission
│   │   ├── select.py         # Select, SelectComment, OverallSelectComment
│   │   ├── notification.py   # NotificationSchedule, NotificationLog
│   │   ├── guild_settings.py
│   │   └── voice_session.py
│   ├── repositories/
│   │   ├── kukai_repo.py
│   │   ├── entry_repo.py
│   │   ├── submission_repo.py
│   │   ├── select_repo.py
│   │   └── notification_repo.py
│   ├── services/
│   │   ├── errors.py
│   │   ├── kukai_service.py
│   │   ├── entry_service.py
│   │   ├── submission_service.py
│   │   ├── select_service.py
│   │   ├── result_service.py
│   │   ├── notification_service.py
│   │   └── permission_service.py
│   ├── state_machine/
│   │   ├── states.py
│   │   ├── transitions.py
│   │   └── machine.py
│   ├── scheduler/
│   │   ├── setup.py
│   │   └── jobs.py
│   ├── cogs/
│   │   ├── kukai_cog.py         # /kukai *, /kukai admin *, /kukai notify *, /list, /info
│   │   ├── entry_cog.py         # /entry *
│   │   ├── preset_cog.py        # /select-preset *
│   │   ├── notify_preset_cog.py # /notify-preset *
│   │   ├── submission_cog.py    # /submit, /submit-bulk
│   │   ├── select_cog.py        # /select, /select-bulk
│   │   ├── check_cog.py         # /check
│   │   ├── result_cog.py        # /result
│   │   └── admin_cog.py         # /guild settings
│   ├── ui/
│   │   ├── common.py         # ConfirmView, PaginatedEmbed, error_embed
│   │   ├── submission_view.py
│   │   ├── select_view.py
│   │   ├── entry_manage_view.py
│   │   └── wizard/
│   │       ├── wizard_state.py
│   │       ├── base.py
│   │       ├── step_basic.py
│   │       ├── step_schedule.py
│   │       ├── step_entry.py
│   │       ├── step_submission.py
│   │       ├── step_publish.py
│   │       ├── step_confirm.py
│   │       ├── step_select_rule.py
│   │       ├── step_publish.py
│   │       ├── step_voice.py
│   │       ├── step_notify.py
│   │       └── step_confirm.py
│   └── utils/
│       ├── bulk_parser.py
│       ├── channel.py        # effective_channel_id() スレッド対応ヘルパー
│       ├── text.py
│       ├── datetime_utils.py
│       ├── discord_retry.py
│       ├── entry_notifications.py
│       └── embed_builder.py
└── tests/
    ├── conftest.py
    ├── test_bulk_parser.py
    ├── test_kukai_service.py
    ├── test_kukai_info_embed.py
    ├── test_entry_service.py
    ├── test_submission_service.py
    ├── test_select_service.py
    ├── test_result_service.py
    ├── test_preset_service.py
    ├── test_select_rule_service.py
    ├── test_result_cog.py
    ├── test_phase9_services.py
    └── test_notification_phase10.py
```

---

## 残タスク

### Phase 9 — 追加改善（任意）
- `/kukai edit` の更新対象をさらに拡張
  - entry関連の時刻・設定項目や submission_overflow/underflow など
- `/kukai_admin import_data` の運用強化
  - dry-run検証、部分インポート、重複検出レポート
- `/kukai_admin export` のCSV改善
  - 分析向けのテーブル別CSV出力（現在は汎用フラット形式）

---

### Phase 10 — 仕上げ

**統合テスト・品質**
- Discord UIのE2Eテストは手動確認（pytest-asyncioの範囲外）
- サービス層のテストカバレッジ補強 ✅
  - notification_service のジョブ登録/解除テスト（APSchedulerモック）を追加
  - deadline_job の自動進行ロジック（full_auto / semi_auto不足時通知）テストを追加
  - `_notify_admins` のDM失敗時チャンネルフォールバックテストを追加
- エラーハンドリングの網羅確認
  - ServiceErrorのすべてのサブクラスがCogでキャッチされる構成を確認
  - Discordの権限不足（Forbidden）エラーのフォールバック ✅（`/kukai publish` / `/result` / scheduler通知）

**未実装/将来拡張**
- `bot/models/voice_session.py` — 句会イベントの時間・場所保存は実装済み。ボイス入退室ログやVC内進行支援は未実装。

**Docker検証**
- `docker compose up --build` 実行確認 ✅（ビルド・コンテナ起動を確認）
- Bot本体は `.env` のダミー `BOT_TOKEN` で `401 Unauthorized`（実トークン設定が必要）
- `alembic upgrade head` 実行確認 ✅
- 環境変数 `.env` の設定確認（BOT_TOKEN, DATABASE_URL）✅（`.env.example` から作成）

**レート制限対策**
- 大量のフォローアップ送信時のsleep ✅（`/result` の複数ページ送信）
- embed送信失敗時のリトライ ✅（`bot/utils/discord_retry.py` を scheduler/公開投稿へ適用）

---

## 主要な設計上の注意点（引継ぎ用）

### SQLAlchemy async でのlazyload禁止
async環境ではリレーション属性への暗黙アクセスが `MissingGreenlet` を起こす。
- ロード時は必ず `selectinload()` を使う
- `session.refresh(obj, attribute_names=["relation"])` で明示的にリフレッシュ
- サービス層でオブジェクトを返す前に必要なリレーションをすべて事前ロードする

### Discord Interaction の応答ルール
- 1 Interactionに対して Response は1回のみ（その後は Followup）
- Modal の on_submit から `interaction.response.edit_message()` は有効（modal_submitタイプで動作する）
- Modal の on_submit から `interaction.response.send_message(ephemeral=True)` でエラー表示もOK
- ウィザードのNavigationはすべて `edit_message` を使う（新しいephemeralを増やさない）

### APSchedulerのジョブ関数
- `bot/scheduler/jobs.py` のトップレベル関数として定義（APSchedulerのpersistenceのため）
- `_bot` グローバル変数にBotをセット（`set_bot(bot)` を startup時に呼ぶ）
- ジョブ内では必ず `await _bot.wait_until_ready()` してからDiscord操作
- `has_scheduler()` でテスト時のフォールバック（テストはスケジューラ未使用）

### テストの構造
- `tests/conftest.py` — in-memory aiosqlite + 全テーブル作成 + `db_session` fixture
- サービス層のみテスト（Discord型は使わない）
- `pytest-asyncio` の `mode=AUTO` を使用（`@pytest.mark.asyncio` 不要）
- ただし各テストに `@pytest.mark.asyncio` が明示されている（互換性のため残存）

---

## 動作確認手順

```bash
# 1. 環境準備
cp .env.example .env
# .envに BOT_TOKEN を設定

# 2. Docker起動
docker compose up

# 3. または直接起動（開発時）
pip install -e .
alembic upgrade head
python -m bot.main

# 4. テスト実行
py -m pytest tests/ -v

# 5. Discord動作確認順序
/kukai create          → ウィザード（9ステップ）で句会作成
  Step 5でプリセット選択、選句数・コメント設定、または選句種別の直接入力が可能
/kukai create-bulk ... → 行形式で句会を一括作成
  voice_enabled=true / voice_channel=<#...> / reminder=... でボイス句会・通知も設定可能
/kukai notify list     → 句会の通知設定を確認
/kukai notify replace  → event,offset,destination,target,mention 形式で通知を差し替え
/kukai notify restore  → 通知をデフォルト（投句・選句24時間前）へ戻す
/kukai status          → 句会管理者向けにエントリー・投句・選句状況を確認
/list                  → 作成した句会を確認
/kukai proceed ...     → 状態を進める
/info ...              → 句会情報（現在の状態・締切・ボイス句会情報など）を確認
/entry join ...        → エントリー（entry_enabled=trueの場合）
  エントリー締切後は承認待ちになり、句会管理者へ通知
  承認時は句会チャンネルで承認対象ユーザーだけに通知
/submit ...            → 投句
/submit-bulk ...       → 複数行を一括投句
/kukai publish ...     → 投句公開（publish_mode=manualの場合）
/select ...            → 選句
/select-bulk ...       → 番号=ラベル|コメント 形式で一括選句
/kukai proceed ...     → 選句締切→結果へ
/result ...            → 結果表示
```
