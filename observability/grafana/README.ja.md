# Grafana実験比較ダッシュボード

## 対象と正本

このディレクトリを、ローカルGrafana実験比較ダッシュボードのrepository管理上の正本と
する。Grafanaは診断用の補助系であり、実験結果の正本は常に
`experiments/<experiment-id>/results/<tier>/<run-id>/`以下の不変なrun成果物である。
dashboard値を正本へ書き戻したり、正本の代用にしたりしない。

dashboardは登録済みmetricを個別に表示し、未登録の複合quality scoreを作らない。
metric名、属性、histogram境界は
[`docs/telemetry-ja.md`](../../docs/telemetry-ja.md)で定義する。

## 配置と安定ID

| repository path                                     | container pathまたはidentity                                                          |
| --------------------------------------------------- | ------------------------------------------------------------------------------------- |
| `provisioning/datasources/grafana-datasources.yaml` | `/otel-lgtm/grafana/conf/provisioning/datasources/grafana-datasources.yaml`           |
| `provisioning/dashboards/kokochi-dashboards.yaml`   | `/otel-lgtm/grafana/conf/provisioning/dashboards/kokochi-dashboards.yaml`             |
| `dashboards/experiment-comparison.json`             | `/otel-lgtm/grafana/conf/provisioning/dashboards/kokochi/experiment-comparison.json`  |
| `dashboards/task-control-discovery.json`            | `/otel-lgtm/grafana/conf/provisioning/dashboards/kokochi/task-control-discovery.json` |
| dashboard folder                                    | `KOKOCHI Experiments`、UID `kokochi-experiments`                                      |
| dashboard                                           | `KOKOCHI experiment comparison`、UID `kokochi-experiment-comparison`                  |
| task dashboard                                      | `KOKOCHI タスク別テレメトリー`、UID `kokochi-task-telemetry`                          |
| datasource                                          | UID `prometheus`、`tempo`、`loki`、`pyroscope`                                        |

各fileは`observability/otel/compose.yaml`から、固定した`grafana/otel-lgtm` imageが
使用する公式pathへread-only mountする。Grafanaはdashboard fileを30秒ごとに再読込する。
datasource・folder・dashboardのUIDは安定させ、container交換後もlinkとtrace/log相関を
保つ。Gitを設定の正本とするためUI更新は無効にする。

## 起動と利用

repository rootから実行する。

```bash
docker compose -f observability/otel/compose.yaml up -d --build
pnpm observability:dashboard
```

`http://localhost:3000/d/kokochi-experiment-comparison`を開く。固定imageではanonymousの
local accessを有効にしている。smoke commandはproduction telemetry APIから、複数条件・
複数modelを含む決定的なdummy run matrixを送り、provisionされたdashboard契約の完全一致、
全表示Prometheus queryとscatter invariant、新しく送ったTempo traceとfilter済みLoki log、
headless Chromiumでの全panel描画を検証する。model・有料APIは呼ばない。dummy dataはservice名
`kokochi-dashboard-smoke-<uuid>`で識別し、正式実験結果として提示しない。

`form-group-membership-reconstruction`の実データは
`http://localhost:3000/d/kokochi-task-telemetry`で確認する。この専用dashboardは
`kokochi.task.id`を共通filterとし、12 cellの実行観測数、target-control-set F1、完全一致、
schema validity、run・推論遅延、provider報告token、失敗分類、Tempo trace、Loki logを
同じtask単位で辿る。記事用スクリーンショットの取得前には、dummyを送信する
`pnpm observability:dashboard`を実行せず、pilot runnerが送信した実データだけを使う。

実パイロット完了後の記事用証跡は、stackが起動している間に次で取得する。

```bash
pnpm observability:task-results
```

このcommandは新しい実験を実行せず、write-onceのpilot正本36件とPrometheusの全実行数・
失敗数・F1観測数、Tempo trace、Lokiのtask metadataを照合する。さらにGrafana全panelと
4 fixtureの代表画像は、Git管理対象外の任意のローカルディレクトリへ保存します。失敗runも12 cellの
実行観測数へ含める一方、scoreが生成されなかったrunをF1 0として扱わない。全runがprovider
段階で失敗した場合、実行数と失敗分類は表示し、F1 panelは`F1なし`を表示するのが正しい。

初期値は直近24時間・全条件で、同一画面上の比較を優先する。filterはexperiment/version、
fixture/version、input representation、provider、model/version、prompt version、scorer
versionを含む。比較条件selectorは汎用F1 panelのgroupingをfixture、input
representation、model間で切り替える。上部のsource path・fixture URLを開く前には、
experimentとfixtureをそれぞれ1件だけ選択する。`All`は比較用正規表現であり、filesystem
pathやfixture URLの有効なsegmentではない。

## Panelの定義

各値は選択時間帯と全filterを適用した後に計算する。

| panel group             | 定義                                                                                                                                                                                                 |
| ----------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| run件数・結果           | terminal `kokochi.experiment.run.count` sampleをsuccess/failureと安定したfailure分類で集計する。                                                                                                     |
| complete match          | `true`のcomplete-match run数を、評価済みschema-invalid応答を含む全評価結果数で割る。                                                                                                                 |
| schema validity         | schema-valid run数を、schema outcomeを記録できた全normalized評価試行数で割る。provider段階のfailureはrun outcome・errorに残すが、この分母には含めない。                                              |
| hallucination-free rate | hallucination countが0のscore済みrun数を、全score済みrun数で割る。                                                                                                                                   |
| item-association F1     | 完全なconfiguration tuple内のrepetitionごとの正確なF1 sampleの平均。95%区間は`mean ± 1.96 × sample標準偏差 / sqrt(n)`の正規近似で、`n < 2`では表示しない。bootstrapや母集団に対する保証ではない。    |
| 分布・変動              | repetitionごとのF1 sampleを表示する。完全なconfiguration tuple内でsample標準偏差÷平均を変動係数とし、minimumをlow outlierにする。条件間差をrepetition varianceとして扱わない。                       |
| latency                 | run単位のE2E durationとprovider inference durationについて、明示的なclassic-histogram bucket内をPrometheusが線形補間したp50/p95推定値。単位は秒。                                                    |
| token                   | provider報告input/output tokenの1 inference当たり平均。推定input tokenとbilling対象tokenを混同しない。                                                                                               |
| qualityとcost           | 完全なconfiguration tuple・repetitionごとに1点を描き、xを同一identityのprovider報告input token平均、yをF1平均にする。複数model/versionを1点へ加算しない。                                            |
| error・trace・log       | 安定したerror stage/classification、対応するTempo `experiment.run` trace、dashboard全体と同じfilterを適用したLoki構造化logを表示する。trace/run IDはtrace・logだけに置き、metric labelには入れない。 |

PromQLはrun/trace IDではなく、事前登録された有限のrepetition indexを使う。
`max_over_time`で各repetition seriesの正確なhistogram/counter sampleを復元してから集計する。
選択時間帯の同一filter tupleで同じrepetition indexを再利用しないことを前提とする。
再実行時はexperiment versionを上げるかlocal診断volumeを初期化する。正本run成果物は
変更しない。

全quantitative panelに単位、legend、threshold、明示的no-data messageを設定する。
空panelは選択条件のtelemetryがないことを表し、score 0を意味しない。trace tableからTempo
trace viewへ移動でき、datasource設定により対応Loki log・Prometheus metricへ移動できる。
Lokiのtrace IDからもTempoへ戻れる。

## Screenshot・review手順

1. fixtureとLGTM stackを起動し、`pnpm observability:dashboard`を実行する。
2. 安定dashboard URLを開き、viewportを1440×900以上にする。
3. source link確認時はexperiment/version・fixture/versionを各1件に絞り、比較画像では
   複数条件を選択する。
4. filterと上部quality panel、latency/token/variability panel、trace/log drilldownを撮影し、
   選択時間帯を見える状態にする。
5. smoke command由来の画像にはdummy dataと明記する。正式結果ではexperiment ID/version、
   Git commit、時間帯、filterを画像と併記する。生prompt、応答、credential、非公開成果物を
   撮影しない。
6. dashboard表示のexperiment directoryとfixture URLが、登録済み
   `experiments/<experiment-id>/` sourceとfixture catalogへ解決することを確認する。

タスク別pilotの記事用画像では、task dashboardを開き、実行run数が36、12 cellの各実行観測数が
3であることを正本run数と照合する。Grafana画像と同じcommit・plan hash・時間帯を記録し、
4 fixtureの`inputs/screenshot.png`はwrite-once pilot成果物から同一SHAの代表画像を選ぶ。
記事本文のF1などの数値はGrafanaから転記せず、正本`outputs/score.json`をtask別に再集計する。
scoreが存在しない失敗runは、失敗件数として報告し、F1の分母や0点へ変換しない。

## Dashboard更新手順

commit済みJSONが正本で、`allowUiUpdates`はfalseである。layout検討時は一時UIDのcopyを
importするか、破棄可能なlocal Grafanaを使う。dashboard JSONをexportし、review済み差分
だけを対応する`dashboards/*.json`へ反映する。安定UIDを維持し、exportで
runtime固有の数値`id`が追加された場合は除去し、2-space JSON formattingとGit diffを
確認する。provision済みdashboardをUI編集しただけで永続化されたと判断しない。

query変更はtelemetry契約と同期する。panelへdataを出す目的でprompt、scorer、fixture、
ground truthを変更しない。次で検証する。

```bash
pnpm observability:config
uv run pytest -v tests/unit/test_grafana_dashboard.py tests/unit/test_telemetry.py
pnpm observability:dashboard
```

unit testはfolder/dashboard/datasource UID、公式mount path、filter初期値、必須panel、metric
名、必須の有限label、PromQLにrun/trace IDを含めないことを固定する。live smoke testは、
Grafanaがcommit済みJSONと異なるdashboard契約を読み込んだ場合、表示queryがdataを返さない
場合、scatterがmodel identity・F1範囲を失った場合、新規Tempo/Loki相関を取得できない場合、
またはChromium上のpanelがclient error/no-dataになった場合に失敗する。

## 既知の制約

- LGTM telemetryはbest-effortであり、保持・欠落特性は
  [`observability/otel/README.ja.md`](../otel/README.ja.md)に従う。
- 95%区間は記述的な正規近似であり、repetition数が少ない場合やscore分布が非正規の場合は
  不安定になり得る。
- dashboard契約導入前に送ったhistogramにはrepetition labelやGenAI共通dimensionがなく、
  一部比較を信頼できない場合がある。
- Grafana linkはfixture開発serverが`localhost:5173`で利用できることを前提とする。

provisioningはGrafana公式の
[dashboard provisioning](https://grafana.com/docs/grafana/latest/administration/provisioning/#dashboards)
と
[datasource provisioning](https://grafana.com/docs/grafana/latest/administration/provisioning/#data-sources)
に従う。container pathは固定した
[`grafana/docker-otel-lgtm`](https://github.com/grafana/docker-otel-lgtm) imageに合わせる。
