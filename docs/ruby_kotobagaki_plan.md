# 夏雲式ルビ・詞書対応 実装計画

最終更新: 2026-07-02

## 目的

- 投句本文に夏雲システム式のルビ記法を使えるようにする。
- 句の冒頭に詞書を付け、Discord上とPDF上の両方で句本文より小さく前行に表示できるようにする。
- Discordでは読みやすいプレーンテキスト表示、PDFではLuaLaTeXによる正式なルビ組みを行う。
- 入力記法が曖昧な場合は誤変換せず、投句時点で利用者にエラーとして返す。

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

- DBには原則として入力記法を含む本文を保持する。
- Discord表示では `親字（よみ）` に変換する。
- PDF表示では `\ruby{親字}{よみ}` に変換する。

制約:

- ルビ中の再ルビは不可。
- `｜` の直後に親字がない入力は不可。
- `（` または `）` が閉じていない入力は不可。
- ルビを含む本文内に通常の `（ ）` や `｜` が混在し、解釈が曖昧になる場合はエラーにする。

夏雲式のエスケープとして、ルビではない通常の `（` を `｜` の直後に置きたい場合は、`｜｜（` を通常文字列として扱う。

```text
｜（（これは詞書ではなく本文の二重括弧で始めたい句
```

上記は先頭の `｜` をエスケープ記号として扱い、詞書ではなく本文として保存・表示する。

### 詞書

夏雲式の冒頭 `（（...））` を詞書として採用する。

```text
（（母上の詞自ら句になりて））毎年よ彼岸の入に寒いのは
```

表示方針:

- 投稿時に冒頭の `（（...））` を検出し、詞書と句本文に分離する。
- 詞書は `submissions.kotobagaki` に保存する。
- 句本文は詞書を除いた本文として扱う。
- Discordでは句本文の前行に詞書を表示する。
- PDFでは句本文の前に小書きで詞書を表示する。
- 詞書内のルビも夏雲式 `｜親字（よみ）` を使えるようにする。

詞書扱いにしたくない本文が `（（` で始まる場合は、夏雲式に合わせて先頭に `｜` を付ける。

```text
｜（（これは詞書ではなく本文））
```

## 実装方針

### データモデル

`submissions` に nullable な詞書カラムを追加する。

```python
kotobagaki: Mapped[str | None] = mapped_column(String(500), nullable=True)
```

実装対象:

- `bot/models/submission.py`
- Alembic migration
- export/import
- 関連テスト

既存データは `kotobagaki=None` として扱い、本文表示・PDF生成の既存挙動を維持する。

### パーサ

夏雲式を解析する共通ユーティリティを追加する。

候補:

- `bot/utils/submission_markup.py`

責務:

- 投句入力から詞書を分離する。
- ルビ記法を検証する。
- Discord表示用テキストへ変換する。
- TeX表示用テキストへ変換する。

想定API:

```python
@dataclass(frozen=True)
class ParsedSubmissionText:
    text: str
    kotobagaki: str | None

def parse_submission_input(raw: str) -> ParsedSubmissionText:
    ...

def render_submission_for_discord(text: str) -> str:
    ...

def render_submission_for_tex(text: str) -> str:
    ...
```

TeX用変換では、親字と読みを個別に `tex_escape()` 相当でエスケープしてから `\ruby{...}{...}` を生成する。ユーザー入力をそのままTeXコマンドへ流し込まない。

### 投稿・編集

`submission_service.submit()` と `submission_service.edit()` で入力を解析・検証する。

方針:

- `normalize(text.strip())` 後に夏雲式パーサを通す。
- 空本文は既存どおりエラーにする。
- 詞書だけで本文が空になる入力はエラーにする。
- 解析エラーは `ValidationError` として返す。
- 本文は `Submission.text`、詞書は `Submission.kotobagaki` に保存する。

`/submit-bulk` では各行を1句として扱う。各行の冒頭 `（（...））` はその句の詞書として解析する。

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

1. 保存済み `kotobagaki` をDiscord用ルビ変換する。
2. 保存済み `text` をDiscord用ルビ変換する。
3. それぞれ `discord_safe()` を通す。
4. 詞書がある場合は句本文の前行に表示する。

表示例:

```text
母上の詞自ら句になりて
毎年よ彼岸の入に寒いのは
```

Embed内では詞書を本文より控えめに見せるため、引用記号や括弧など既存UIに合う表現を選ぶ。ただしDiscordには文字サイズ指定がないため、PDFと完全一致させる必要はない。

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
- 句本文と詞書にはルビ対応filterを使う。
- 詞書は句本文の前に `\small` 相当で出す。
- 投句一覧PDFでは俳句セル内で詞書を前行に置く。
- 結果PDFでは句本文直前に詞書を置く。

TeXテンプレートには次を追加する。

```tex
\usepackage{luatexja-ruby}
```

### Export/Import

`submissions` セクションに `kotobagaki` を追加する。

export:

```json
{
  "text": "...",
  "kotobagaki": "..."
}
```

import:

- `kotobagaki` がない既存形式は `None` として扱う。
- `kotobagaki` がある場合は文字列として検証する。
- import時も本文・詞書それぞれの長さ制限を守る。

## 変更対象ファイル

主な変更対象:

- `bot/models/submission.py`
- `bot/services/submission_service.py`
- `bot/services/pdf_service.py`
- `bot/services/export_service.py`
- `bot/utils/submission_markup.py`
- `bot/utils/submission_publish.py`
- `bot/utils/result_publish.py`
- `bot/ui/submission_view.py`
- `bot/ui/select_view.py`
- `bot/cogs/submission_cog.py`
- `bot/cogs/result_cog.py`
- `bot/cogs/check_cog.py`
- `bot/templates/pdf/default/submission_list.tex.j2`
- `bot/templates/pdf/default/result.tex.j2`
- Alembic migration
- `tests/test_submission_service.py`
- `tests/test_pdf_service.py`
- 表示ユーティリティ用の新規テスト
- export/import関連テスト

実装時は、投句本文を表示している箇所を `rg "discord_safe\\(.*text|submission\\.text|r\\.text|result\\.text"` などで再確認し、表示変換漏れを防ぐ。

## テスト計画

### パーサ

- `｜遠山（とおやま）に日の当たりたる｜枯野（かれの）かな` を2つのルビとして解析できる。
- `炎天下待ち行列に｜www（草生える）` を英字親字のルビとして解析できる。
- `（（母上の詞自ら句になりて））毎年よ彼岸の入に寒いのは` から詞書と本文を分離できる。
- `｜（（本文としての二重括弧））` が詞書扱いされない。
- 詞書内の `｜親字（よみ）` を解析できる。
- ルビを含まない通常本文はそのまま通る。

### 入力エラー

- 未閉じの `（` はエラー。
- 未閉じの `）` はエラー。
- `｜（よみ）` のように親字が空の入力はエラー。
- `｜親字（）` のように読みが空の入力はエラー。
- ルビ内に再度 `｜` を含む入力はエラー。
- ルビを含む本文内に曖昧な通常括弧が混在する場合はエラー。
- 詞書だけで本文が空になる入力はエラー。

### Discord表示

- ルビ付き本文が `親字（よみ）` に変換される。
- 詞書が本文の前行に出る。
- DiscordメンションやMarkdown特殊文字は、変換後も既存どおり無害化される。
- 投句一覧、選句UI、結果表示、結果投稿で同じ表示変換になる。

### PDF表示

- ルビ付き本文から `\ruby{親字}{よみ}` が生成される。
- 親字・読みのTeX特殊文字がそれぞれエスケープされる。
- 詞書が句本文の前に出る。
- 詞書内ルビも `\ruby{...}{...}` に変換される。
- 既存の絵文字変換とTeXエスケープが壊れない。

### 永続化

- 新規投句で `Submission.text` と `Submission.kotobagaki` が正しく保存される。
- 編集で本文と詞書が更新される。
- 既存投句は `kotobagaki=None` のまま表示できる。
- export/importで `kotobagaki` が保持される。
- `kotobagaki` がない旧export形式をimportできる。

## 実装順序

1. Alembic migrationと `Submission.kotobagaki` を追加する。
2. 夏雲式パーサと表示変換ユーティリティを追加する。
3. パーサ単体テストを追加する。
4. `submission_service.submit()` / `edit()` で入力解析・保存を行う。
5. 投句UIと `/submit-bulk` の完了表示を詞書対応にする。
6. 投句公開・選句UI・結果表示・結果投稿のDiscord表示変換を統一する。
7. `pdf_service` にルビ対応TeX filterを追加する。
8. PDFテンプレートで詞書とルビを表示する。
9. export/importに `kotobagaki` を追加する。
10. targeted testsを実行し、可能なら全体テストを実行する。

## 実装時の注意

- DBにはTeX変換後文字列を保存しない。
- Discord表示用変換とTeX表示用変換を混同しない。
- `discord_safe()` は変換後に必ず通す。
- TeX用変換では親字・読み・詞書を個別にエスケープする。
- 曖昧な入力を都合よく推測しない。誤変換より入力エラーを優先する。
- 既存の `tex_escape()` と絵文字処理を壊さない。
- `submissions.text` の既存500文字制限に加え、`kotobagaki` も長くなりすぎないよう制限する。
- Discord Modalは入力欄数制限があるため、通常追加・編集では詞書欄を増やすか、夏雲式冒頭記法だけで入力させるかをUI実装時に明確にする。
- `/submit-bulk` は1行1句の前提を維持し、行をまたぐ詞書は扱わない。
- 既存コマンド・既存PDF生成の利用者に対して、ルビや詞書を使わない通常句の挙動を変えない。

## 未対応範囲

- 青空文庫式 `｜親字《よみ》` のサポート。
- HTML `<ruby>` のサポート。
- Discord上での正式なルビ表示。
- ルビ対象の自動推定。
- ルビ記法と通常括弧の複雑な混在の自動解釈。
- 複数行にまたがる詞書。
- 既存投句本文から詞書を自動移行する処理。

## 完了条件

- 夏雲式ルビ付き句を投句・編集できる。
- 夏雲式詞書付き句を投句・編集できる。
- Discord上の投句一覧、選句UI、結果表示で詞書とルビ読みが破綻せず表示される。
- PDF上でルビが正式なルビとして組まれる。
- PDF上で詞書が句本文の前に小書きで表示される。
- export/importで詞書が保持される。
- ルビ・詞書を使わない既存句の表示とPDF生成が変わらない。
