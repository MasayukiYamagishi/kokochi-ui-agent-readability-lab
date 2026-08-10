# KOKOCHI UI Agent Readability Lab

LLMがWeb UIのHTML構造をどのように解釈するかを、
再現可能な条件で観測・評価するための実験基盤です。

OpenTelemetryを利用して、
実験結果だけでなく推論・評価処理のtrace、metrics、logsも記録します。

## このリポジトリについて

この公開版には、Zenn記事
「LLMによるUI解釈評価をOpenTelemetryで計測する」
で使用した実験基盤を収録しています。

主な実験:

- form-group-membership-reconstruction
- semantic-first / presentation-first のFixture比較
- Ollamaを利用したLLM推論
- control-group-membership F1による評価
- OpenTelemetry + Grafanaによる観測

## 構成

apps/fixtures-web/
実験用Fixtureを表示するViteアプリ

experiments/
実験定義、prompt、ground truth、plan

src/
Pythonの実験runner・評価・telemetry

observability/
OpenTelemetry Collector / Grafana / Tempo / Loki / Prometheus

tests/
評価・Fixture・telemetry等のテスト

## 必要環境

- Node.js 24
- pnpm 11
- Python 3.13
- uv
- Docker / Docker Compose
- Ollama

## セットアップ

pnpm install --frozen-lockfile
uv sync --dev
uv run playwright install chromium

## Fixtureを確認する

pnpm dev

**ブラウザで:**

`http://localhost:5173/experiments`

## Observability stackを起動する

```sh
docker compose -f observability/otel/compose.yaml up -d --build
```

**Grafana:**

`http://localhost:3000`

## テスト

```sh
pnpm test
uv run pytest -v
```

## 実験について

experiments/form-group-membership-reconstruction/

以下を参照してください。

`config.yaml`
`prompt`
`ground truth`
`pilot plan`
`official plan`

## Ollamaについて

Ollamaは同一PCまたはLAN上の別PCで動かせます。

接続先は環境変数で指定してください。

例:

`OLLAMA_BASE_URL=http://localhost:11434`

## 実験結果について

このrepositoryには正式実験のraw LLM responseや
write-onceの実行結果は含めていません。

公開版は実験基盤と再現に必要な定義・コードを提供することを目的としています。

## ライセンス

PolyForm Noncommercial License 1.0.0

個人の学習、研究、実験、趣味目的などの非商用利用が可能です。
商用利用は許可していません。

詳細は LICENSE を参照してください。
