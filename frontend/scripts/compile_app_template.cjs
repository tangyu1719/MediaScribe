const fs = require('fs');
const path = require('path');
const { compile } = require('@vue/compiler-dom');

const html = fs.readFileSync(path.join(__dirname, '../index.html'), 'utf8');
const m = html.match(/<div id="app" v-cloak>([\s\S]*)<\/div>\s*\n<!-- 主站内嵌 MD/);
if (!m) {
  console.error('cannot extract #app');
  process.exit(1);
}
const template = m[1];
try {
  compile(template, { mode: 'module' });
  console.log('[compile] ok, template length', template.length);
} catch (e) {
  console.error('[compile] FAILED:', e.message);
  if (e.loc) {
    const lines = template.split('\n');
    const line = e.loc.start.line;
    console.error('near line', line, ':', lines[line - 1]);
    console.error('context:', lines.slice(Math.max(0, line - 3), line + 2).join('\n'));
  }
  process.exit(1);
}
