# 参加記録・横断履歴・投句重複警告 実装計画

最終更新: 2026-06-19

## 目的

- 自分の投句・選句履歴を、句会単位で確認できるようにする。
- サーバ設定で許可した場合、同一サーバ内の他参加者の参加記録も見られるようにする。
- 同じBot/同じDB内の複数サーバをまたいで、自分の参加記録確認と投句重複警告を行う。

ここでいう参加記録は、句会への参加、投句、選句、選評、総評のうちDBに保存されているものを指す。

## 実装範囲

- 対象は同一Botインスタンスが接続している同一DB内の履歴のみ。
- 別Bot、別DB、エクスポート済み外部データ、手元のPDFやCSVは対象外。
- 複数サーバ横断は、DB上の同一Discord `user_id` で検索できる範囲に限る。
- 重複判定は正規化済み本文の完全一致のみ。
- 類似句検出、表記ゆれ検出、読み仮名や旧字体の同一視は行わない。

## 表示仕様

### 基本形式

参加記録は句会タイトルごとにブロック化する。

```text
第1回〇〇句会

投句
ぶらんこを……（4点）

選句
特選
寒紅引け……（神野紗希）

並選
セクシーに……（関悦史）

第2回〇〇句会
...
```

- 句会タイトルは、可能ならMarkdownリンクにする。
- 投句欄は、対象ユーザー自身の投句を表示する。
- 選句欄は、対象ユーザーが選んだ句を選句ラベルごとに表示する。
- 投句の点数は、結果集計できる段階以降だけ表示する。
- 点数制OFFの句会では点数を表示しない。
- 選句先の作者名は、作者公開済みの場合のみ表示する。
- 作者非公開の場合は作者名部分を省略し、句本文だけ表示する。
- 総評がある場合は、選句欄の後に `総評` として表示する。

### リンク生成

タイトルリンクのリンク先は、次の優先順で決める。

1. `kukais.result_message_id` があり、DiscordのメッセージURLを組み立てられる場合は結果メッセージへリンクする。
2. `kukais.channel_id` がある場合は句会チャンネルへリンクする。
3. どちらも使えない場合はリンクなしの通常タイトルにする。

DiscordのメッセージURL形式:

```text
https://discord.com/channels/{guild_id}/{channel_id}/{message_id}
```

チャンネルURL形式:

```text
https://discord.com/channels/{guild_id}/{channel_id}
```

Discord Embedのフィールド名はMarkdownリンク表示が安定しない可能性があるため、句会タイトルリンクはEmbed本文側に置く。

### ページングと制限

- `limit` は句会ブロック数の上限として扱う。
- Discord Embedの文字数制限を超えそうな場合は複数Embedに分割する。
- 1つのEmbedには最大25フィールド、合計6000文字制限があるため、本文生成時に安全側で分割する。
- 長い句本文、選評、総評は省略する。省略長は実装時に定数化する。

## 権限制御

### 本人記録

- `/record me` は本人だけに見えるephemeral返信で表示する。
- 本人の記録は常時閲覧可能とする。
- 進行中句会でも、本人自身の投句・選句・総評は表示してよい。
- `scope=current` は現在のサーバ内に限定する。
- `scope=all` は同一DB内の全サーバを対象にする。

### 他人記録

- `/record user` は同一サーバ内の他参加者記録を表示する。
- サーバ設定 `participation_record_visibility` が `guild_public` の場合のみ許可する。
- 既定値 `private` では拒否する。
- 他人の記録は、現在のサーバの句会だけを対象にする。
- 他人の記録は、句会状態が `results` または `ended` のものだけ表示する。
- 進行中句会、作者非公開の作者名、未公開の投句一覧は表示しない。
- 返信は原則ephemeralにする。公開投稿はこの機能の初期実装では扱わない。

### 管理者例外

初期実装では、サーバ管理者や句会管理者も `participation_record_visibility=private` を迂回しない。

管理者専用の監査・進捗確認は既存の `/kukai status` や `/entry list` と役割が異なるため、参加記録の他人閲覧とは分けて考える。

## DB変更

`guild_settings` に参加記録公開設定を追加する。

```text
participation_record_visibility VARCHAR(20) NOT NULL DEFAULT 'private'
```

値:

- `private`: 他人の参加記録閲覧を許可しない。
- `guild_public`: 同一サーバ内メンバーによる、結果公開済み句会の参加記録閲覧を許可する。

実装時の更新箇所:

- `bot/models/guild_settings.py`
- `bot/models/__init__.py` は既に `GuildSettings` を読み込んでいるため、通常は変更不要。
- Alembic migrationを追加する。
- SQLiteテスト環境は `Base.metadata.create_all()` で作成されるため、モデル変更が反映される。

## 実装方針

### サービス層

`bot/services/participation_record_service.py` を追加する。

サービス層で担当すること:

- 対象ユーザーの参加句会一覧を取得する。
- `scope=current` / `scope=all` の検索範囲を解決する。
- 本人閲覧と他人閲覧の表示許可を判定する。
- 句会ごとに投句、点数、選句、選評、総評を集約する。
- 作者名を表示してよいか判定する。
- Discord表示用DTOを作る。
- 句会タイトルリンクを生成する。

サービス層でDiscord Embedそのものは作らない。Embed生成はCog側または専用formatterに寄せる。

想定DTO:

```python
@dataclass
class ParticipationRecord:
    kukai_id: int
    guild_id: int
    channel_id: int | None
    result_message_id: int | None
    title: str
    title_url: str | None
    state: str
    submissions: list[ParticipationSubmission]
    selections_by_label: list[ParticipationSelectionGroup]
    overall_comment: str | None

@dataclass
class ParticipationSubmission:
    text: str
    total_score: int | None

@dataclass
class ParticipationSelectionGroup:
    label: str
    selections: list[ParticipationSelection]

@dataclass
class ParticipationSelection:
    selected_text: str
    author_name: str | None
    comment: str | None
```

DTOは実装都合で多少変更してよいが、CogがSQLAlchemyモデルを直接組み合わせずに済む境界を維持する。

### データ取得

履歴対象の句会は、少なくとも次のどれかに該当するものとする。

- `entries.user_id == target_user_id`
- `submissions.user_id == target_user_id`
- `selects.selector_user_id == target_user_id`
- `overall_comments.user_id == target_user_id`

エントリー制なしの句会では `kukai_participants` に俳号が保存されることがあるため、表示名解決では `entries` と `kukai_participants` の両方を見る。

点数表示は既存の `result_service.compute_results()` を優先して使う。状態が集計可能でない場合や公開前の場合は点数を `None` にする。

### 表示名

作者名・選者名の表示は、既存結果表示と同じ方針に合わせる。

- 俳号があれば俳号を優先する。
- 俳号がなければサーバー表示名を使う。
- 取得できない場合は `UID:{user_id}` とする。

他サーバ横断の `/record me scope=all` では、Botが現在キャッシュしていないサーバのメンバー表示名を取れない場合がある。その場合も俳号優先、なければ `UID:{user_id}` でよい。

### Cog

`bot/cogs/record_cog.py` を追加する。

コマンド:

- `/record me scope: current|all limit:`
- `/record user user: limit:`

方針:

- 返信はephemeral。
- `limit` は既定10、最大25程度に制限する。
- `scope` の既定は `current`。
- `user` にBot自身を指定した場合はエラーにする。
- サーバ外ユーザーや取得不能ユーザーはDiscord側の `discord.Member` 引数で自然に制限する。

Embed生成は、句会ブロック単位で本文へ積む。文字数制限が近い場合は次Embedに分ける。

### Guild Settings

`bot/cogs/admin_cog.py` の `/guild settings` に `participation_record_visibility` 引数を追加する。

表示時は現在値をEmbedに出す。

更新時はサーバ管理者またはサーバ所有者のみ許可する既存ルールを使う。

許可値:

- `private`
- `guild_public`

## コマンド仕様

### `/record me`

自分の参加記録を表示する。

引数:

- `scope`: `current` / `all`
- `limit`: 表示する句会数。省略時10。

仕様:

- `current` は現在のサーバ内だけを見る。
- `all` は同一DB内の全サーバを見る。
- 本人記録なので、進行中句会の自分の投句・選句も表示する。
- 点数は結果集計可能な句会だけ表示する。

### `/record user`

同一サーバ内の他参加者の参加記録を表示する。

引数:

- `user`: 対象ユーザー。
- `limit`: 表示する句会数。省略時10。

仕様:

- 現在サーバの `participation_record_visibility` が `guild_public` でない場合は拒否する。
- 対象は現在サーバの句会だけ。
- 表示対象は `results` / `ended` の句会だけ。
- 作者名はその句会の作者公開設定に従う。

### `/guild settings`

既存の `/guild settings` に次を追加する。

引数:

- `participation_record_visibility`: `private` / `guild_public`

表示:

- 現在値を `participation_record_visibility` として表示する。

更新:

- 既存の `create_role` 更新と同じく、サーバ管理者またはサーバ所有者のみ許可する。
- 引数が指定されていない項目は既存値を維持する。

## 重複警告仕様

### 判定

投句登録時に、本人の全サーバ履歴から同じ正規化済み本文を検索する。

- 対象は `submissions.user_id == current_user_id`。
- `is_discarded=True` は警告対象から除外する。
- 現在登録しようとしている句会の既存投句も対象に含めてよいが、同一処理内で登録した直後の自分自身を誤検出しないようにする。
- 編集時に同じ警告を出すかは初期実装では任意。まずは新規追加時を対象にする。

### 表示

警告は登録を止めない。

表示例:

```text
⚠️ 以前の参加記録に同じ投句があります。
- 第1回〇〇句会
- 第3回△△句会
```

句会タイトルは可能ならリンクにする。

### 実装箇所

`submission_service.submit()` の戻り値を拡張する。

現在:

```python
tuple[Submission, bool]
```

変更案:

```python
@dataclass
class SubmissionResult:
    submission: Submission
    over_limit_warning: bool
    duplicate_warnings: list[DuplicateSubmissionWarning]
```

既存呼び出し元が複数あるため、移行時は全呼び出しを同時に更新する。

対象:

- `bot/cogs/submission_cog.py`
- `bot/ui/submission_view.py`
- 関連テスト

`/submit-bulk` では複数句を登録するため、句ごとの警告を集約して表示する。

GUI投句モーダルでも、登録完了Embedまたは追加のephemeralメッセージに警告を表示する。

## テスト計画

### サービス層

- 本人の履歴取得で、複数 `guild_id` の句会が返る。
- `scope=current` では現在サーバの句会だけ返る。
- `scope=all` では同一DB内の全サーバの句会が返る。
- `entries` なし、投句のみの句会も本人履歴に含まれる。
- 選句のみ、総評のみの参加も履歴に含まれる。
- 結果集計済み句会では投句に点数が付く。
- 集計不可状態では点数が `None` になる。
- 作者未公開では選句先の作者名が `None` になる。
- 作者公開済みでは俳号優先の作者名が入る。

### 権限制御

- `participation_record_visibility=private` では `/record user` 相当のサービス呼び出しが拒否される。
- `guild_public` では同一サーバの結果公開済み句会だけ返る。
- 他人記録では進行中句会が返らない。
- 他人記録では別サーバの句会が返らない。

### コマンド/表示

- `/record me` のEmbed本文が句会タイトル、投句、選句ラベル、選句先を含む。
- 句会タイトルリンクが `result_message_id` 優先で生成される。
- `result_message_id` がない場合はチャンネルリンクになる。
- URLを作れない場合はリンクなしタイトルになる。
- limitを超える句会は表示されない。
- 長文や件数過多でEmbed分割される。

### 重複警告

- 同じ正規化済み本文を本人が別サーバで投句済みなら警告が出る。
- 同じ正規化済み本文を本人が同一サーバの別句会で投句済みなら警告が出る。
- 他人の同一本文は警告対象にならない。
- `is_discarded=True` の投句は警告対象にならない。
- 警告が出ても投句登録は成功する。
- `/submit-bulk` で複数句の警告が集約表示される。
- GUI投句モーダルでも警告が表示される。

## 実装時の注意

- `CURRENT_SPEC.md` は現在仕様の文書なので、この機能の実装完了後に更新する。
- `FUTURE_IMPROVEMENTS.md` は短いバックログ用なので、必要ならこの文書への参照だけ追記する。
- 参加記録はプライバシーに関わるため、既存サーバの既定値は必ず `private` にする。
- 他人記録の公開範囲はサーバ単位に限定し、`guild_public` でも複数サーバ横断の他人検索はしない。
- 本人の複数サーバ横断履歴は、Discord `user_id` が同じであることを根拠にする。
- 作者公開設定は既存の `author_reveal` / `author_reveal_zero` と矛盾しないようにする。
- 結果表示と同じく、俳号を優先して表示する。
- SQLAlchemyのlazy-loadで非同期エラーを起こさないよう、必要な関連は `selectinload` するか明示クエリで取得する。
- 既存の `/check` は現在句会内の自分の状況確認として残し、履歴機能とは役割を分ける。
