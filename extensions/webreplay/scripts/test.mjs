import assert from 'node:assert/strict';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import * as esbuild from 'esbuild';

async function importTs(file) {
  const result = await esbuild.build({
    entryPoints: [file],
    bundle: true,
    format: 'esm',
    platform: 'node',
    target: 'node20',
    write: false,
  });
  const source = result.outputFiles[0].text;
  return import(`data:text/javascript;base64,${Buffer.from(source).toString('base64')}`);
}

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const timing = await importTs(path.join(root, 'src/shared/replay-timing.ts'));
const frameMatch = await importTs(path.join(root, 'src/shared/frame-match.ts'));

assert.equal(timing.replayDelayMs(undefined, { recordedAt: 1000 }), 0, '首步不等待');
assert.equal(timing.replayDelayMs({ recordedAt: 1000 }, { recordedAt: 1020 }), 50, '过短间隔限流');
assert.equal(timing.replayDelayMs({ recordedAt: 1000 }, { recordedAt: 1825 }), 825, '保留真实间隔');
assert.equal(timing.replayDelayMs({ recordedAt: 1000 }, { recordedAt: 9000 }), 5000, '过长停顿封顶');
assert.equal(timing.replayDelayMs({}, {}), 350, '旧脚本使用兼容间隔');
assert.equal(frameMatch.frameUrlMatches('https://example.com/a?x=1', 'https://example.com/a?x=2'), true);
assert.equal(frameMatch.frameUrlMatches('https://example.com/a', 'https://example.com/b'), false);

console.log('[webreplay] unit tests passed');
