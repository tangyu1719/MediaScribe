import * as esbuild from 'esbuild';
import fs from 'fs';
import path from 'path';
import { fileURLToPath } from 'url';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const root = path.join(__dirname, '..');
const dist = path.join(root, 'dist');

function copyStatic() {
  fs.mkdirSync(dist, { recursive: true });
  for (const f of ['manifest.json', 'popup/index.html', 'popup/popup.css']) {
    const src = path.join(root, f);
    const dest = path.join(dist, f);
    fs.mkdirSync(path.dirname(dest), { recursive: true });
    fs.copyFileSync(src, dest);
  }
  const iconsSrc = path.join(root, 'icons');
  if (fs.existsSync(iconsSrc)) {
    fs.cpSync(iconsSrc, path.join(dist, 'icons'), { recursive: true });
  }
}

async function build() {
  copyStatic();
  await esbuild.build({
    entryPoints: [
      path.join(root, 'src/background/index.ts'),
      path.join(root, 'src/content/index.ts'),
      path.join(root, 'src/popup/popup.ts'),
    ],
    outdir: path.join(dist),
    outbase: path.join(root, 'src'),
    bundle: true,
    format: 'esm',
    target: 'chrome120',
    sourcemap: true,
  });
  await esbuild.build({
    entryPoints: [path.join(root, 'src/content/fetch-hook.ts')],
    outfile: path.join(dist, 'content/fetch-hook.js'),
    bundle: true,
    format: 'iife',
    target: 'chrome120',
    sourcemap: true,
  });
  console.log('[webreplay] build ok -> dist/');
}

build().catch((e) => {
  console.error(e);
  process.exit(1);
});
