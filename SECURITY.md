# Kukai Bot セキュリティ仕様

最終更新: 2026-05-30

## 1. 脅威モデル

このBotは中央ホスト型・セルフホスト型どちらの運用も想定する。
いずれの場合も「一般ユーザーが他サーバーのデータを読める」状態を防ぐことが最重要。

| 脅威 | 対象 |
|---|---|
| ユーザーAが別サーバーのデータをコマンド経由で取得する | 防止済み（後述） |
| 任意の guild_id をパラメータに渡して偽装する | 防止済み（後述） |
| Bot運営者がDBを直接閲覧する | 仕様として許容・プライバシーポリシーで開示 |
| VMへの不正アクセスによるDB取得 | インフラ層で対策（後述） |

---

## 2. アプリケーション層のデータ分離

### 設計方針

一般ユーザーはDBに直接触れず、スラッシュコマンド経由でのみデータにアクセスできる。
guild_id は常に `interaction.guild.id`（Discordが保証する値）から取得し、ユーザーが指定・偽装することは不可能。

### 多層防御の構造

```
Discord Context（interaction.guild.id）
    ↓ 偽装不可
Cog層     ── resolve_kukai_in_channel(guild_id=interaction.guild.id)
    ↓
Service層  ── kukai.guild_id != guild_id なら NotFoundError
    ↓
Repository層 ── .where(Kukai.guild_id == guild_id) で全クエリ絞り込み
    ↓
DB（guild_idカラム・インデックス付き）
```

### 実装確認済みポイント

- **モデル層**: `Kukai`, `NotificationPreset`, `SelectRuleTemplate`, `GuildSettings` すべてに `guild_id` カラムあり。`Entry`, `Submission`, `Select` は `kukai_id` 外部キー経由でguild隔離される。
- **リポジトリ層**: 全クエリに `guild_id` または `kukai_id` フィルターあり。未使用の `get_selects_for_submission()` を除きリスクなし。
- **サービス層**: `kukai_service.get_kukai()` でfetch後に `guild_id` 不一致なら `NotFoundError`（二重防御）。
- **UI層（View/Button）**: ボタンコールバック内でも `kukai_service.get_kukai(session, kukai_id, interaction.guild.id)` で再検証。

**結論: 一般ユーザー間のクロスサーバーデータ漏洩はコード上防がれている。**

---

## 3. DBに保存されるデータの内容

| データ | 内容 | 備考 |
|---|---|---|
| Discord ユーザーID | 数値のみ（名前・メールは含まない） | Discordの仕様上、IDから個人を特定するには権限が必要 |
| 投稿テキスト | 俳句・コメント等 | 平文で保存 |
| サーバーID・チャンネルID | 数値 | |
| 投稿日時・操作ログ | タイムスタンプ | |

メールアドレス・パスワード・決済情報は一切保存しない。
ただし「誰がいつ何を投稿したか」はBot運営者がDBを直接参照すれば把握できる。
**プライバシーポリシーにこの旨を明記すること。**

---

## 4. インフラ層の対策（中央ホスト時）

### VM・OS

```bash
# SSH パスワード認証を無効化（最優先）
# /etc/ssh/sshd_config
PasswordAuthentication no
PermitRootLogin no
```

### Dockerコンテナ

```dockerfile
# root で動かさない
RUN useradd -m botuser
USER botuser
```

### PostgreSQL

```bash
# 5432番ポートをインターネットに公開しない
# DB認証情報は .env に置き、Git管理しない
```

### ネットワーク

- 5432番ポートをインターネットに公開しない
- Docker network / Oracle VCN（仮想ネットワーク）でBot以外からのアクセスを遮断

---

## 5. バックアップのセキュリティ

バックアップファイルは平文のまま外部ストレージに置かない。

```bash
# 暗号化してからObject Storageへアップロード
pg_dump -U kukai -d kukai > kukai_backup.sql
gpg --symmetric --cipher-algo AES256 kukai_backup.sql
# → kukai_backup.sql.gpg を Oracle Object Storage（無料10GB）へ
```

---

## 6. Discord Bot公開時の要件

75サーバー以上に参加する場合、Discordの Bot Verification（審査）が必要。
審査通過のために以下を用意する：

- プライバシーポリシーページ（URL必須）
  - 収集データの種類（上記セクション3）
  - Bot運営者がDBを直接閲覧できる旨
  - データの保持期間・削除ポリシー
- 利用規約ページ（URL必須）
- Privileged Intent（`SERVER MEMBERS INTENT` 等）使用有無の申告

---

## 7. 対応済み / 未対応 一覧

| 項目 | 状態 | 備考 |
|---|---|---|
| クロスサーバーデータ分離 | **対応済み** | 全層でguild_id検証 |
| PostgreSQL移行 | **対応済み** | Compose override + Alembic |
| Dockerをnon-rootで実行 | **未対応** | Dockerfileに追加が必要 |
| SSHパスワード認証無効化 | デプロイ時に設定 | VMセットアップ手順に含める |
| バックアップ暗号化 | **未対応** | PostgreSQL dumpの暗号化とObject Storage連携時に実施 |
| プライバシーポリシー | **未作成** | 公開前に必須 |
| Discord Bot Verification | **未対応** | 75サーバー超時に対応 |
