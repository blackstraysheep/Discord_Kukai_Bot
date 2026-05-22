# PDF生成機能 実装メモ

最終更新: 2026-05-22

## 技術スタック

| 要素 | 選択 | 備考 |
|-----|------|------|
| TeXエンジン | LuaLaTeX | Unicode完全対応 |
| 投句一覧クラス | `ltjtarticle` + `luatexja-preset` | 横書き・landscape |
| 結果クラス | `jlreq` + `luatexja-fontspec` | 縦書き |
| 均等割り | `kintou.sty`（カスタム） | `bot/templates/pdf/` に配置 |
| テンプレート | Jinja2 (`.tex.j2`) | `\| tex` フィルタでエスケープ |
| コンテナ | Docker（`python:3.11` + apt LuaLaTeX） | ローカル＝本番の同一イメージ |
| PDF公開 | Caddy（Discord 25MB 超過時のみ） | 未設定時は超過でエラー |

## ファイル構成

```
bot/
  cogs/pdf_cog.py               ← コマンド定義
  services/pdf_service.py       ← コンパイル・公開ロジック
  templates/pdf/
    kintou.sty                  ← 共有スタイルファイル（コンパイル時に自動コピー）
    default/
      theme.toml                ← フォント・用紙設定
      submission_list.tex.j2    ← 投句一覧テンプレート
      result.tex.j2             ← 結果テンプレート
tests/
  test_pdf_service.py           ← TeX生成・エスケープのテスト（LuaLaTeX不要）
Dockerfile                      ← LuaLaTeX込みイメージ（フォントキャッシュ事前生成）
```

## 環境変数

```bash
LUALATEX_BIN=/usr/bin/lualatex   # 空にするとPDF機能を無効化
PDF_MAX_CONCURRENT=2              # 同時コンパイル数（プロセス内セマフォ）
PDF_COMPILE_TIMEOUT=60            # タイムアウト秒数
PDF_SERVE_BASE_URL=               # Caddy公開URL（省略時はサイズ超過でエラー）
PDF_SERVE_DIR=/srv/pdfs           # 公開ディレクトリ
```

## コマンド

```
/pdf submission [kukai_id] [show_author] [theme] [public]
/pdf result     [kukai_id] [show_author] [theme] [public]
```

### アクセス制御
- 誰でも実行可能（`public=False` 時は自分だけに見える ephemeral）
- `public=True` でチャンネル投稿 → 句会管理者のみ

### `show_author` の制限（投句一覧のみ）
- ステートが `results` / `ended` の場合のみ `show_author=True` が有効
- それ以前は強制的に `False`（無記名）

### ファイル名
```
submission_{kukai_id}_{named|anonymous}.pdf
result_{kukai_id}_{named|anonymous}.pdf
```

### 日付
`submission_close_at` 優先、なければ `entry_close_at`（JST表示）

## テンプレートシステム

### テーマ構造
```
bot/templates/pdf/{theme}/
  theme.toml           ← フォント・用紙設定
  submission_list.tex.j2
  result.tex.j2
```

### Jinja2 利用パターン
```jinja2
{{ var | tex }}          ← ユーザー入力のTeXエスケープ（必須）
{% for s in submissions %}...{% endfor %}
{% if s.author %}...{% endif %}
{{ submissions | batch(20, fill) }}  ← ページ分割
{{- var -}}              ← 前後のスペースを除去（\kintouの引数など）
```

### カスタム .sty の配置
`bot/templates/pdf/*.sty` は `_compile()` 内でコンパイル用一時ディレクトリへ自動コピーされる。
テーマ固有ではなく全テーマ共通として扱う。

## セキュリティ

- ユーザー入力は必ず `{{ var | tex }}` でエスケープ
- `--shell-escape` は使用しない
- エスケープ対象: `\ { } % # & _ $ ^ ~`

## 投句一覧レイアウト（defaultテーマ）

- `ltjtarticle` クラス、A4横置き（landscape）
- `longtable` でページ分割対応
- 列: №(1zw) / 選者(5zw, 空欄) / 俳句(26zw, kintou均等割り) / 作者(5zw) / 予備(5zw)
- ヘッダー行はページごとに繰り返し（`\endhead`）
- 参加者一覧を右揃えで表示

## 結果レイアウト（defaultテーマ）

- `jlreq` クラス、A4縦組み（tate）
- 順位・得点・投句番号 → 俳句本文 → 作者名
- ラベル別得票数（`\tatechuyoko` で縦中横）
- 選評コメント一覧
- 総評セクション（コメントがある場合のみ表示）

## Caddy による大容量PDF公開（オプション）

```
PDF_SERVE_BASE_URL=https://pdf.example.com
PDF_SERVE_DIR=/srv/pdfs
```

```
# Caddyfile
pdf.example.com {
    root * /srv/pdfs
    file_server
}
```

```
# 1日後に削除
find /srv/pdfs -type f -mtime +1 -delete
```
