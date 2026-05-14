# kukai_bot 実装進捗・引継ぎ資料

作成日: 2026-05-14

---

## 概要

Discord完結型句会管理Bot。Python 3.13 / discord.py 2.x / SQLAlchemy 2.x async / aiosqlite / APScheduler 3.x。

**現在の状態**: Phase 1〜10(品質強化の一部) 完了。テスト 60件すべてパス。

---

## 完了済みフェーズ

### Phase 1 — 基盤
- `pyproject.toml`, `Dockerfile`, `docker-compose.yml`
- `bot/settings.py` — pydantic-settings（BOT_TOKEN, DATABASE_URL, DATA_DIR, LOG_LEVEL, DEV_GUILD_IDS）
- `bot/database.py` — async engine, sessionmaker, `get_session()` コンテキストマネージャ
- `bot/models/` — 全14テーブル（Kukai, KukaiAdmin, VoteLabel, Entry, Submission, PublishedSubmission, Vote, VoteComment, OverallComment, NotificationSchedule, NotificationLog, GuildSettings, VoteRuleTemplate, VoiceSession）
- `alembic/versions/0001_initial.py` — 初期マイグレーション
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
- `bot/services/entry_service.py` — apply/approve/reject/withdraw/list_entries
- `bot/ui/entry_manage_view.py` — EntryManageView（承認/却下ボタン）
- `bot/cogs/entry_cog.py` — `/entry apply/list/approve/reject/withdraw`
- **テスト**: `test_entry_service.py`（12件）

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
- `bot/repositories/vote_repo.py`
- `bot/services/vote_service.py` — cast_vote/remove_vote/set_overall_comment/list_votes_for_voter
  - cast_vote: 自句禁止・max_count制限・comment_mode対応・既存票の上書き（upsert）
- `bot/ui/vote_view.py` — VoteView（1句ずつ表示、ラベルSelect、コメントModal、前後ナビ）
- `bot/cogs/vote_cog.py` — `/select kukai_id`
- `bot/cogs/check_cog.py` — `/check kukai_id`（エントリー/投句/選句の状況一覧）
- **テスト**: `test_vote_service.py`（11件）

### Phase 6 — 結果表示
- `bot/services/result_service.py` — compute_results()
  - SubmissionResult / LabelVotes データクラス
  - スコア降順ソート、タイブレーク（rank_priority順のラベル数比較）、同点同順位
- `bot/cogs/result_cog.py` — `/result kukai_id [format:score/number/author]`
  - RESULTS状態で公開（非ephemeral）、それ以前は管理者のみプレビュー（ephemeral）
  - 6000文字上限によるページ分割
- **テスト**: `test_result_service.py`（6件）

### Phase 7 — Scheduler + 通知
- `bot/scheduler/setup.py` — init_scheduler / get_scheduler / has_scheduler
- `bot/scheduler/jobs.py` — notification_job / deadline_job / _all_submitted / _all_voted / _notify_channel / _notify_admins
  - `notification_job(schedule_id)`: NotificationScheduleを取得しリマインダー送信
  - `deadline_job(kukai_id, event_type)`: manual/semi_auto/full_autoに応じた自動進行
- `bot/repositories/notification_repo.py`
- `bot/services/notification_service.py` — schedule_kukai_jobs / cancel_kukai_jobs
  - 句会作成時にデフォルトスケジュール（24h前通知 × 2）を自動生成
  - APSchedulerの`date`トリガーでジョブ登録
- `bot/main.py` 更新: setup_hookでinit_scheduler + set_bot + scheduler.start()
- `bot/cogs/kukai_cog.py` 更新: create/pause/resume/cancelにスケジューラ連携

### Phase 8 — 句会作成ウィザード
- `bot/ui/wizard/wizard_state.py` — WizardState dataclass（TTL 15分）+ インメモリレジストリ（get/set/clear_wizard）
- `bot/ui/wizard/base.py` — goto_step() ディスパッチャ（STEP_COUNT=6）、cancel_wizard()
- `bot/ui/wizard/step_basic.py` — Step 1: 題名/題/説明（Modal）
- `bot/ui/wizard/step_schedule.py` — Step 2: 投句締切/選句締切（Modal + JST検証）
- `bot/ui/wizard/step_entry.py` — Step 3: エントリー設定（Select × 2）
- `bot/ui/wizard/step_submission.py` — Step 4: 投句設定（Select + 詳細Modalで最低/最大投句数）
- `bot/ui/wizard/step_publish.py` — Step 5: 公開/結果/作者設定（Select × 3）
- `bot/ui/wizard/step_confirm.py` — Step 6: 確認・作成（チャンネル作成 → create_kukai → schedule_kukai_jobs）
- `bot/ui/wizard/step_vote_rule.py` / `step_notify.py` / `step_voice.py` — 将来用スタブ
- `bot/services/kukai_service.py` 更新: create_kukai()にウィザード設定パラメータ追加
- `/kukai create` をウィザード起動に差し替え（権限チェック後、step 1を送信）

### Phase 9 — Export / Import + /kukai edit + ギルド設定（コア実装）
- `bot/services/export_service.py` 追加
  - `export_payload()` — 句会設定/entries/submissions/published_submissions/votes/vote_comments/overall_comments/results/通知情報をJSON化
  - `import_payload()` — エクスポートJSONを同一guildへ復元（ID再採番マッピング）
  - `payload_to_json()`, `payload_to_csv()` を実装
- `bot/cogs/admin_cog.py` 実装
  - `/kukai_admin export` — JSON/CSVをDM送信
  - `/kukai_admin import_data` — JSON添付から復元
  - `/kukai_admin add_admin`, `/kukai_admin remove_admin`
  - `/guild settings` — create_role と role/user ID リストの確認・更新
- `bot/services/kukai_service.py` 拡張
  - `add_kukai_admin()`, `remove_kukai_admin()`
  - `edit_kukai()` — title/theme/description/締切/submission設定/publish_mode/result_mode/author_revealを更新
  - 状態制約: `VOTING_OPEN` 以降は submission設定変更不可
- `bot/cogs/kukai_cog.py` 実装
  - `/kukai edit` を実装（権限チェック、JST日時パース、更新、締切変更時のジョブ再登録）

---

## テスト状況

```
tests/test_kukai_service.py       8件  ✅
tests/test_entry_service.py      12件  ✅
tests/test_submission_service.py 15件  ✅
tests/test_vote_service.py       11件  ✅
tests/test_result_service.py      6件  ✅
tests/test_phase9_services.py     4件  ✅
tests/test_notification_phase10.py 4件 ✅
                              -------
合計                            60件  全パス
```

実行: `py -m pytest tests/ -v`

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
│   │   ├── vote_rule.py      # VoteLabel, VoteRuleTemplate
│   │   ├── entry.py
│   │   ├── submission.py     # Submission, PublishedSubmission
│   │   ├── vote.py           # Vote, VoteComment, OverallComment
│   │   ├── notification.py   # NotificationSchedule, NotificationLog
│   │   ├── guild_settings.py
│   │   └── voice_session.py
│   ├── repositories/
│   │   ├── kukai_repo.py
│   │   ├── entry_repo.py
│   │   ├── submission_repo.py
│   │   ├── vote_repo.py
│   │   └── notification_repo.py
│   ├── services/
│   │   ├── errors.py
│   │   ├── kukai_service.py
│   │   ├── entry_service.py
│   │   ├── submission_service.py
│   │   ├── vote_service.py
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
│   │   ├── kukai_cog.py      # /kukai *
│   │   ├── entry_cog.py      # /entry *
│   │   ├── submission_cog.py # /submit
│   │   ├── vote_cog.py       # /select
│   │   ├── check_cog.py      # /check
│   │   ├── result_cog.py     # /result
│   │   └── admin_cog.py      # スタブ
│   ├── ui/
│   │   ├── common.py         # ConfirmView, PaginatedEmbed, error_embed
│   │   ├── submission_view.py
│   │   ├── vote_view.py
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
│   │       ├── step_vote_rule.py  # スタブ
│   │       ├── step_notify.py     # スタブ
│   │       └── step_voice.py      # スタブ
│   └── utils/
│       ├── text.py
│       ├── datetime_utils.py
│       └── embed_builder.py
└── tests/
    ├── conftest.py
    ├── test_kukai_service.py
    ├── test_entry_service.py
    ├── test_submission_service.py
    ├── test_vote_service.py
    └── test_result_service.py
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
- サービス層のテストカバレッジ補強 ✅（一部完了）
  - notification_service のジョブ登録/解除テスト（APSchedulerモック）を追加
  - deadline_job の自動進行ロジック（full_auto / semi_auto不足時通知）テストを追加
- エラーハンドリングの網羅確認
  - ServiceErrorのすべてのサブクラスがCogでキャッチされているか
  - Discordの権限不足（Forbidden）エラーのフォールバック ✅（`/kukai publish` の公開投稿失敗時）

**未実装のスタブ**
- `bot/ui/wizard/step_vote_rule.py` — ウィザード内での投票ラベルカスタマイズ
- `bot/ui/wizard/step_notify.py` — ウィザード内での通知チャンネル設定
- `bot/ui/wizard/step_voice.py` — ボイスセッション連携（VoiceSessionモデルはあるが未活用）
- `bot/models/voice_session.py` — VoiceSessionモデルは存在するがCog/Serviceが未実装

**Docker検証**
- `docker compose up` でBot起動確認
- `alembic upgrade head` 実行確認
- 環境変数 `.env` の設定確認（BOT_TOKEN, DATABASE_URL）

**レート制限対策**
- 大量のフォローアップ送信時のsleep
- embed送信失敗時のリトライ

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
/kukai create          → ウィザード（6ステップ）で句会作成
/kukai list            → 作成した句会を確認
/kukai proceed ...     → 状態を進める
/entry apply ...       → エントリー（entry_enabled=trueの場合）
/submit ...            → 投句
/kukai publish ...     → 投句公開（publish_mode=manualの場合）
/select ...            → 選句
/kukai proceed ...     → 選句締切→結果へ
/result ...            → 結果表示
```
