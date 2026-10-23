# 2026-10-23 セキュリティ更新

Node.js は package.json の系列制約に従い、22.23.4 / 24.22.1 / 26.12.1 以降の修正を含む版を使います。依存は既存の npm / pnpm と lockfile から再現します。

Python の証明書依存は certifi 2026.10.19、urllib3 2.8.0 を修正基準にしています。requirements.in がある場合は `uv pip compile requirements.in --python-version 3.13 --generate-hashes -o requirements.txt` で再生成します。

Playwright の同梱 Node は 24.21.0 のため、`pnpm playwright:install` と `pnpm test:python` を使います。pytest の開始時にも修正版 Node を検証してドライバーへ渡します。古い同梱 Node の指定はテスト開始前に拒否されます。

Collector は 0.163.2、LGTM は 0.37.0 に修正済み CA を組み込んだイメージを使用します。`docker compose -f observability/otel/compose.yaml build --pull --no-cache` で再構築してください。研究結果の runtime / image 記録は実際に実行した版として保持します。

根拠: [Node.js](https://nodejs.org/en/blog/vulnerability/october-2026-security-releases)、[Next.js](https://nextjs.org/blog/nextjs-security-update-october-14-2026)、[certifi](https://github.com/certifi/python-certifi/security/advisories/GHSA-6q48-6c7w-9gw2)、[Python](https://blog.python.org/2026/10/python-3151-3149-31317/)。各リポジトリの実際の適用範囲と検証結果は作業レポートに記録しています。
