# Discord句会bot PDF生成 実装メモ

## 目的

Discord句会botで、句会結果・投句一覧・選評などをLuaLaTeXでPDF化する。

**運用モデル**: セルフホスト。導入者が各自の環境（Oracle Free Tier など）でbotを運用する。
Dockerイメージで配布し、導入者は `git clone → .env 設定 → docker compose up` で起動できる。

## 技術スタック

| 要素 | 選択 |
|-----|------|
| TeXエンジン | LuaLaTeX |
| 文書クラス | jlreq（縦書きモード） |
| フォント | Noto Serif CJK JP |
| テンプレート | Jinja2 (.tex.j2) |
| コンテナ | Docker（ローカル＝本番の同一イメージ） |
| PDF公開 | Caddy（Discord 25MB 超過時のみ） |

## 基本構成

Discord Bot
  ↓
PDF生成要求
  ↓
LuaLaTeX worker
  ↓
PDF生成
  ↓
Discordへ添付
  ↓
サイズ超過時は一時URLを投稿←ここは要検討

## 推奨サーバ構成
第一候補

Oracle Cloud Always Free VM

Discord botを常時起動
LuaLaTeXを同一VMにインストール
PDF生成も同一VMで実行
サイズ超過PDFはVM上で一時公開
PDF公開
/srv/pdfs/
  ├ random-id-1.pdf
  ├ random-id-2.pdf

CaddyでHTTPS公開する。

pdf.example.com {
    root * /srv/pdfs
    file_server
}

PDF URL例：

https://pdf.example.com/7f3d91c8a2.pdf
サイズ超過時の処理
PDFを生成
ファイルサイズ確認
Discord添付上限以下なら添付送信
超過なら /srv/pdfs/ に保存
ランダムURLをDiscordに投稿
cronで一定期間後に削除

例：

find /srv/pdfs -type f -mtime +1 -delete
LuaLaTeX導入例

Ubuntu系の場合：

sudo apt update
sudo apt install -y \
  texlive-luatex \
  texlive-lang-japanese \
  texlive-fonts-recommended \
  latexmk \
  fonts-noto-cjk \
  ghostscript
PDF生成コマンド例
lualatex -interaction=nonstopmode main.tex

または：

latexmk -lualatex -interaction=nonstopmode main.tex
セキュリティ注意

Discord入力をTeXに直接埋め込まない。
＊texでそのまま出せない文字の対応も。

最低限エスケープする文字：

\ { } % # & _ $ ^

また、基本的に --shell-escape は使わない。

実装方針
TeXテンプレートは固定
(縦書き)
bot側でJSONデータを生成
JSONから .tex を生成
一時ディレクトリでコンパイル
成功時のみPDFを返す
失敗時はログを管理者向けに返す
timeoutを設定する
同時実行数を制限する

フォント指定もゆくゆくはできれば。

## Discordコマンド設計

```
/pdf submission [kukai_id] [show_author]   ← 投句一覧PDF（管理者限定、publish後から）
/pdf result     [kukai_id] [show_author]   ← 結果PDF（管理者限定、selecting後から）
```

- `kukai_id` 省略時はチャンネルから自動解決
- `show_author`（bool, default=True）: 投句一覧・結果で独立して俳号表示を制御
- `LUALATEX_BIN` が未設定の場合は「有効化されていません」エラーを返す

## 環境切り替え対応

```bash
# .env.example
LUALATEX_BIN=/usr/bin/lualatex   # 空にするとPDF機能を無効化
PDF_SERVE_BASE_URL=               # Caddy公開URL（省略時はサイズ超過でエラー）
PDF_SERVE_DIR=/srv/pdfs
```

ローカル開発時は `LUALATEX_BIN=` で無効化し、Docker環境でのみ有効にする運用が基本。
同一Dockerイメージをローカル・クラウドで共用するため環境差分ゼロ。

## 新規ファイル一覧

| ファイル | 役割 |
|---------|------|
| `Dockerfile` | LuaLaTeX込みのbotイメージ |
| `docker-compose.yml` | ローカル開発・本番共用 |
| `bot/cogs/pdf_cog.py` | コマンド定義 |
| `bot/services/pdf_service.py` | コンパイル・ファイル公開ロジック |
| `bot/templates/pdf/submission_list.tex.j2` | 投句一覧テンプレート |
| `bot/templates/pdf/result.tex.j2` | 結果テンプレート |
| `tests/test_pdf_service.py` | TeX生成・エスケープのテスト |