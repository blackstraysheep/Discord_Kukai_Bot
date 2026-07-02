# 夏雲式ルビ対応 実装計画

最終更新: 2026-07-02

## 目的

- 投句本文に夏雲システム式のルビ記法を使えるようにする。
- Discordでは読みやすいプレーンテキスト表示、PDFではLuaLaTeXによる正式なルビ組みを行う。
- ルビ記法が不正な場合は誤変換せず、投句・編集時点で利用者にエラーとして返す。
- 詞書はDBカラム追加と表示経路の改修が大きいため、今回の実装対象から外して保留する。

参照仕様:

- 夏雲システム「作品投稿時に使える記法」
  - https://ntgm.gitbook.io/docs/i/ruby

## 入力仕様

### ルビ

夏雲式の `｜親字（よみ）` を採用する。

```text
｜遠山（とおやま）に日の当たりたる｜枯野（かれの）かな
炎天下待ち行列に｜www（草生える）
```

表示方針:

- DBには入力記法を含む本文をそのまま保持する。
- Discord表示では `親字（よみ）` に変換する。
- PDF表示では `\ruby{親字}{よみ}` に変換する。

制約:

- `｜` の直後に親字がない入力は不可。
- 読みが空の入力は不可。
- 読みを閉じる `）` がない入力は不可。
- ルビの親字または読みに `｜`、`（`、`）` を含める入力は不可。
- `｜｜` は通常の `｜` を入力するためのエスケープとして扱う。

## 詞書の扱い

詞書/前書きは今回実装しない。

理由:

- `submissions.kotobagaki` の追加にはAlembic migrationが必要。
- 投句追加、編集、一括投句、export/import、PDF、Discord表示の複数経路をまとめて変える必要がある。
- Discord上では文字サイズ指定ができず、PDFとは別の表示設計も必要になる。

今後正式対応する場合は、夏雲式の冒頭 `（（...））` を候補にする。ただし、その時点で別計画としてDBスキーマ、UI、export/import、PDFレイアウトまで含めて再検討する。

## 実装方針

### データモデル

DB変更は行わない。

- `submissions.text` に夏雲式ルビ記法を含む本文を保存する。
- Alembic migrationは追加しない。
- export/importのスキーマは変更しない。

### パーサ

夏雲式ルビを扱う共通ユーティリティを追加する。

候補:

- `bot/utils/submission_markup.py`

責務:

- 投句本文内のルビ記法を検証する。
- Discord表示用テキストへ変換する。
- TeX表示用テキストへ変換する。
- Discord安全化まで含めた表示ヘルパーを提供する。

想定API:

```python
def validate_submission_markup(text: str) -> None:
    ...

def render_submission_for_discord(text: str) -> str:
    ...

def discord_safe_submission_text(text: str, *, limit: int | None = None) -> str:
    ...

def render_submission_for_tex(text: str, escape: Callable[[str], str]) -> str:
    ...
```

TeX用変換では、親字と読みを個別に `tex_escape()` 相当でエスケープしてから `\ruby{...}{...}` を生成する。ユーザー入力をそのままTeXコマンドへ流し込まない。

### 投稿・編集

`submission_service.submit()` と `submission_service.edit()` で入力を検証する。

方針:

- 既存どおり `normalize(text.strip())` を行う。
- 空本文は既存どおりエラーにする。
- ルビ記法が不正な場合は `ValidationError` として返す。
- 保存する本文は変換しない。

`/submit-bulk` やGUI一括投句は、各句ごとに既存の `submission_service.submit()` を通るため同じ検証を受ける。

### Discord表示

既存の `discord_safe()` を直接呼ぶ前に、夏雲式表示変換を挟む。

対象:

- 投句確認UI
- 投句公開Embed
- 選句UI
- 結果表示
- 結果公開Embed
- `/check` など投句本文を表示する箇所

表示順:

1. 保存済み `text` をDiscord用ルビ変換する。
2. 必要なら表示長で切り詰める。
3. `discord_safe()` を通す。

表示例:

```text
遠山（とおやま）に日の当たりたる枯野（かれの）かな
```

### PDF表示

defaultテーマのPDFテンプレートで `luatexja-ruby` を使う。

対象:

- `bot/services/pdf_service.py`
- `bot/templates/pdf/default/submission_list.tex.j2`
- `bot/templates/pdf/default/result.tex.j2`
- `tests/test_pdf_service.py`

方針:

- Jinja2 filterとしてルビ対応TeX変換を追加する。
- 既存の `tex` filterは通常テキスト用として残す。
- 句本文にはルビ対応filterを使う。

TeXテンプレートには次を追加する。

```tex
\usepackage{luatexja-ruby}
```

## 変更対象ファイル

主な変更対象:

- `bot/services/submission_service.py`
- `bot/services/pdf_service.py`
- `bot/utils/submission_markup.py`
- `bot/utils/submission_publish.py`
- `bot/utils/result_publish.py`
- `bot/ui/submission_view.py`
- `bot/ui/select_view.py`
- `bot/cogs/result_cog.py`
- `bot/cogs/check_cog.py`
- `bot/templates/pdf/default/submission_list.tex.j2`
- `bot/templates/pdf/default/result.tex.j2`
- `tests/test_submission_markup.py`
- `tests/test_submission_service.py`
- `tests/test_pdf_service.py`

実装時は、投句本文を表示している箇所を `rg "discord_safe\\(.*text|submission\\.text|r\\.text|result\\.text"` などで再確認し、表示変換漏れを防ぐ。

## テスト計画

### パーサ

- `｜遠山（とおやま）に日の当たりたる｜枯野（かれの）かな` を2つのルビとして解析できる。
- `炎天下待ち行列に｜www（草生える）` を英字親字のルビとして解析できる。
- `｜｜（` が通常の `｜（` として扱われる。
- ルビを含まない通常本文はそのまま通る。

### 入力エラー

- 読みを閉じる `）` がない入力はエラー。
- `｜（よみ）` のように親字が空の入力はエラー。
- `｜親字（）` のように読みが空の入力はエラー。
- 親字または読みに `｜`、`（`、`）` を含む入力はエラー。

### Discord表示

- ルビ付き本文が `親字（よみ）` に変換される。
- DiscordメンションやMarkdown特殊文字は、変換後も既存どおり無害化される。
- 投句一覧、選句UI、結果表示、結果投稿で同じ表示変換になる。

### PDF表示

- ルビ付き本文から `\ruby{親字}{よみ}` が生成される。
- 親字・読みのTeX特殊文字がそれぞれエスケープされる。
- 既存の絵文字変換とTeXエスケープが壊れない。
- `luatexja-ruby` がdefaultテーマの投句一覧PDFと結果PDFで読み込まれる。

## 実装順序

1. 夏雲式ルビの検証・変換ユーティリティを追加する。
2. パーサ単体テストを追加する。
3. `submission_service.submit()` / `edit()` で入力検証を行う。
4. 投句公開・選句UI・結果表示・結果投稿・checkのDiscord表示変換を統一する。
5. `pdf_service` にルビ対応TeX filterを追加する。
6. PDFテンプレートで本文にルビ対応filterを使う。
7. targeted testsを実行し、可能なら全体テストを実行する。

## 実装時の注意

- DBにはTeX変換後文字列を保存しない。
- Discord表示用変換とTeX表示用変換を混同しない。
- `discord_safe()` はルビ変換後に必ず通す。
- TeX用変換では親字・読みを個別にエスケープする。
- 既存の `tex_escape()` と絵文字処理を壊さない。
- ルビを使わない通常句の挙動を変えない。
- 詞書は今回実装しない。`（（...））` は通常本文として扱う。

## 未対応範囲

- 詞書/前書き。
- `submissions.kotobagaki` 追加。
- 青空文庫式 `｜親字《よみ》` のサポート。
- HTML `<ruby>` のサポート。
- Discord上での正式なルビ表示。
- ルビ対象の自動推定。
- 複雑な括弧混在の自動解釈。

## 完了条件

- 夏雲式ルビ付き句を投句・編集できる。
- 不正なルビ記法は投句・編集時にエラーになる。
- Discord上の投句一覧、選句UI、結果表示でルビ読みが破綻せず表示される。
- PDF上でルビが正式なルビとして組まれる。
- ルビを使わない既存句の表示とPDF生成が変わらない。
