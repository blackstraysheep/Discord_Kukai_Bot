# Kukai Bot ユーザ向け取扱説明書

最終更新: 2026-07-03

## 1. Kukai Bot とは

Kukai Bot は、Discord 上で句会を開くための Bot です。

参加者は Discord のボタンやコマンドから、エントリー、投句、選句、結果確認を行えます。句会管理者は、句会の作成、締切管理、進行、結果公開、PDF出力、プリセット管理、データ入出力を行えます。

## 2. できること

- 句会の作成、編集、中止、一時停止、再開
- エントリー受付、承認、却下、締切後申請の管理
- 投句受付、投句一覧公開、選句受付、結果公開
- 選句、選評、作者コメント、総評の受付
- 結果集計、表示形式切替、作者公開
- 投句一覧PDF、結果PDFの作成
- 締切前通知、通知プリセット、選句プリセットの管理
- ボイス句会予定の管理
- サーバーごとの句会作成権限設定
- 句会データのエクスポート、インポート

## 3. 基本の流れ

一般的な句会は次の順に進みます。

```text
エントリー
投句
投句一覧公開
選句
結果公開
終了
```

句会によっては、エントリーが省略されたり、管理者が手動で次の段階へ進めたり、自動で進行したりします。

多くのコマンドでは `kukai_id` を省略できます。同じチャンネルに進行中の句会が1つだけある場合、Bot が自動で対象句会を判定します。候補がない場合や複数ある場合は、句会IDの指定が必要です。

## 4. 参加者向け

### 4.1 開催中の句会を探す

```text
/list
```

このサーバーの開催中・招集中の句会一覧を表示します。

```text
/info kukai_id:123
```

句会名、題、状態、締切、句数、作者公開設定、ボイス句会予定などを確認します。`kukai_id` は省略できます。

### 4.2 句会に参加する

句会チャンネルに「エントリーする」ボタンがある場合は、そのボタンを押します。

コマンドで参加する場合:

```text
/entry join kukai_id:123
```

`kukai_id` は省略できます。実行すると俳号入力画面が開きます。俳号を空欄にした場合は、Discord のサーバー表示名が使われます。

承認制の句会では、管理者が承認するまで参加は確定しません。エントリー締切後の申請は、管理者へ承認依頼として通知されます。

### 4.3 エントリーを取り消す

```text
/entry cancel kukai_id:123
```

エントリー受付中であれば、自分のエントリーを取り消せます。受付終了後の削除は管理者に依頼してください。

### 4.4 投句する

句会チャンネルに「投句する」ボタンがある場合は、そのボタンを押します。

GUIで追加、変更、削除する場合:

```text
/submit kukai_id:123
```

`追加` を押すと投句入力画面が開きます。入力欄には1行に1句ずつ入力します。空行は無視されます。

```text
春風やまだ名を知らぬ橋を越え
山門に雨の匂ひの残りけり
```

1句は500文字までです。入力欄全体はDiscordの仕様により最大4000文字です。句会に最大投句数がある場合、残り投句可能数を超える入力は登録されません。

コマンド引数で複数句をまとめて入力する場合:

```text
/submit-bulk texts:
春風やまだ名を知らぬ橋を越え
山門に雨の匂ひの残りけり
```

`texts` は1行1句で入力します。`kukai_id` は省略できます。投句数の上限や下限は句会ごとの設定に従います。投句上限超過を許可する句会では、超過分は警告付きで登録されます。

### 4.5 選句する

投句一覧が公開され、選句受付が始まると、句会チャンネルに「選句する」ボタンが表示されます。

GUIで選句する場合:

```text
/select kukai_id:123
```

複数の選句をまとめて登録する場合:

```text
/select-bulk selections:
1=特選|景が鮮やかです
4=並選
7=clear
overall=全体に春らしい句が多かったです
```

`selections` は `番号=ラベル|コメント` の形式です。`overall=` は句会全体への総評、`番号=clear` はその句への選句取消です。`作者コメント` は自分の句に対してのみ登録できます。

### 4.6 自分の進捗を確認する

```text
/check kukai_id:123
```

参加状態、投句数、選句数、不足している作業などを確認できます。`kukai_id` は省略できます。

### 4.7 結果を見る

結果公開後は、句会チャンネルの「結果を見る」ボタン、または次のコマンドから確認できます。

```text
/result kukai_id:123 format:score
```

`format` は `score`（点数順）、`number`（番号順）、`author`（作者別）から選べます。`kukai_id` と `format` は省略できます。点数制OFFの句会では `score` は使えません。作者非公開の句会では `author` は使えません。

## 5. 管理者向け

### 5.1 句会を作成する

対話式の作成画面を開く場合:

```text
/kukai create
```

画面の案内に従って、基本情報、エントリー、締切、投句数、選句ルール、通知、ボイス句会などを設定します。

行形式でまとめて作成する場合:

```text
/kukai create-bulk config:
title=ミニ句会
theme=月
channel=current
entry_enabled=false
submission_close_at=2026-07-10 23:59
selecting_close_at=2026-07-12 23:59
```

`config` は複数行の `key=value` 形式です。主な項目は「7. 一括入力フォーマット」を参照してください。

### 5.2 句会情報と進捗を確認する

```text
/info kukai_id:123
/kukai admin status kukai_id:123
```

`/info` は句会設定や締切を表示します。`/kukai admin status` は管理者向けの進捗確認で、エントリー、投句、選句の不足状況を表示します。

現行実装では、句会情報表示はトップレベルの `/info` です。

### 5.3 句会を進行する

```text
/kukai proceed kukai_id:123
```

現在状態から次の段階へ進めます。投句不足や選句不足がある場合、Bot は実行者に警告を表示します。内容を確認し、「それでも進める」を選んだ場合のみ進行します。

投句公開は `/kukai proceed` の進行処理内で行われます。現行実装には独立した `/kukai publish` コマンドはありません。

### 5.4 操作ボタンを再投稿する

```text
/kukai button kind:current kukai_id:123
/kukai button kind:result kukai_id:123
```

`kind` は `current`、`entry`、`submission`、`selecting`、`result` から選べます。`current` は現在の状態に応じて、エントリー、投句、選句、結果のいずれかのボタンを投稿します。

### 5.5 句会設定を変更する

```text
/kukai edit kukai_id:123 title:新しい句会名 submission_close_at:2026-07-10 23:59
```

すべての引数は任意です。変更したい項目だけ指定します。

主な引数:

| 引数 | 説明 |
|---|---|
| `kukai_id` | 対象句会ID。省略可 |
| `title` | 句会名 |
| `theme` | 題 |
| `description` | 説明 |
| `entry_close_at` | エントリー締切 |
| `submission_open_at` | 投句開始日時 |
| `submission_close_at` | 投句締切 |
| `selecting_close_at` | 選句締切 |
| `entry_approval` | エントリー承認制の有無 |
| `entry_mode` | エントリー締切の進行モード。`manual` / `auto` |
| `min_participants` | 最少参加者数 |
| `submission_min` | 最少投句数 |
| `submission_max` | 最大投句数 |
| `submission_max_unlimited` | 最大投句数を無制限にする |
| `submission_overflow` | 上限超過投句を許可する |
| `submission_mode` | 投句締切の進行モード。`manual` / `semi_auto` / `full_auto` |
| `selecting_mode` | 選句締切の進行モード。`manual` / `semi_auto` / `full_auto` |
| `publish_mode` | 投句公開モード。`manual` / `auto` |
| `result_mode` | 結果公開モード。`manual` / `auto` |
| `author_publication_mode` | 作者公開方式。`with_result` / `manual` / `never` |
| `author_reveal` | 作者を公開済みにするか |
| `author_reveal_zero` | 0点以下の作者も公開するか |
| `select_rule_config` | 選句ルール差し替え。`gui` または行形式 |

`select_rule_config` は他の編集項目と同時指定できません。`select_rule_config=gui` を指定すると入力画面が開きます。行形式では `preset_id=3`、または `points_enabled=true` と複数の `label=...` を指定します。

変更後は、句会チャンネルに変更通知が投稿されます。締切を変更した場合は通知スケジュールも再設定されます。句会名変更時、チャンネル名が旧句会名由来の名前と一致している場合はチャンネル名も更新されます。

### 5.6 作者を公開する

```text
/kukai reveal-authors kukai_id:123
```

結果公開後、作者公開方式が手動公開の場合に作者を公開します。既に投稿済みの過去メッセージを巻き戻す操作ではなく、以後の結果表示やPDFに現在の設定が反映されます。

### 5.7 句会を一時停止・再開・中止・ロールバックする

```text
/kukai pause kukai_id:123
/kukai resume kukai_id:123
/kukai cancel kukai_id:123
/kukai rollback kukai_id:123 target_state:submission_open
```

`pause` は句会を一時停止します。`resume` は一時停止前の状態へ戻します。`cancel` は句会を中止します。`rollback` は指定した前段階へ戻します。

`rollback` の `target_state` は、開始前、エントリー受付中、エントリー締切、投句受付中、投句締切、投句公開待ち、選句受付中、選句締切から選べます。戻し先によっては投句番号や選句データがリセットされるため、Bot の確認画面をよく確認してください。

### 5.8 エントリーを管理する

```text
/entry list kukai_id:123 status:pending
/entry approve kukai_id:123 user:@user
/entry reject kukai_id:123 user:@user
/entry approve-all kukai_id:123
/entry remove kukai_id:123 user:@user
```

`/entry list` の `status` は `pending`、`approved`、`rejected`、`withdrawn` から選べます。省略すると全件表示します。

`approve` と `reject` の `user` は省略できます。省略した場合は承認待ち一覧から選ぶ画面が表示されます。承認・却下の通知は、原則としてメンションなしで句会チャンネルに投稿されます。

`remove` はエントリー締切後に管理者がエントリーを削除する操作です。

### 5.9 通知を設定する

```text
/kukai notify list kukai_id:123
/kukai notify replace kukai_id:123 config:
submission_close,24h,kukai,all,false
selecting_close,1h,mention,incomplete,true
voice_start,30m,dm,all,false
/kukai notify restore kukai_id:123
```

`replace` は通知設定をすべて差し替えます。`restore` はデフォルト通知へ戻します。通知行の形式は「7. 一括入力フォーマット」を参照してください。

### 5.10 選句プリセットを管理する

```text
/select-preset list
/select-preset gui
/select-preset add name:標準 points_enabled:true set_default:true
/select-preset bulk config:
name=標準
points_enabled=true
set_default=true
label=特選,2,1,0,1,optional
label=並選,1,2,0,5,optional
```

その他の操作:

| コマンド | 主な引数 | 説明 |
|---|---|---|
| `/select-preset rename` | `preset_id`, `new_name` | プリセット名を変更 |
| `/select-preset delete` | `preset_id` | プリセットを削除 |
| `/select-preset set-points` | `preset_id`, `points_enabled` | 点数制の有無を変更 |
| `/select-preset set-default` | `preset_id` | 既定プリセットに設定 |
| `/select-preset summary` | `preset_id`, `text` | 句数表示テキストを設定。空文字で自動生成に戻す |
| `/select-preset label list` | `preset_id` | ラベル一覧を表示 |
| `/select-preset label add` | `preset_id`, `label_name`, `point` | ラベルを追加・更新 |
| `/select-preset label edit` | `preset_id`, `label_name`, `new_name`, `point` | ラベル名または点数を変更 |
| `/select-preset label remove` | `preset_id`, `label_name` | ラベルを削除 |

### 5.11 通知プリセットを管理する

```text
/notify-preset list
/notify-preset add
/notify-preset bulk config:
name=標準通知
set_default=true
entry=submission_close,24h,kukai,all,false
entry=selecting_close,24h,kukai,all,false
/notify-preset set-default name:標準通知
/notify-preset delete name:標準通知
```

`add` はモーダルで通知プリセットを作成します。`bulk` は行形式で作成・更新します。既定プリセットは句会作成ウィザードの通知設定ステップで自動適用されます。

### 5.12 PDFを作成する

投句一覧PDF:

```text
/pdf submission kukai_id:123 show_author:false theme:default public:false
```

結果PDF:

```text
/pdf result kukai_id:123 show_author:true show_reviewer:true theme:default public:false
```

PDFコマンドの引数:

| 引数 | 対象 | 説明 |
|---|---|---|
| `kukai_id` | 両方 | 対象句会ID。省略可 |
| `show_author` | 両方 | 作者名を表示するか。投句一覧PDFでは結果公開前は強制的に非表示 |
| `show_reviewer` | `/pdf result` | 選評者名を表示するか |
| `theme` | 両方 | PDFテーマ名。通常は `default` |
| `public` | 両方 | `true` ならチャンネルへ投稿。管理者のみ |

通常は自分だけに見える返信でPDFが作成されます。チャンネルへ公開する場合は管理者権限が必要です。結果公開前の結果PDF生成は管理者のみ、結果公開前のチャンネル投稿はできません。

### 5.13 句会管理者とデータを管理する

```text
/kukai admin add kukai_id:123 user:@user
/kukai admin remove kukai_id:123 user:@user
/kukai admin export kukai_id:123 export_format:json
/kukai admin import file:kukai_export.json
```

`add` と `remove` は句会ごとの管理者を追加・削除します。削除は句会作成者またはサーバー所有者のみ実行できます。

`export` は句会データをDMで送付します。`kukai_id` を省略すると全句会をエクスポートしますが、全句会エクスポートはサーバー管理者のみ実行できます。`export_format` は `json` または `csv` です。

`import` はエクスポートしたJSONファイルを取り込みます。サーバー管理者のみ実行できます。

### 5.14 サーバー設定を管理する

```text
/guild settings
/guild settings create_role:role role_ids:123456789012345678
/guild settings create_role:specific user_ids:123456789012345678,987654321098765432
```

引数なしで現在の句会作成権限設定を表示します。変更はサーバー管理者のみ実行できます。

`create_role` は `everyone`、`admin`、`owner`、`role`、`specific` から選べます。`role` の場合は `role_ids`、`specific` の場合は `user_ids` をカンマ区切りの数値IDで指定します。

## 6. コマンド一覧

### 6.1 参加者・閲覧系

| コマンド | 引数 | 説明 |
|---|---|---|
| `/list` | なし | 開催中・招集中の句会一覧 |
| `/info` | `kukai_id` | 句会詳細 |
| `/entry join` | `kukai_id` | エントリー |
| `/entry cancel` | `kukai_id` | 自分のエントリー取消 |
| `/submit` | `kukai_id` | 投句GUI |
| `/submit-bulk` | `texts`, `kukai_id` | 1行1句で一括投句 |
| `/select` | `kukai_id` | 選句GUI |
| `/select-bulk` | `selections`, `kukai_id` | 行形式で一括選句 |
| `/check` | `kukai_id` | 自分の進捗確認 |
| `/result` | `kukai_id`, `format` | 結果表示 |
| `/pdf submission` | `kukai_id`, `show_author`, `theme`, `public` | 投句一覧PDF |
| `/pdf result` | `kukai_id`, `show_author`, `show_reviewer`, `theme`, `public` | 結果PDF |

### 6.2 句会管理系

| コマンド | 引数 | 説明 |
|---|---|---|
| `/kukai create` | なし | 作成ウィザード |
| `/kukai create-bulk` | `config` | 行形式で一括作成 |
| `/kukai proceed` | `kukai_id` | 次の状態へ進行 |
| `/kukai button` | `kind`, `kukai_id` | 操作ボタン再投稿 |
| `/kukai edit` | 多数 | 句会設定変更 |
| `/kukai reveal-authors` | `kukai_id` | 作者公開 |
| `/kukai pause` | `kukai_id` | 一時停止 |
| `/kukai resume` | `kukai_id` | 再開 |
| `/kukai cancel` | `kukai_id` | 中止 |
| `/kukai rollback` | `kukai_id`, `target_state` | 指定状態へ戻す |
| `/kukai notify list` | `kukai_id` | 通知設定一覧 |
| `/kukai notify replace` | `config`, `kukai_id` | 通知設定差し替え |
| `/kukai notify restore` | `kukai_id` | 通知設定をデフォルトへ戻す |
| `/kukai admin status` | `kukai_id` | 進捗確認 |
| `/kukai admin add` | `kukai_id`, `user` | 句会管理者追加 |
| `/kukai admin remove` | `kukai_id`, `user` | 句会管理者削除 |
| `/kukai admin export` | `kukai_id`, `export_format` | データ出力 |
| `/kukai admin import` | `file` | JSON取り込み |

### 6.3 プリセット・サーバー設定系

| コマンド | 引数 | 説明 |
|---|---|---|
| `/select-preset list` | なし | 選句プリセット一覧 |
| `/select-preset gui` | なし | 選句プリセットGUI |
| `/select-preset bulk` | `config` | 選句プリセット一括登録 |
| `/select-preset add` | `name`, `points_enabled`, `set_default` | プリセット追加 |
| `/select-preset rename` | `preset_id`, `new_name` | プリセット改名 |
| `/select-preset delete` | `preset_id` | プリセット削除 |
| `/select-preset set-points` | `preset_id`, `points_enabled` | 点数制変更 |
| `/select-preset set-default` | `preset_id` | 既定設定 |
| `/select-preset summary` | `preset_id`, `text` | 句数表示テキスト設定 |
| `/select-preset label list` | `preset_id` | ラベル一覧 |
| `/select-preset label add` | `preset_id`, `label_name`, `point` | ラベル追加・更新 |
| `/select-preset label edit` | `preset_id`, `label_name`, `new_name`, `point` | ラベル編集 |
| `/select-preset label remove` | `preset_id`, `label_name` | ラベル削除 |
| `/notify-preset list` | なし | 通知プリセット一覧 |
| `/notify-preset add` | なし | モーダルで通知プリセット作成 |
| `/notify-preset bulk` | `config` | 通知プリセット一括登録 |
| `/notify-preset delete` | `name` | 通知プリセット削除 |
| `/notify-preset set-default` | `name` | 既定通知プリセット設定 |
| `/guild settings` | `create_role`, `role_ids`, `user_ids` | 句会作成権限の表示・更新 |

## 7. 一括入力フォーマット

### 7.1 共通ルール

- `key=value` 形式で入力します。
- 空行と `#` で始まるコメント行は無視されます。
- 真偽値は `true/false`、`on/off`、`yes/no`、`1/0` を使えます。
- 日時は `2026-07-10 23:59` のようにJSTで入力します。
- 無制限は `∞`、`unlimited`、`none`、`null` を使えます。
- エラー時は原因行が表示されます。

### 7.2 `/kukai create-bulk config`

必須項目:

| 項目 | 説明 |
|---|---|
| `title` | 句会名 |
| `submission_close_at` | 投句締切 |
| `selecting_close_at` | 選句締切 |
| `entry_close_at` | `entry_enabled=true` の場合に必要 |

主な任意項目:

| 項目 | 説明 |
|---|---|
| `theme` | 題 |
| `description` | 説明 |
| `channel` | `current` / `new` / `<#channel_id>` |
| `channel_name` | `channel=new` のときのチャンネル名 |
| `category_id` | `channel=new` のときのカテゴリID |
| `entry_enabled` | エントリー制の有無。既定 `true` |
| `entry_approval` | 承認制の有無。既定 `false` |
| `entry_mode` | `manual` / `auto` |
| `min_participants` | 最少参加者数 |
| `submission_open_at` | 投句開始日時 |
| `submission_min` | 最少投句数。既定 `1` |
| `submission_max` | 最大投句数。既定 `5` |
| `submission_overflow` | 上限超過許可 |
| `submission_mode` | `manual` / `semi_auto` / `full_auto` |
| `selecting_mode` | `manual` / `semi_auto` / `full_auto` |
| `publish_mode` | `manual` / `auto` |
| `result_mode` | `manual` / `auto` |
| `author_publication_mode` | `with_result` / `manual` / `never` |
| `author_reveal` | 作者公開済みにするか |
| `author_reveal_zero` | 0点以下作者も公開するか |
| `preset_id` | 選句プリセットID |
| `label` | 選句ラベル。複数行可 |
| `voice_enabled` | ボイス句会の有無 |
| `voice_channel` | ボイス/ステージチャンネル |
| `voice_start_at` | ボイス句会開始 |
| `voice_end_at` | ボイス句会終了 |
| `reminder` | 通知設定。複数行可 |

選句ラベル:

```text
label=名前,点数,rank,最小数,最大数,コメントモード
label=名前,点数,最小数,最大数,コメントモード
```

`comment_mode` は `none`、`optional`、`required` です。`rank` を省略した場合は定義順で自動採番されます。`label=` がある場合は `preset_id` より優先されます。

通知:

```text
reminder=event,offset,destination,target,mention
```

| 要素 | 値 |
|---|---|
| `event` | `entry_close` / `submission_open` / `submission_close` / `selecting_close` / `voice_start` |
| `offset` | `24h`、`30m`、`1d6h` など |
| `destination` | `kukai` / `dm` / `mention` / `admin` / `<#channel_id>` |
| `target` | `all` / `incomplete` / `admin` |
| `mention` | `true` / `false` |

全項目例:

```text
title=春の句会
theme=桜
description=春季定例句会です
channel=current
entry_enabled=true
entry_approval=false
min_participants=0
entry_close_at=2026-07-01 23:59
submission_close_at=2026-07-03 23:59
selecting_close_at=2026-07-05 23:59
submission_min=1
submission_max=5
submission_overflow=false
submission_mode=manual
selecting_mode=manual
publish_mode=manual
result_mode=manual
author_publication_mode=with_result
author_reveal=true
author_reveal_zero=true
label=特選,2,1,0,1,optional
label=並選,1,2,0,5,optional
label=予選,0,3,0,∞,optional
voice_enabled=true
voice_channel=<#123456789012345678>
voice_start_at=2026-07-06 21:00
voice_end_at=2026-07-06 22:00
reminder=entry_close,24h,kukai,all,false
reminder=submission_close,24h,kukai,all,false
reminder=selecting_close,1h,mention,incomplete,true
reminder=voice_start,30m,dm,all,false
```

### 7.3 `/select-preset bulk config`

```text
name=標準
points_enabled=true
set_default=true
label=特選,2,1,0,1,optional
label=並選,1,2,0,5,optional
label=予選,0,3,0,∞,optional
```

対応項目は `name`、`points_enabled`、`set_default`、`label` です。

### 7.4 `/notify-preset bulk config`

```text
name=標準通知
set_default=true
entry=submission_close,24h,kukai,all,false
entry=selecting_close,24h,kukai,all,false
```

対応項目は `name`、`set_default`、`entry` です。`entry` の形式は通知の `event,offset,destination,target,mention` と同じです。

### 7.5 `/select-bulk selections`

```text
1=特選|景が鮮やかです
4=並選
7=clear
overall=全体に春らしい句が多かったです
```

公開番号を左辺に指定します。ラベル名は句会内にある選句ラベル名と一致させます。

## 8. よくある質問

### コマンドに句会IDを入れなくてもよいですか

多くのコマンドでは省略できます。同じチャンネルに進行中の句会が1つだけある場合、Bot が自動で判定します。

候補がない場合や複数ある場合は、句会IDの指定を求められます。スレッド内で実行した場合は、親チャンネルを使って判定します。

### ボタンを押しても受付できないことがあります

あります。ボタンが残っていても、句会の状態が進んでいる場合は受付できません。その場合、Bot が「現在は受付中ではありません」などのエラーを返します。

### 俳号とDiscord名のどちらが表示されますか

俳号が登録されている場合は俳号が優先されます。俳号がない場合は Discord の表示名が使われます。

### 作者はいつ公開されますか

句会ごとの作者公開設定に従います。結果公開時に公開する設定、管理者が後で公開する設定、公開しない設定があります。0点以下の作者を伏せる設定もあります。

### 選句の「作者コメント」とは何ですか

自分の句に対して付けるコメントです。選句ラベルとして自動的に用意されます。通常の選句とは異なり、自句に対してのみ登録できます。

### PDFで作者名が出ないことがあります

あります。作者公開設定が非公開の場合、PDFでも作者名は出ません。投句一覧PDFでは、結果公開前は `show_author:true` を指定しても無記名になります。0点以下作者を非公開にする設定もPDFに反映されます。

### Discordの画面内に独自の管理画面は出ますか

出ません。Kukai Bot が使えるUIは、Discord の Embed、ボタン、セレクトメニュー、モーダル、コマンドです。Webアプリのような自由な画面は Discord 内には表示できません。

## 9. 困ったとき

まず以下を確認してください。

- 句会が現在どの状態か
- 締切を過ぎていないか
- 自分のエントリーが承認済みか
- 投句数や選句数の条件を満たしているか
- 同じチャンネルに複数の進行中句会がないか
- PDF生成の場合、環境でPDF機能が有効か
- 公開投稿や管理操作の場合、自分に句会管理者権限があるか

参加者は `/info` と `/check` を確認してください。管理者は `/info`、`/kukai admin status`、`/entry list`、`/kukai notify list` を確認してください。動作がおかしい場合は、Bot の運用者にログ確認を依頼してください。
