// Real-browser test: drag-to-reorder + localStorage persistence + reset, via Chrome DevTools Protocol.
// Usage: node --experimental-websocket tests/drag_test.js <dashboard.html> [screenshot.png]
// Needs google-chrome/chromium on PATH (or CHROME env). Exit 0 = pass, 1 = fail, 77 = skipped (no browser).
const { spawn, execSync } = require('child_process');
const fs = require('fs'), os = require('os'), path = require('path'), http = require('http');
const file = path.resolve(process.argv[2]); const shot = process.argv[3];
let chrome = process.env.CHROME;
if (!chrome) for (const c of ['google-chrome', 'chromium', 'chromium-browser']) { try { chrome = execSync('command -v ' + c, { stdio: ['ignore', 'pipe', 'ignore'] }).toString().trim(); if (chrome) break; } catch (e) {} }
if (!chrome) { console.log('SKIP: no chrome/chromium'); process.exit(77); }
const udd = fs.mkdtempSync(path.join(os.tmpdir(), 'dash-chrome-'));
const proc = spawn(chrome, ['--headless=new', '--no-sandbox', '--disable-gpu', '--remote-debugging-port=0', '--user-data-dir=' + udd, 'about:blank'], { stdio: 'ignore' });
const sleep = ms => new Promise(r => setTimeout(r, ms));
const getJSON = url => new Promise((res, rej) => http.get(url, r => { let b = ''; r.on('data', d => b += d); r.on('end', () => { try { res(JSON.parse(b)); } catch (e) { rej(e); } }); }).on('error', rej));
let fails = 0; const check = (name, ok, info) => { console.log((ok ? 'PASS ' : 'FAIL ') + name + (info ? ' ' + info : '')); if (!ok) fails++; };
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
  await send('Page.enable'); await send('Emulation.setDeviceMetricsOverride', { width: 1440, height: 1000, deviceScaleFactor: 1, mobile: false });
  await load();
  const order = () => ev('JSON.stringify([...document.querySelectorAll("#grid > .card")].map(c=>c.dataset.pid))').then(JSON.parse);
  const stored = () => ev('localStorage.getItem(KEY)');
  const errs = await ev('document.body.innerText.includes("render failed")'); check('no render failures', !errs);
  const o0 = await order(); check('panels rendered', o0.length >= 3, JSON.stringify(o0));
  check('nothing stored before drag', (await stored()) === null);
  const center = async sel => JSON.parse(await ev(`(()=>{const r=document.querySelector(${JSON.stringify(sel)}).getBoundingClientRect();return JSON.stringify([r.left+r.width/2,r.top+r.height/2])})()`));
  const mouse = (type, x, y) => send('Input.dispatchMouseEvent', { type, x, y, button: type === 'mouseMoved' ? 'none' : 'left', buttons: type === 'mouseReleased' ? 0 : 1, clickCount: 1, pointerType: 'mouse' });
  // quick click on a handle must do nothing
  let [hx, hy] = await center(`#p-${o0[0]} .handle`);
  await mouse('mouseMoved', hx, hy); await mouse('mousePressed', hx, hy); await sleep(80); await mouse('mouseReleased', hx, hy);
  check('quick click does not reorder', JSON.stringify(await order()) === JSON.stringify(o0));
  // press, hold, drag first card onto the last card
  const first = o0[0], last = o0[o0.length - 1];
  [hx, hy] = await center(`#p-${first} .handle`); const [tx, ty] = await center(`#p-${last}`);
  await mouse('mouseMoved', hx, hy); await mouse('mousePressed', hx, hy); await sleep(500);
  check('card lifts after hold', await ev('!!document.querySelector(".card.lifting")'));
  if (shot) { await sleep(100); }
  for (let i = 1; i <= 20; i++) { await mouse('mouseMoved', hx + (tx + 120 - hx) * i / 20, hy + (ty - hy) * i / 20); await sleep(40); }
  await sleep(450); await mouse('mouseMoved', tx + 120, ty); await sleep(450);
  await mouse('mouseReleased', tx + 120, ty); await sleep(200);
  const o1 = await order(); check('order changed after drag', JSON.stringify(o1) !== JSON.stringify(o0) && o1[0] !== first, JSON.stringify(o1));
  check('same panels, none lost', JSON.stringify([...o1].sort()) === JSON.stringify([...o0].sort()));
  const st = JSON.parse(await stored() || 'null'); check('order saved to localStorage', JSON.stringify(st) === JSON.stringify(o1), JSON.stringify(st));
  check('layout menu export shows order', (await ev('document.getElementById("export").value')) === JSON.stringify(o1));
  await load(); check('order persists after reload', JSON.stringify(await order()) === JSON.stringify(o1));
  // corrupt/unknown ids in storage are ignored, missing appended
  await ev(`localStorage.setItem(KEY, JSON.stringify(["nope", ${JSON.stringify(o0[2])}, 5]))`); await load();
  const o2 = await order(); check('bad storage handled', o2[0] === o0[2] && o2.length === o0.length && !o2.includes('nope'), JSON.stringify(o2));
  await ev('document.getElementById("reset").click()'); await sleep(150);
  check('reset restores default + clears storage', JSON.stringify(await order()) === JSON.stringify(o0) && (await stored()) === null);
  if (shot) { await send('Page.bringToFront'); const r = await send('Page.captureScreenshot', { format: 'png' }); fs.writeFileSync(shot, Buffer.from(r.result.data, 'base64')); console.log('screenshot ' + shot); }
  ws.close();
})().catch(e => { console.log('FAIL exception', e.message); fails++; }).finally(() => { try { proc.kill('SIGKILL'); } catch (e) {} try { fs.rmSync(udd, { recursive: true, force: true }); } catch (e) {} process.exit(fails ? 1 : 0); });
