import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import test from 'node:test'


test('fixture HTML mounts the local React entry point', async () => {
  const html = await readFile(new URL('../index.html', import.meta.url), 'utf8')

  assert.match(html, /<div id="root"><\/div>/)
  assert.match(html, /src="\/src\/main\.tsx"/)
  assert.match(html, /KOKOCHI UI Fixture Host/)
})
