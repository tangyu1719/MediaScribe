import fs from 'fs';
import path from 'path';
import { fileURLToPath } from 'url';

/** 最小合法 16x16 橙色 PNG（单像素缩放观感） */
const PNG_16 = Buffer.from(
  'iVBORw0KGgoAAAANSUhEUgAAABAAAAAQCAYAAAAf8/9hAAAAI0lEQVQ4T2NkYGD4z0ABYBw1gGEE0X8M' +
  'DAwMDP8ZGBj+MzAw/APqXQ0AVd0AB3d6nZ8AAAAASUVORK5CYII=',
  'base64'
);

const dir = path.join(path.dirname(fileURLToPath(import.meta.url)), '..', 'icons');
fs.mkdirSync(dir, { recursive: true });
for (const name of ['icon-16.png', 'icon-48.png', 'icon-128.png']) {
  fs.writeFileSync(path.join(dir, name), PNG_16);
}
console.log('[webreplay] icons written');
