# Kukai Bot 技術者向け仕様書

最終更新: 2026-06-22

## 1. 目的

Kukai Bot は、Discord 上で句会の募集、投句、選句、結果公開、PDF出力までを完結させる Bot である。

この文書は、開発者・運用者が実装構造、主要な状態遷移、データ境界、運用上の注意点を把握するための入口として使う。現在仕様の詳細は `CURRENT_SPEC.md`、本番運用手順は `docs/operations.md` を参照する。

## 2. 技術スタック

| 項目 | 内容 |
|------|------|
| 言語 | Python 3.11+ |
| Discord | `discord.py` / `app_commands` |
| DB | PostgreSQL |
| Migration | Alembic |
| Scheduler | APScheduler |
| PDF | LuaLaTeX / Jinja2 template |
| コンテナ | Docker Compose |
| テスト | pytest / pytest-asyncio |

ローカル単体テストでは in-memory SQLite を使用する。本番環境では Docker Compose 内の PostgreSQL を使用する。

## 3. アーキテクチャ

主要レイヤは以下の通り。

| レイヤ | 役割 | 主な配置 |
|--------|------|----------|
| Cogs | Slash command、Discord interaction の入口 | `bot/cogs/` |
| UI | View、Button、Select、Modal | `bot/ui/` |
| Services | 業務ロジック、検証、状態変更 | `bot/services/` |
| Repositories | DBアクセス | `bot/repositories/` |
| Models | SQLAlchemy model | `bot/models/` |
| State Machine | 句会状態と遷移 | `bot/state_machine/` |
| Scheduler | 締切・通知ジョブ | `bot/scheduler/` |
| Templates | PDFテンプレート | `bot/templates/` |

原則として、Discord 固有の応答処理は Cogs/UI に閉じ込め、句会の成立判定、受付可否、状態遷移、通知対象の判定は Services に置く。

## 4. 主要データ

### Kukai

句会本体。タイトル、題、説明、チャンネル、状態、締切、進行モード、作者公開設定などを保持する。

主な関連データ:

- Entry: 参加申請、俳号、承認状態
- Submission: 投句
- Select / SelectComment: 選句、選評、総評、作者コメント
- SelectRule: 句会ごとの選句ラベル定義
- Notification / NotificationPreset: 通知設定
- VoiceSession: ボイス句会予定
- Participant: エントリー制なし句会の参加者プロファイル

### ID解決

多くのコマンドは `kukai_id` を省略できる。省略時は、実行チャンネルまたはスレッド親チャンネルの進行中句会を探索する。候補が 0 件または複数件の場合は明示エラーにする。

## 5. 状態遷移

主な状態は以下。

```text
draft
entry_open
entry_closed
submission_open
submission_closed
waiting_publish
selecting_open
selecting_closed
results
ended
paused
cancelled
```

通常フローでは `waiting_results` は使わず、選句締切後は `results` へ進む。句会作成直後は、エントリー制ありなら `entry_open`、エントリー制なしなら `submission_open` を初期状態とする。

状態遷移の定義は `bot/state_machine/transitions.py` に置く。手動進行と scheduler 自動進行は、可能な限り同じサービス処理を通す。

## 6. Discord UI制約

Kukai Bot は Discord 上で動くため、UIは Discord が提供する要素に限定される。

実現できるもの:

- Slash command
- Embed
- Button
- Select menu
- Modal
- Ephemeral response
- Persistent view

実現できないもの:

- HTML/CSS のような自由な画面レイアウト
- Embed 内に埋め込まれたボタン
- 独自フォント、細かな余白、複雑なカラムレイアウト
- Webアプリのような常時更新ダッシュボード

HPや説明資料では、実際の Discord UI として誤解される架空の画面を使わない。

## 7. 句会作成

句会作成は2系統ある。

| 方法 | 用途 |
|------|------|
| `/kukai create` | GUIウィザードで対話的に作成 |
| `/kukai create-bulk` | `key=value` 形式で一括作成 |

どちらも最終的には `kukai_service.create_kukai()` を使う。サーバー既定の選句プリセットや通知プリセットがある場合は、ウィザード開始時に初期値として適用する。

必須項目の基本は、句会名、投句締切、選句締切である。エントリー制を有効にする場合はエントリー締切も必要になる。

## 8. エントリー

エントリー制ありの句会では、参加者は `/entry join` または公開ボタンから参加する。俳号未入力時は Discord の表示名を使う。

承認制ありの場合は `pending` として登録し、管理者が承認する。承認・却下通知は、原則としてメンションなし、俳号優先の表示にする。

締切後エントリーは時刻ではなく状態で判定する。`entry_closed` 状態での申請のみ締切後エントリーとして扱う。

## 9. 投句

投句は `/submit` のGUI、または `/submit-bulk` の一括入力で行う。受付可否、参加承認、投句数上限は service 層で検証する。

投句内容は公開前まで参加者本人だけが編集できる。公開時に投句番号を割り当て、選句対象にする。

## 10. 選句

選句は `/select` のGUI、または `/select-bulk` の一括入力で行う。

選句ラベルは句会ごとに定義される。ラベルごとに点数、同点時の優先度、最小数、最大数、コメント必須/任意/なしを持つ。

自句への通常選句は不可。ただし作者コメントは自句に対してのみ登録できる。

総評は句会全体に対するコメントとして扱い、各句の選評とは分けて表示する。

## 11. 結果

結果表示は以下の切替を提供する。

- 点数順
- 番号順
- 作者別

作者名、選評者名、総評者名は俳号を優先する。点数表記は `点` に統一する。

作者公開設定により、作者を公開しない、結果公開時に公開する、0点以下の作者を伏せる、といった制御を行う。

## 12. 通知と自動進行

通知対象イベントは主に以下。

- `entry_close`
- `submission_close`
- `selecting_close`
- `voice_start`

通知先は句会チャンネル、DM、対象者mention、管理者スレッド、指定チャンネルを扱う。

半自動モードでは、条件未達がある場合は状態を維持し、管理者に判断を求める。全自動モードでは、未達があっても進行し、管理者スレッドに記録する。

手動 `/kukai proceed` でも未達者がいる場合は警告を出し、実行者が明示確認した場合だけ進行する。

## 13. PDF

PDFは `/pdf submission` と `/pdf result` で生成する。

投句一覧PDFは、結果公開前は作者非公開を強制する。結果PDFと投句一覧PDFの作者表示は、句会の作者公開設定に従う。

PDFテンプレートは `bot/templates/pdf/{theme}/` に置く。ユーザー入力は TeX エスケープして出力する。

## 14. 運用

本番は OCI A1 上の Docker Compose で稼働する。DB は PostgreSQL コンテナ、Bot は同一 Compose ネットワーク内で接続する。

運用手順、バックアップ、更新、復旧、日次確認は `docs/operations.md` を正とする。

開発時は以下を基本にする。

```powershell
py -m pytest
```

Docker または本番相当の確認が必要な場合は、`.env.test` と `docker-compose.test.yml` を使い、本番 Bot token や本番DBを流用しない。

## 15. 関連文書

- `CURRENT_SPEC.md`: 詳細な現行仕様
- `docs/USER_MANUAL.md`: ユーザ向け取扱説明書
- `docs/operations.md`: 本番運用手順
- `FUTURE_IMPROVEMENTS.md`: 未実装・将来改善
- `docs/index.html`: Kukai Bot HP
