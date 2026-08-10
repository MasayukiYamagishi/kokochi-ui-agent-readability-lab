# ローカルOpenTelemetry・LGTM運用手順

## 対象と正本

このディレクトリを、実験に依存しないCollector・Grafana LGTM共通構成の正本とする。
観測基盤は診断用の補助系であり、正本は常に
`experiments/<experiment-id>/results/<tier>/<run-id>/` 以下の不変な実験成果物である。
テレメトリーが停止・欠落しても、生の実験結果を削除・上書きしない。

固定するディストリビューションは次のとおり。

- `otel/opentelemetry-collector:0.156.0`: 上流の最小runtime imageへHTTP
  health probeだけを加えた派生imageで実行する
- `grafana/otel-lgtm:0.27.1`: ローカル開発、デモ、テスト専用

Collector core `0.156.0`は、使用するOTLP receiver、OTLP/debug exporter、
`memory_limiter`・`batch` processor、`health_check` extensionを含む。
`Dockerfile`のGo build stageは標準libraryだけを使う。上流の最小Collector runtimeに
Docker healthcheck用のshell、curl、wgetがないため必要であり、Collector binary自体は
再build・置換しない。

## 起動と準備完了

repository rootから実行する。

```bash
docker compose -f observability/otel/compose.yaml config
docker compose -f observability/otel/compose.yaml up -d --build
docker compose -f observability/otel/compose.yaml ps
```

起動時にrepository管理の実験比較dashboardと、相関設定済みPrometheus・Tempo・Loki・
Pyroscope datasourceを自動provisionする。安定URL、panel定義、filter、screenshot手順、
dashboard smoke testは
[`observability/grafana/README.ja.md`](../grafana/README.ja.md)を参照する。

`otel-collector`は`health_check` extensionが`http://127.0.0.1:13133/`へ
応答してからhealthyになる。実験ランナーのtelemetry factoryはOTLP exporter生成前に
最大30秒このendpointを待つ。準備完了しなければ、そのruntimeのOTLP exportを無効に
してwarningを残すが、実験実行とwrite-once結果保存は継続できる。

対話的な診断時だけdebug exporterを有効にする。

```bash
docker compose \
  -f observability/otel/compose.yaml \
  -f observability/otel/compose.debug.yaml \
  up -d --build
docker compose -f observability/otel/compose.yaml logs -f otel-collector
```

通常構成ではdebug exporterを有効にしない。debug出力には、生prompt、HTML、画像、
credential、provider応答を含めない。

## Pipelineと回復性の判断

trace・metric・logは同じ明示的pipelineを通る。

1. OTLP/gRPCまたはOTLP/HTTPで受信する
2. 最初に`memory_limiter`を適用する
3. ローカルの小規模負荷をbatch化する
4. LGTMのOTLP endpointへ送信する

container上限は256 MiBとし、`memory_limiter`のhard limitを192 MiB、spike allowanceを
64 MiBにする。processとhealth probe用の余裕を残す。batchは1秒または512 itemで
flushする。

LGTM exporterは1回5秒のtimeout、2 consumer・256 requestのin-memory queue、
初回1秒・最大5秒・合計30秒の有界retryを使う。短いLGTM再起動は吸収しつつ、補助系が
実験を長時間止めないための値である。queueは意図的に永続化しないため、Collector再起動
時には未送信telemetryが失われ得る。一方、正本のraw run directoryはwrite-onceで独立
している。telemetryを結果backupとして扱わない。

## Port

公開portはすべて`127.0.0.1`へbindし、LAN・internet公開を意図しない。

| Port | 用途 |
| ---: | --- |
| 13133 | Collector readiness |
| 4317 | OTLP/gRPC入力 |
| 4318 | OTLP/HTTP入力 |
| 3000 | Grafana |
| 3100 | smoke test用Loki API |
| 3200 | smoke test用Tempo API |
| 9090 | smoke test用Prometheus API |

`0.0.0.0`へ変更する場合は、事前に明示的なsecurity reviewを行う。

## Run recordへのimage identity記録

tagは配布物を選び、local content digestは実際に動いた内容を識別する。
`up -d --build`後、各serviceにつきcontainerが1個ずつrunningであることを前提とする。
設定されたtagとserviceの稼働containerを解決し、後から可変tagが指すimageではなく、
container自身の不変image IDを収集して通常の実行環境収集へ渡す。

```python
from pathlib import Path

from kokochi_ui_agent_readability_lab.records import (
    collect_compose_container_images,
    collect_execution_environment,
)

images = collect_compose_container_images(
    Path("observability/otel/compose.yaml"),
)
environment = collect_execution_environment(browser, container_images=images)
```

`container_images`の各要素にCompose service、tag付きreference、local `sha256:` image
digest、Dockerが返す場合はregistry repository digestを記録する。tagを実体へ解決
できない場合、serviceが停止・scaleされている場合、収集中にcontainerが変わった場合は、
曖昧な情報を残さず収集を失敗させる。

## Volumeの用途・初期化・保持期間

`lgtm-data`は`/data`以下のGrafana状態と、local Prometheus、Loki、Tempo、Pyroscope
dataを保持する。通常の`down`、container置換、再起動では残る。診断dataであり、実験
成果物ではない。

labの保持方針は、対応する実験終了から最大7日間とする。all-in-one imageは全backendへ
一律7日を強制しないため、期間終了時にoperatorがvolumeを初期化する。実行中のrunが
ないことを確認してから行う。

```bash
docker compose -f observability/otel/compose.yaml down
docker compose -f observability/otel/compose.yaml down --volumes
docker compose -f observability/otel/compose.yaml up -d --build
```

`down --volumes`はlocal dashboardとtelemetryを完全削除するが、実験結果directoryは
対象にしない。raw実験成果物を`lgtm-data`へコピーしない。

既知の制約として、上流all-in-one LGTM imageでは子processがComposeの30秒grace
periodを超えて残り、終了code 137になる場合がある。named volumeはcontainerと分離
されるが、telemetryは診断dataであり、強制停止をまたぐ完全保持を保証しない。この場合は
stack再起動後に`pnpm observability:smoke`を実行してから新しいtelemetryを利用する。
正本の実験結果directoryには影響しない。

## 状態別の診断

| 状態 | 期待動作 | 診断方法 |
| --- | --- | --- |
| 初回起動 | LGTMがhealthyになった後、Collectorが起動してhealthyになる | `docker compose -f observability/otel/compose.yaml ps`。unhealthyのservice logを確認する |
| Collector再起動 | SDK batchはretryし得る。Collectorの未送信in-memory queueは失われ得る | Collector logとsmoke testを確認する。正本run fileは変化しない |
| Collector停止 | readinessが失敗し、新しいtelemetry runtimeは有界待機後にOTLP exportを無効化する | runner warningとCollector logを確認し、raw結果保存は継続する |
| LGTM停止 | Collectorは受信を継続し、最大30秒queue・retryする | exporter error・dropと、再起動後のLGTM logを確認する |

```bash
docker compose -f observability/otel/compose.yaml ps
docker compose -f observability/otel/compose.yaml logs --tail=200 otel-collector
docker compose -f observability/otel/compose.yaml logs --tail=200 lgtm
```

## 検証とsmoke test

静的Compose検証は`pnpm test`に含め、個別にも実行できる。

```bash
pnpm observability:config
```

stackがhealthyな状態で、production telemetry factoryから一意なdummy trace・metric・
logを送り、Tempo・Prometheus・Lokiで到達を確認する。

```bash
pnpm observability:smoke
```

smoke testはmodelや有料APIを呼ばない。失敗時はlog相関用のrun ID・trace IDを表示する。

決定的なdummy実験runを送り、全表示queryとtrace/log drilldownを検証する場合は次を実行する。

```bash
pnpm observability:dashboard
```
