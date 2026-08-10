import assert from 'node:assert/strict'
import { fileURLToPath } from 'node:url'
import test from 'node:test'
import { createServer } from 'vite'

const fixtureHostRoot = fileURLToPath(new URL('../', import.meta.url))

test('Vite development server resolves catalog and direct fixture URLs', async () => {
  const server = await createServer({
    root: fixtureHostRoot,
    optimizeDeps: { noDiscovery: true },
    server: {
      host: '127.0.0.1',
      port: 0,
    },
  })

  try {
    await server.listen()
    const address = server.httpServer?.address()
    assert.ok(address && typeof address === 'object')
    const origin = `http://127.0.0.1:${address.port}`

    for (const pathname of [
      '/experiments',
      '/experiments/fixture-host-contract/stable-url',
      '/experiments/missing-experiment/missing-fixture',
    ]) {
      const response = await fetch(`${origin}${pathname}`, {
        headers: { connection: 'close' },
      })
      assert.equal(response.status, 200)
      assert.match(await response.text(), /<div id="root"><\/div>/)
    }
  } finally {
    server.httpServer?.closeAllConnections()
    await server.close()
  }
})
