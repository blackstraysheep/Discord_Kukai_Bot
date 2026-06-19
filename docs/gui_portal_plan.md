# GUIポータル・管理パネル実装計画

## 目的

現在は `/kukai create` の作成ウィザード、投句GUI、選句GUI、結果表示GUIなど最低限のGUIはあるが、以下の導線がコマンド依存になっている。

- サーバー横断の句会作成・句会一覧・自分の参加状況確認
- 句会管理者向けの進行、停止、再開、中止、巻き戻し、通知設定、操作ボタン再投稿
- 常駐導線がないため、ユーザーが毎回スラッシュコマンドを知っている前提になっている

v1では、GUIを次の2系統に分けて実装する。

- サーバーハブ: Botが作成する専用チャンネルに、一般利用者向けの常駐ボタンを置く
- 句会管理パネル: 各句会の管理者 private thread に、管理者向けの常駐ボタンを置く

既存コマンドは削除しない。GUIは既存コマンドの代替入口として追加する。

## 実装範囲

### v1でGUI化するもの

サーバーハブに置く操作:

- 句会作成
  - 既存 `/kukai create` と同じ作成ウィザードを開く
  - 作成権限は `permission_service.can_create_kukai()` を使う
- 句会一覧
  - 既存 `/kukai list` 相当の一覧を ephemeral で表示する
  - 各句会の開催チャンネルへ移動しやすい表示にする
- 自分の状況確認
  - 既存 `/check` 相当の表示を行う
  - ハブチャンネルでは句会を一意解決できないため、進行中句会が複数ある場合は句会選択セレクトを出す

句会管理パネルに置く操作:

- `proceed`
- `pause`
- `resume`
- `cancel`
- `rollback`
- `reveal-authors`
- 現在ステージに応じた操作ボタン再投稿
- 通知設定の表示
- 通知設定のデフォルト復元
- 通知設定の行形式差し替え
- 通常運用向けの設定編集
  - タイトル
  - 題
  - 説明
  - 各締切
  - 進行モード
  - 投句数
  - 作者公開設定

### v1ではコマンド専用のまま残すもの

以下は大量入力、移行、復旧、高度編集の性質が強いためv1のGUI化対象外にする。

- `/kukai create-bulk`
- `/submit-bulk`
- `/select-bulk`
- `/select-preset bulk`
- `/notify-preset bulk`
- `/kukai admin export`
- `/kukai admin import`
- `/kukai edit select_rule_config` の高度な差し替え

## データモデル

`GuildSettings` にサーバーハブチャンネルIDを追加する。

```python
portal_channel_id: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)
```

Alembic migration を追加する。

- upgrade: `guild_settings.portal_channel_id` を nullable BigInteger で追加
- downgrade: 同カラムを削除

既存の句会管理者 private thread は `kukais.admin_thread_id` を使う。新しいDBカラムは追加しない。

## コマンド追加

### `/guild portal setup`

サーバー管理者専用。

処理:

1. `interaction.user` がサーバーownerまたはadministratorか確認する
2. `GuildSettings` を取得または作成する
3. `portal_channel_id` があり、チャンネルが存在する場合は再利用する
4. ない場合は Bot がテキストチャンネルを作成する
   - 推奨名: `句会案内`
   - 作成先カテゴリはv1では指定しない
5. `portal_channel_id` を保存する
6. サーバーハブメッセージを投稿する
7. 成功メッセージを ephemeral で返す

### `/guild portal repost`

サーバー管理者専用。

処理:

1. `GuildSettings.portal_channel_id` を読む
2. 対象チャンネルが存在しなければエラー
3. サーバーハブメッセージを再投稿する
4. 成功メッセージを ephemeral で返す

### `/kukai panel [kukai_id]`

句会管理者専用。

処理:

1. 既存の `resolve_kukai_in_channel()` で対象句会を解決する
2. `permission_service.is_kukai_admin()` で権限確認する
3. `admin_notice_service.ensure_admin_thread()` で管理者 thread を作成または取得する
4. 管理者 thread に `管理パネルを開く` 常駐ボタンを投稿する
5. 成功メッセージを ephemeral で返す

句会作成完了時にも同じ処理で管理者 thread に管理パネル入口を投稿する。

## UI構成

### `bot/ui/portal_view.py`

追加する主な要素:

- `PortalView`
  - persistent view
  - `timeout=None`
  - ボタン:
    - `句会を作成`
    - `句会一覧`
    - `自分の状況`
- `KukaiSelectForCheckView`
  - `/check` 相当の対象句会を選ぶ一時View
  - `timeout=120`
  - 操作者本人だけが操作可能

custom_id:

- `kukai:portal:create`
- `kukai:portal:list`
- `kukai:portal:check`

注意点:

- `句会を作成` は一般公開ボタンとして表示されるが、押下時に作成権限を確認する
- ハブチャンネルからは `effective_channel_id()` による句会解決を使わない
- 一覧と状況確認は ephemeral にする

### `bot/ui/admin_panel_view.py`

追加する主な要素:

- `KukaiAdminPanelEntryView`
  - persistent view
  - `timeout=None`
  - ボタン:
    - `管理パネルを開く`
  - custom_id: `kukai:admin-panel:<kukai_id>`
- `KukaiAdminPanelView`
  - ephemeral の操作パネル
  - `timeout=900`
  - 操作者本人だけが操作可能
- `KukaiAdminEditModal`
  - 通常運用向け編集項目の入力
- `NotificationReplaceModal`
  - 既存 `/kukai notify replace` と同じ形式の通知設定入力

管理パネルに置くボタン:

- `状態更新`
  - 最新の状態・締切・通知件数を再読込する
- `次へ進める`
  - 既存 `/kukai proceed` と同じ処理
- `一時停止`
- `再開`
- `中止`
- `巻き戻し`
- `作者公開`
- `操作ボタン再投稿`
- `設定編集`
- `通知設定`

表示内容:

- 句会名
- 句会ID
- 現在状態
- 開催チャンネル
- 締切
- 投句・選句の進行モード
- 作者公開設定
- 通知設定件数

## 既存処理の切り出し

`bot/cogs/kukai_cog.py` に重複実装を増やさないため、既存コマンド処理の中核を private helper に切り出す。

切り出し対象:

- proceed 実行
- pause 実行
- resume 実行
- cancel 実行
- rollback 実行
- reveal-authors 実行
- stage action button 再投稿
- notify replace
- notify restore
- 通常設定編集

特に `proceed` は既存副作用を必ず維持する。

維持する副作用:

- 未達者がいる場合の ephemeral 警告
- `それでも進める` 確認
- 管理者 thread への未達記録
- 手動進行時の締切通知補完
- 投句一覧の自動投稿
- 結果入口の自動投稿
- 通知ジョブの cancel/reschedule
- ステージ通知

コマンドとGUIは同じ helper を呼ぶ構造にする。

## Persistent View 登録

`bot/ui/persistent_views.py` を更新する。

起動時に常に登録するもの:

- `PortalView`

対象句会ごとに登録するもの:

- `KukaiAdminPanelEntryView(kukai.id)`

既存の登録対象:

- `StageActionView`
- `ResultOpenView`

これは維持する。

登録対象句会の状態は既存 `_PERSISTENT_VIEW_KUKAI_STATES` を使う。`cancelled` は管理パネル再操作不要なので対象外のままでよい。

## 権限

サーバーハブ:

- setup/repost はサーバーownerまたはadministratorのみ
- create ボタンは `permission_service.can_create_kukai()` で判定
- list/check ボタンは一般利用者も使用可

句会管理パネル:

- entry button 自体は管理者 thread に置く
- 押下時にも `permission_service.is_kukai_admin()` で必ず再検証する
- 権限がない場合は ephemeral error を返す

## エラー処理

サーバーハブ:

- Botにチャンネル作成権限がない場合は、権限不足を ephemeral で返す
- 保存済み `portal_channel_id` のチャンネルが消えていた場合は、setup で再作成する
- createボタンで権限がない場合は、作成権限がない旨を ephemeral で返す

管理パネル:

- 対象句会が削除済みまたは終了済みで操作不能な場合は、操作ごとに既存 `ServiceError` を表示する
- 中止・巻き戻し・通知復元など破壊的操作は確認UIを挟む
- 通知差し替えのパースエラーは行番号つきで表示する

## テスト計画

### Persistent View

追加または更新するテスト:

- `PortalView` の custom_id が固定である
- `KukaiAdminPanelEntryView` の custom_id が `kukai:admin-panel:<id>` である
- `register_persistent_views()` が portal view と admin panel view を登録する

対象:

- `tests/test_persistent_views.py`

### サーバーハブ

追加テスト:

- `/guild portal setup` が `GuildSettings.portal_channel_id` を保存する
- 既存チャンネルが保存済みなら再利用する
- 保存済みチャンネルが存在しなければ再作成する
- createボタンが作成権限を確認する
- checkボタンが複数句会時に選択UIを出す

### 管理パネル

追加テスト:

- 非管理者は管理パネルを開けない
- 管理者はパネルを開ける
- GUI proceed が既存 proceed helper を呼ぶ
- GUI cancel は確認後にのみ実行する
- GUI rollback は `RollbackView` 相当の選択を経て実行する
- GUI notify restore は既存 notify restore helper と同じ処理を呼ぶ

### 回帰テスト

最低限実行する。

```powershell
py -m pytest tests/test_persistent_views.py tests/test_kukai_service.py tests/test_notification_phase10.py -q
```

余裕があれば全体を実行する。

```powershell
py -m pytest -q
```

## ドキュメント更新

実装時に更新する文書:

- `CURRENT_SPEC.md`
  - サーバーハブ
  - 句会管理パネル
  - persistent view の対象
  - 既存コマンドとの関係
- `FUTURE_IMPROVEMENTS.md`
  - 詳細計画は `docs/gui_portal_plan.md` に移した旨だけを追記
  - 実装後は完了済み項目として整理する

## 実装順序

1. Alembic migration と `GuildSettings.portal_channel_id` を追加する
2. `PortalView` と `/guild portal setup/repost` を実装する
3. portal persistent view 登録とテストを追加する
4. `KukaiAdminPanelEntryView` を実装する
5. `/kukai panel` と句会作成後の管理パネル投稿を実装する
6. 既存 `kukai_cog.py` の管理操作を helper に切り出す
7. `KukaiAdminPanelView` から helper を呼ぶ
8. 通知設定GUIと通常編集GUIを追加する
9. `CURRENT_SPEC.md` と `FUTURE_IMPROVEMENTS.md` を更新する
10. targeted tests と可能なら full tests を実行する

## 実装時の注意

- 既存コマンドを消さない
- コマンドとGUIで業務ロジックを分岐させない
- 管理者操作は必ず押下時に権限再確認する
- `proceed` の副作用を絶対に落とさない
- ハブチャンネルではチャンネル単位の句会自動解決に頼らない
- 一般参加者に表示する操作は ephemeral を基本にする
- 破壊的操作は確認UIを必ず挟む
- 既存のステージ別参加ボタンはそのまま維持する

## 完了条件

- Botがサーバーハブチャンネルを作成し、常駐ボタンを投稿できる
- ハブから句会作成ウィザードを開ける
- ハブから句会一覧を表示できる
- ハブから自分の状況確認ができる
- 各句会の管理者 thread に管理パネル入口を投稿できる
- 管理パネルから主要な管理操作を実行できる
- Bot再起動後も portal/admin/stage/result の persistent button が動く
- 既存コマンド経路の挙動が変わらない
