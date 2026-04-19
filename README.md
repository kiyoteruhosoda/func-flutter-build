# func-flutter-build

Azure Functions (Python v2) で実装された、Flutter 向けセルフホスト GitHub Actions Runner を Azure Container Instances (ACI) 上で起動・停止・監視し、ACR でランナーイメージをビルドするためのコントローラ API。

機密値は一切コミットせず、すべて **Azure Function App の Application Settings（環境変数）** と **GitHub Secrets / Variables** で管理する。

---

## エンドポイント

| メソッド | ルート | 概要 |
|---|---|---|
| POST | `/api/start-build` | GitHub Runner 登録トークンを取得し、ACI を起動 |
| GET  | `/api/aci-status` | ACI の状態・exit code・ログを取得 |
| POST | `/api/stop-build` | ACI を削除 |
| POST | `/api/upload-and-build` | `Dockerfile` / `entrypoint.sh` を Blob にアップロードし、ACR クラウドビルドを起動 |

### `POST /api/start-build`
リクエスト:
```json
{ "github_repo": "owner/repo" }
```

### `POST /api/upload-and-build`
`multipart/form-data` で `Dockerfile` / `entrypoint.sh` を送信。
レスポンス例:
```json
{ "uploaded": ["Dockerfile", "entrypoint.sh"], "run_id": "ca1" }
```

---

## ディレクトリ構成

```
.
├── function_app.py              # Functions v2 プログラミングモデルのエントリ
├── host.json                    # Functions ランタイム設定
├── requirements.txt             # Python 依存
├── .funcignore                  # デプロイ ZIP 除外設定
├── local.settings.json.example  # ローカル実行用テンプレート（値は空）
└── .github/workflows/deploy.yml # Azure Functions デプロイワークフロー
```

---

## 必要な環境変数（Function App の Application Settings）

機密値は **絶対にリポジトリへコミットしない**。Azure ポータルの Function App → Configuration、または `az functionapp config appsettings set` で設定する。

| キー | 用途 |
|---|---|
| `AzureWebJobsStorage` | Functions ランタイムが使う Storage 接続文字列 |
| `FUNCTIONS_WORKER_RUNTIME` | `python` 固定 |
| `SUBSCRIPTION_ID` | Azure サブスクリプション ID |
| `ACI_NAME` | 起動対象 ACI 名 |
| `ACI_RG` | ACI / ACR が属するリソースグループ名 |
| `ACI_IMAGE` | ACI で起動するコンテナイメージ（例: `myacr.azurecr.io/flutter-builder:latest`） |
| `ACR_SERVER` | ACR ログインサーバ（例: `myacr.azurecr.io`） |
| `ACR_USER` | ACR ユーザー名 |
| `ACR_PASS` | ACR パスワード |
| `GITHUB_PAT` | GitHub Runner 登録トークン取得用 PAT（`repo` / `admin:repo_hook` 相当） |
| `BUILD_STORAGE_CONNECTION` | Dockerfile コンテキスト用 Storage 接続文字列 |
| `BUILD_STORAGE_ACCOUNT` | 同 Storage アカウント名 |
| `BUILD_STORAGE_KEY` | 同 Storage アクセスキー |

### Managed Identity 権限
Function App の **System-assigned Managed Identity** を有効にし、以下のロールを付与する:

| リソース | ロール |
|---|---|
| ACI（Container Instances） | Contributor |
| ACR（Container Registry） | Contributor |
| Storage Account | Storage Blob Data Contributor |

---

## GitHub からのデプロイ

### 1. Repository variables（公開設定 OK）
`Settings > Secrets and variables > Actions > Variables` に登録:

| 変数名 | 例 |
|---|---|
| `AZURE_RESOURCE_GROUP` | `rg-flutter-build` |
| `AZURE_FUNCTIONAPP_NAME` | `func-flutter-build` |

### 2. Repository secrets（機密）
`Settings > Secrets and variables > Actions > Secrets` に登録:

| シークレット名 | 取得方法 |
|---|---|
| `AZURE_FUNCTIONAPP_PUBLISH_PROFILE` | Azure ポータル → Function App → 「発行プロファイルの取得」で DL した XML の中身をそのまま貼付 |

### 3. デプロイ
- `main` ブランチへの push で自動デプロイ
- 手動実行: Actions タブから `Deploy Azure Function App` → `Run workflow`

---

## ローカル実行

```bash
# 1. 依存インストール
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# 2. 設定ファイル作成（実値を入れる。コミット対象外）
cp local.settings.json.example local.settings.json

# 3. 起動
func start
```

### curl 例
```bash
# ACI 起動
curl -X POST http://localhost:7071/api/start-build \
  -H "Content-Type: application/json" \
  -d '{"github_repo":"owner/repo"}'

# ステータス取得
curl http://localhost:7071/api/aci-status

# ACI 停止
curl -X POST http://localhost:7071/api/stop-build

# Dockerfile アップロード & ビルド
curl -X POST http://localhost:7071/api/upload-and-build \
  -F "Dockerfile=@./Dockerfile" \
  -F "entrypoint.sh=@./entrypoint.sh"
```

---

## 前提 Azure リソース
Function App 本体以外（ACR / ACI / Storage / Managed Identity のロール割当）は**このリポジトリの外で管理**する。本リポジトリのデプロイ対象は Function App（`vars.AZURE_FUNCTIONAPP_NAME`）のみ。
