# Discord句会bot PDF生成 実装メモ

## 目的

Discord句会botで、句会結果・投句一覧・選評などをLuaLaTeXでPDF化する。

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