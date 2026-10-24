// Makes the four listing screenshots from a SAMPLE-data dashboard.html via Chrome DevTools Protocol.
// Usage: node --experimental-websocket tests/screenshots.js <dashboard.html> <outdir>
// Needs google-chrome/chromium on PATH (or CHROME env). Exit 0 = pass, 1 = fail, 77 = skipped (no browser).
const { spawn, execSync } = require('child_process');
const fs = require('fs'), os = require('os'), path = require('path'), http = require('http');
const file = path.resolve(process.argv[2]); const outdir = path.resolve(process.argv[3]);
let chrome = process.env.CHROME;
if (!chrome) for (const c of ['google-chrome', 'chromium', 'chromium-browser']) { try { chrome = execSync('command -v ' + c, { stdio: ['ignore', 'pipe', 'ignore'] }).toString().trim(); if (chrome) break; } catch (e) {} }
if (!chrome) { console.log('SKIP: no chrome/chromium'); process.exit(77); }
const udd = fs.mkdtempSync(path.join(os.tmpdir(), 'dash-chrome-'));
const proc = spawn(chrome, ['--headless=new', '--no-sandbox', '--disable-gpu', '--remote-debugging-port=0', '--user-data-dir=' + udd, 'about:blank'], { stdio: 'ignore' });
const sleep = ms => new Promise(r => setTimeout(r, ms));
const getJSON = url => new Promise((res, rej) => http.get(url, r => { let b = ''; r.on('data', d => b += d); r.on('end', () => { try { res(JSON.parse(b)); } catch (e) { rej(e); } }); }).on('error', rej));
let fails = 0;
(async () => {
  let port; for (let i = 0; i < 50 && !port; i++) { await sleep(200); try { port = fs.readFileSync(path.join(udd, 'DevToolsActivePort'), 'utf8').split('\n')[0]; } catch (e) {} }
  if (!port) throw new Error('chrome did not start');
  let targets; for (let i = 0; i < 25; i++) { try { targets = await getJSON(`http://127.0.0.1:${port}/json/list`); if (targets.find(t => t.type === 'page')) break; } catch (e) {} await sleep(200); }
  const ws = new WebSocket(targets.find(t => t.type === 'page').webSocketDebuggerUrl);
  await new Promise(r => ws.addEventListener('open', r));
  let id = 0; const pend = {};
  ws.addEventListener('message', m => { const d = JSON.parse(m.data); if (d.id && pend[d.id]) { pend[d.id](d); delete pend[d.id]; } });
  const send = (method, params = {}) => new Promise(r => { const i = ++id; pend[i] = r; ws.send(JSON.stringify({ id: i, method, params })); });
  const ev = async expr => (await send('Runtime.evaluate', { expression: expr, returnByValue: true })).result.result.value;
  const load = async () => { await send('Page.navigate', { url: 'file://' + file }); for (let i = 0; i < 50; i++) { await sleep(150); if (await ev('!!document.querySelector("#grid .card")')) return; } throw new Error('page did not render'); };
  const snap = async (name, full) => {
    let params = { format: 'png' };
    if (full) { const h = await ev('document.documentElement.scrollHeight'); const w = await ev('document.documentElement.clientWidth'); params = { format: 'png', captureBeyondViewport: true, clip: { x: 0, y: 0, width: w, height: h, scale: 1 } }; }
    const r = await send('Page.captureScreenshot', params); fs.writeFileSync(path.join(outdir, name), Buffer.from(r.result.data, 'base64')); console.log('wrote ' + name);
  };
  await send('Page.enable');
  await send('Emulation.setDeviceMetricsOverride', { width: 1440, height: 900, deviceScaleFactor: 1, mobile: false }); await load(); await sleep(300);
  await snap('preview-desktop.png');
  await ev('document.getElementById("lay").click()'); await sleep(300); await snap('preview-layout-menu.png');
  await send('Emulation.setDeviceMetricsOverride', { width: 390, height: 844, deviceScaleFactor: 1, mobile: true }); await load(); await sleep(300);
  await snap('preview-mobile.png'); await snap('preview-mobile-full.png', true);
  ws.close();
})().catch(e => { console.log('FAIL exception', e.message); fails++; }).finally(() => { try { proc.kill('SIGKILL'); } catch (e) {} try { fs.rmSync(udd, { recursive: true, force: true }); } catch (e) {} process.exit(fails ? 1 : 0); });
