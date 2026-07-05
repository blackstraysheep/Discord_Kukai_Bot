# 段階的参加者限定チャンネル実装計画

## 目的

将来構想として、句会の進行段階に応じて開催チャンネルの閲覧範囲を切り替えられる設定を追加する。

現在の句会チャンネルは、Bot が作成または既存チャンネルを利用し、Discord 側の通常のチャンネル権限に従って誰でも閲覧できる前提になっている。新機能では、句会ごとに「常時公開」か「途中から参加者限定」かを選べるようにする。

重要な前提:

- 作成直後からチャンネルを完全に非公開にすると、そのチャンネル内の `/entry join` やエントリーボタンは未参加者から見えない
- そのため、v1では「最初から非公開」を主案にしない。エントリー導線が必要な段階までは公開し、締切やステージ進行に合わせて参加者限定へ切り替える
- サーバーポータルや公開告知は補助導線として有用だが、参加者限定チャンネルの必須前提にはしない
- Discord のチャンネル権限は DB 状態と自動同期するが、Discord API 失敗時に句会データ更新まで巻き戻すと運用上つらいので、DB 更新を正とし、権限同期は再実行可能にする

## 実装範囲

### v1で実装するもの

- 句会ごとのチャンネル閲覧ポリシー
  - `public`: 現在通り
  - `public_until_participation_close`: 参加受付中は公開し、エントリー締切があればエントリー締切後、なければ投句締切後に有効参加者だけ閲覧可能
- 作成ウィザードで閲覧モードを選択できる
- `/kukai create-bulk` でも閲覧モードを指定できる
- ステージ進行や締切に合わせてチャンネル権限を公開から参加者限定へ切り替える
- エントリーの承認、却下、取消、削除に合わせて、参加者限定化後のチャンネル権限を同期する
- 管理者向けに権限同期を手動再実行するコマンドを追加する
- ポータルからエントリーできる導線は補助機能として扱う

### v1では見送るもの

- 大規模句会向けの専用ロール自動作成方式
- カテゴリ全体の権限管理
- 投稿済みメッセージの可視性差し替え
- ボイスチャンネルや Discord scheduled event の参加者限定化
- 既存チャンネルの既存権限を完全に保存して復元する機能
- 作成直後から非公開にするモード

## ユーザー向け挙動

### 公開チャンネル

現在と同じ。

- 作成された句会チャンネルは、サーバーの通常権限で見える
- エントリー、投句、選句、結果表示の導線は句会チャンネル上に投稿される

### 途中から参加者限定チャンネル

句会作成時に「参加受付が終わったら参加者限定にする」かを選ぶ。

- `public_until_participation_close`:
  - エントリー締切がある句会では、エントリー締切までは開催チャンネルを公開する
  - エントリー締切がない句会では、投句締切までは開催チャンネルを公開する
  - 公開中は未参加者も開催チャンネル内のエントリーボタン、投句ボタン、 `/entry join` 導線を見られる
  - 限定化タイミング到達後、承認済み参加者または有効参加者と管理者だけが見える状態に切り替える
  - 以後、却下、取消、管理者削除された人は開催チャンネルが見えなくなる
- 終了後も、参加者と管理者はチャンネルを閲覧できる

## データモデル

`Kukai` に閲覧ポリシーを追加する。

```python
channel_visibility_policy: Mapped[str] = mapped_column(String(30), nullable=False, default="public")
```

値:

- `public`
- `public_until_participation_close`

Alembic migration:

- upgrade: `kukais.channel_visibility_policy` を nullable ではなく default `public` で追加する
- 既存行はすべて `public` とする
- downgrade: 同カラムを削除する

将来のサーバー既定値として `GuildSettings.default_channel_visibility_policy` を追加してもよいが、v1では句会ごとの明示設定だけで十分。サーバー既定値を入れる場合は、ウィザード初期値だけに使い、作成済み句会へは retroactive に適用しない。

## UI・コマンド

### 作成ウィザード

対象ファイル:

- `bot/ui/wizard/wizard_state.py`
- `bot/ui/wizard/step_basic.py`
- `bot/ui/wizard/step_confirm.py`

`WizardState` に `channel_visibility_policy: str = "public"` を追加する。

`step_basic.py` の句会チャンネル設定にセレクトを追加する。

選択肢:

- `公開`: 現在通り、サーバー権限に従って閲覧
- `参加受付後は参加者限定`: エントリー締切があればエントリー締切後、なければ投句締切後に有効参加者限定にする

確認画面には、チャンネル作成方法とは別に閲覧モードを表示する。

### `/kukai create-bulk`

対象ファイル:

- `bot/cogs/kukai_cog.py`

config に `channel_visibility_policy=public|public_until_participation_close` を追加する。

省略時は `public`。

既存チャンネル指定時に `public` 以外が指定された場合は、締切時に既存権限を変更するため、完了メッセージに注意を出す。

### 管理者向け同期コマンド

対象ファイル:

- `bot/cogs/kukai_cog.py`

追加コマンド:

```text
/kukai visibility-sync [kukai_id]
```

挙動:

1. 対象句会を `resolve_kukai_in_channel()` で解決する
2. `permission_service.is_kukai_admin()` で権限確認する
3. `channel_visibility_service.sync_channel_permissions()` を実行する
4. 付与、削除、失敗件数を ephemeral で返す

Discord API 失敗やチャンネル未検出時は、DBは変更せず、再実行可能なエラーとして返す。

## エントリー導線

段階的参加者限定では、エントリーまたは投句に必要な段階までは開催チャンネルを公開するため、開催チャンネル内のボタンを主導線にできる。

v1ではサーバーポータルを補助導線にする。

対象ファイル:

- `bot/ui/portal_view.py`
- `bot/cogs/entry_cog.py`
- `bot/services/kukai_service.py`

必要に応じて追加する導線:

- ポータルに `エントリーする` ボタンを追加する
- 進行中の `entry_enabled=True` かつエントリー受付可能な句会一覧を ephemeral で表示する
- 対象句会を選ぶと既存 `EntryHaigoModal` を開く

注意点:

- ポータルでは `effective_channel_id()` による句会解決に頼らない
- 対象句会は `guild_id` と `kukai_id` で明示的に解決する
- 公開チャンネルの句会もポータルからエントリーできてよい
- 既存の開催チャンネル内エントリーボタンは維持する

## 権限同期サービス

新規ファイル:

- `bot/services/channel_visibility_service.py`

主要関数:

```python
async def apply_initial_channel_visibility(
    guild: discord.Guild,
    kukai,
    channel: discord.TextChannel,
) -> ChannelVisibilitySyncResult: ...

async def restrict_channel_to_participants(
    session: AsyncSession,
    guild: discord.Guild,
    kukai,
    channel: discord.TextChannel | None = None,
) -> ChannelVisibilitySyncResult: ...

async def sync_channel_permissions(
    session: AsyncSession,
    guild: discord.Guild,
    kukai,
    channel: discord.TextChannel | None = None,
) -> ChannelVisibilitySyncResult: ...

async def grant_entry_access(
    guild: discord.Guild,
    kukai,
    entry: Entry,
) -> ChannelVisibilitySyncResult: ...

async def revoke_entry_access(
    guild: discord.Guild,
    kukai,
    user_id: int,
) -> ChannelVisibilitySyncResult: ...
```

`ChannelVisibilitySyncResult` は dataclass にする。

項目:

- `mode`
- `channel_id`
- `granted_count`
- `revoked_count`
- `skipped_count`
- `failed_user_ids`
- `message`

### `public` の同期

v1では `public` への切り替え時、Botが過去に付与した参加者個別 overwrite だけを削除する。

ただし、どの overwrite が Bot によるものかを厳密に判定する保存情報はないため、v1では次のどちらかに限定する。

- Botが限定化した参加者個別 overwrite を追跡できる句会だけ `public` へ戻せる
- もしくは `public` への変更機能自体は v1対象外にし、作成時設定のみとする

推奨は後者。v1では作成後の閲覧モード変更を実装しない。

### 初期権限

`public_until_participation_close` でも、新規チャンネル作成時点では公開状態にする。

理由:

- 開催チャンネル内の既存エントリーボタン、投句ボタン、句会案内をそのまま使える
- ポータル導線を必須にしなくてよい
- Discord権限の初期設定が単純になる

### 参加者限定化時の権限

- `@everyone`: `view_channel=False`
- Bot自身: `view_channel=True`, `send_messages=True`, `manage_channels=True` が必要
- 句会作成者: `view_channel=True`, `send_messages=True`
- サーバー管理者は Discord 権限上見えるため、個別 overwrite は不要
- 明示的な句会管理者が追加済みの場合は `view_channel=True`

既存チャンネル利用時:

- 既存の `@everyone view_channel` を `False` に変更する
- 個別参加者 overwrite を付与する
- 完了メッセージで「既存チャンネルの閲覧権限を変更した」旨を表示する
- 実装前にテストしやすくするため、v1ではまず新規チャンネルのみ対応にしてもよい

推奨実装順では、新規チャンネルのみ先に対応し、既存チャンネル対応は最後に回す。

### 限定化のトリガー

- `public_until_participation_close`:
  - エントリー締切が設定されている場合は、`entry_close` deadline job
  - エントリー締切が設定されていない場合は、`submission_close` deadline job
  - `/kukai proceed` で、上記の限定化タイミングをまたいで次段階へ進むとき
  - `/kukai jump` などで、上記の限定化タイミングより後の状態へ移動する管理操作がある場合

限定化は冪等にする。同じ句会で複数回呼ばれても、必要な overwrite を再同期するだけで成功扱いにする。

## エントリー状態との連動

対象ファイル:

- `bot/services/entry_service.py`
- `bot/cogs/entry_cog.py`
- `bot/ui/entry_manage_view.py`
- `bot/utils/entry_notifications.py`

`entry_service` は DB 状態の更新だけを担当し、Discord 権限同期は Cog/UI 層またはステージ進行側から呼ぶ。

理由:

- `entry_service` は Discord `guild` / `channel` オブジェクトを持たない
- DB 更新と Discord API 更新を同じ transaction に入れると失敗時の扱いが複雑になる
- 既存の通知処理も Cog/UI 層で DB 更新後に実行されている

呼び出し箇所:

- `/entry join`
  - チャンネルがまだ公開段階なら権限付与は不要
  - すでに参加者限定化済みで、戻り値の `entry.status == "approved"` の場合だけ `grant_entry_access()`
  - `pending` の場合は付与しない
- `/entry cancel`
  - `withdrawn` にした後 `revoke_entry_access()`
- `/entry approve`
  - 承認後 `grant_entry_access()`
- `/entry approve-all`
  - 一括承認後、承認者リストをまとめて同期
- `/entry reject`
  - 却下後 `revoke_entry_access()`
- `/entry remove`
  - 削除後 `revoke_entry_access()`
- `EntryManageView` / `LateEntryReviewView`
  - ボタン経由の承認・却下でも同じ同期 helper を呼ぶ

同期失敗時:

- エントリー状態の変更は成功扱いにする
- 操作者には「参加状態は更新済みだが、チャンネル権限同期に失敗した。`/kukai visibility-sync` を実行してください」と返す
- ログには `event=channel_visibility_sync_failed kukai_id=... channel_id=... user_id=...` を残す

## 句会管理者の扱い

句会作成者と `KukaiAdmin` は、参加者限定チャンネルでも閲覧できる必要がある。

対象ファイル:

- `bot/models/kukai.py`
- `bot/repositories/kukai_repo.py`
- `bot/services/permission_service.py`
- 管理者追加・削除コマンドがある場合はその Cog

v1で管理者追加・削除と連動する場合:

- 管理者追加時に `view_channel=True` を付与する
- 管理者削除時は、そのユーザーが承認済み参加者でなければ overwrite を削除する

管理者追加・削除コマンドが未整備なら、v1では同期コマンドでまとめて補正できればよい。

## Discord 権限上の注意

Botに必要な権限:

- Manage Channels
- View Channels
- Send Messages
- Read Message History

運用上の制約:

- チャンネル permission overwrite には上限があるため、参加者が非常に多い句会では個別 overwrite 方式が破綻する可能性がある
- v1では小規模から中規模の句会を前提に個別 overwrite 方式で実装する
- 大規模化する場合は、句会ごとに一時ロールを作成して参加者へロール付与する方式を検討する

エラー時の扱い:

- Botが `Manage Channels` を持たない場合は、参加者限定モードでの作成を失敗させる
- 個別ユーザーへの権限付与に失敗した場合は、エントリー自体は成功させ、管理者へ同期失敗を表示する
- チャンネルが削除済みの場合は `ServiceError` として返す

## 既存機能への影響

### `resolve_kukai_in_channel()`

限定化後にチャンネルが見えない利用者は、そのチャンネル内でコマンドを実行できない。v1では、エントリーまたは投句に必要な段階まではチャンネルを公開しておくため、ポータル導線は必須にしない。

既存のチャンネル解決ロジックは維持する。ポータル経由のエントリーを追加する場合だけ、`kukai_id` 明示で解決する。

### 投稿・選句・結果表示

`submission_service` と `select_service` はすでに承認済みエントリーを要求している。

- `submission_service.submit()` は `entry_enabled=True` の場合、承認済み entry を要求する
- `select_service` も承認済み selector を要求する

したがって、チャンネル閲覧権限は追加の表示制御であり、投句・選句のサービス層ルールは大きく変えない。

### 通知

`public_until_participation_close` では、限定化タイミングまでは未参加者にも開催チャンネル内通知が見える。

限定化後は未参加者に開催チャンネル内通知は見えない。必要に応じて、参加募集や締切前通知はポータルチャンネルにも投稿できるようにする。ただし v1では必須にしない。

## テスト計画

### モデル・migration

- `Kukai.channel_visibility_policy` の default が `public`
- 既存行 migration 後に `public` が入る
- `create_kukai()` が `channel_visibility_policy` を保存する

対象:

- `tests/test_kukai_service.py`
- Alembic migration テストがあれば追加

### 権限同期サービス

Discord API は fake channel/member でテストする。

追加テスト:

- `public` の句会では同期が no-op になる
- `public_until_participation_close` の句会で、エントリー締切がある場合はエントリー締切時に参加者限定化される
- `public_until_participation_close` の句会で、エントリー締切がない場合は投句締切時に参加者限定化される
- 参加者限定化時に承認済み entry に `view_channel=True` を付与する
- `pending` / `rejected` / `withdrawn` entry には付与しない
- 承認済みから却下された user の overwrite を削除する
- 句会作成者は常に閲覧可能
- チャンネル未検出時に失敗結果を返す

対象候補:

- `tests/test_channel_visibility_service.py`

### エントリー連動

追加テスト:

- 公開段階では `/entry join` 後に grant が呼ばれない
- 参加者限定化済みの承認不要句会で `/entry join` 後に grant が呼ばれる
- 承認制句会で `/entry join` pending 時は grant されない
- `/entry approve` 後に grant が呼ばれる
- `/entry reject` / `/entry cancel` / `/entry remove` 後に revoke が呼ばれる
- grant/revoke 失敗時もエントリー状態は更新される

対象:

- `tests/test_entry_service.py`
- Cog/UI は必要に応じて fake interaction で追加

### ポータル導線

追加テスト:

- ポータルのエントリーボタン custom_id が固定
- 受付可能な句会だけ選択肢に出る
- 選択後に `EntryHaigoModal(kukai_id=...)` が開く
- ハブからは `effective_channel_id()` を使わない

対象:

- `tests/test_persistent_views.py`
- `tests/test_portal_view.py`

## 実装順序

1. `Kukai.channel_visibility_policy` と Alembic migration を追加する
2. `kukai_service.create_kukai()` に `channel_visibility_policy` 引数を追加し、default `public` を維持する
3. `WizardState` と作成ウィザードに閲覧モード選択を追加する
4. `/kukai create-bulk` に `channel_visibility_policy` config を追加する
5. `channel_visibility_service.py` を追加し、fake Discord オブジェクトで単体テストを書く
6. `entry_close` / `submission_close` / 手動進行時に参加者限定化を呼ぶ
7. 参加者限定化済みの場合だけ、エントリー join/approve/reject/cancel/remove と `EntryManageView` に grant/revoke を接続する
8. `/kukai visibility-sync` を追加する
9. ポータルのエントリー導線は補助導線として必要に応じて追加する
10. `CURRENT_SPEC.md` に実装後の現在仕様を追記する
11. `FUTURE_IMPROVEMENTS.md` に大規模向けロール方式と既存チャンネル完全対応を残す

## 完了条件

- 句会作成時に `public` / `public_until_participation_close` を選べる
- `public` の既存挙動が変わらない
- `public_until_participation_close` では、限定化タイミングまでは未参加者も開催チャンネルを閲覧できる
- エントリー締切がある場合は、エントリー締切後に未参加者が開催チャンネルを閲覧できない
- エントリー締切がない場合は、投句締切後に未参加者が開催チャンネルを閲覧できない
- 承認済み参加者または有効参加者は、限定化後も開催チャンネルを閲覧できる
- 却下、取消、削除された参加者は閲覧権限を失う
- サーバーポータルなしでも、公開段階の開催チャンネルからエントリーできる
- 権限同期に失敗しても、管理者が `/kukai visibility-sync` で修復できる
- Bot再起動後も既存の persistent view とポータル導線が動く
- 既存の投句・選句・結果表示のサービス層ルールが変わらない

## 未解決・将来検討

- 参加者が多い句会では、個別 overwrite 方式ではなく一時ロール方式に移行する
- 既存チャンネルの権限を変更する場合、変更前 overwrite の保存・復元をどう扱うか決める
- ポータル以外の公開募集チャンネルにエントリーボタンを投稿する専用コマンドを追加する
- 終了後にチャンネルを公開へ戻す、またはアーカイブ権限にする運用を追加する
- ボイス句会や Discord scheduled event も参加者限定にするか検討する
