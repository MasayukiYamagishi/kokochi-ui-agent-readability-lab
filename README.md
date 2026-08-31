# KOKOCHI UI Agent Readability Lab

[![License](https://img.shields.io/badge/license-PolyForm%20Noncommercial%201.0.0-4c1.svg)](./LICENSE)
[![Security policy](https://img.shields.io/badge/security-policy-blue.svg)](./SECURITY.md)

LLMがWeb UIのHTML構造をどのように解釈するかを、再現可能な条件で
観測・評価するための実験基盤です。

OpenTelemetryを利用し、実験結果に加えて推論・評価処理のtrace、
metrics、logsも記録します。

## このリポジトリについて

この公開版には、Zenn記事「LLMによるUI解釈評価をOpenTelemetryで計測する」
で使用した実験基盤を収録しています。

主な内容:

- `form-group-membership-reconstruction` 実験
- semantic-first / presentation-first のFixture比較
- Ollamaを利用したLLM推論
- control-group-membership F1による評価
- OpenTelemetry + Grafanaによる観測

## 構成

| パス | 内容 |
| --- | --- |
| `apps/fixtures-web/` | 実験用Fixtureを表示するViteアプリ |
| `experiments/` | 実験定義、prompt、ground truth、plan |
| `src/` | Pythonの実験runner・評価・telemetry |
| `observability/` | OpenTelemetry Collector / Grafana / Tempo / Loki / Prometheus |
| `tests/` | 評価・Fixture・telemetry等のテスト |

## 必要環境

- Node.js 24
- pnpm 11.15.1
- Python 3.13
- uv
- Docker / Docker Compose
- Ollama

## セットアップ

```powershell
pnpm install --frozen-lockfile
uv sync --dev
uv run playwright install chromium
```

## Fixtureを確認する

```powershell
pnpm dev
```

ブラウザで <http://localhost:5173/experiments> を開きます。

## Observability stackを起動する

```powershell
docker compose -f observability/otel/compose.yaml up -d --build
```

Grafanaは <http://localhost:3000> で確認できます。

## テスト

```powershell
pnpm typecheck
pnpm build
pnpm test
pnpm validate:repository
```

## 実験について

`experiments/form-group-membership-reconstruction/` 以下に、次の定義を収録しています。

- `config.yaml`
- prompt
- ground truth
- pilot plan
- official plan

## Ollamaについて

Ollamaは同一PCまたはLAN上の別PCで動かせます。
接続先は環境変数で指定してください。

```powershell
$env:OLLAMA_BASE_URL = "http://localhost:11434"
```

## 実験結果について

このリポジトリには正式実験のraw LLM responseやwrite-onceの実行結果を
含めていません。

公開版は、実験基盤と再現に必要な定義・コードを提供することを目的としています。

## ライセンス

本リポジトリのオリジナル部分は
[PolyForm Noncommercial License 1.0.0](./LICENSE)で提供します。
個人の学習、研究、実験、趣味目的などの非商用利用が可能ですが、
商用利用は許可していません。

サードパーティ製ソフトウェア、フォント、資産、コンテナイメージには
それぞれの提供元のライセンスが適用されます。詳細は
[THIRD_PARTY_NOTICES.md](./THIRD_PARTY_NOTICES.md)を参照してください。

## セキュリティ

脆弱性は公開Issueではなく、GitHubのPrivate vulnerability reportingから
報告してください。対象範囲と報告方法は[SECURITY.md](./SECURITY.md)を
参照してください。
