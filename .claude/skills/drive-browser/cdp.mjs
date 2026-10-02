// Minimal Chrome DevTools Protocol driver. Node 22+ (built-in fetch + WebSocket), no deps.
// usage: node cdp.mjs <tab-url-or-title-substring> <cmd> [args]
//   tabs                       list page tabs (tab arg ignored, pass "-")
//   text                       print document.body.innerText
//   eval <js>                  evaluate (awaits promises), print JSON result
//   nav <url>                  navigate, wait for load + 1.5s settle
//   shot <file.png> [full]     screenshot viewport, or the full page (capped 6000px)
//   click <css-or-text>        click first element matching a CSS selector, else exact visible text
//   size <w> <h>               set the viewport size (device metrics override)
const [,, sel, cmd, a1, a2] = process.argv;
const PORT = process.env.CDP_PORT || 9222;
const all = (await (await fetch(`http://127.0.0.1:${PORT}/json`)).json()).filter(t => t.type === 'page');
if (cmd === 'tabs') { for (const t of all) console.log(t.title, '|', t.url); process.exit(0); }
const t = all.find(t => t.url.includes(sel) || t.title.includes(sel));
if (!t) { console.error('no tab matches', sel, all.map(t => t.url)); process.exit(1); }
const ws = new WebSocket(t.webSocketDebuggerUrl);
await new Promise(r => ws.onopen = r);
let id = 0; const pend = new Map();
ws.onmessage = e => { const m = JSON.parse(e.data); pend.get(m.id)?.(m); pend.delete(m.id); };
const send = (method, params = {}) => new Promise(r => { const i = ++id; pend.set(i, r); ws.send(JSON.stringify({id: i, method, params})); });
const ev = async js => { const m = await send('Runtime.evaluate', {expression: js, returnByValue: true, awaitPromise: true}); return m.result?.exceptionDetails ? {error: m.result.exceptionDetails.text} : m.result?.result?.value; };
const sleep = ms => new Promise(r => setTimeout(r, ms));
if (cmd === 'eval') console.log(JSON.stringify(await ev(a1), null, 1));
else if (cmd === 'text') console.log(await ev('document.body.innerText'));
else if (cmd === 'nav') {
  await send('Page.navigate', {url: a1});
  for (let i = 0; i < 120; i++) { await sleep(500); if (await ev('document.readyState') === 'complete') break; }
  await sleep(1500); console.log(await ev('location.href + " | " + document.title'));
} else if (cmd === 'click') {
  console.log(await ev(`(() => { let el; try { el = document.querySelector(${JSON.stringify(a1)}); } catch {}
    el ??= [...document.querySelectorAll('a,button,[role=button],[role=tab],li,span,div')].find(e => e.innerText?.trim() === ${JSON.stringify(a1)});
    if (!el) return 'not found'; el.scrollIntoView({block: 'center'}); el.click(); return 'clicked ' + el.tagName; })()`));
  await sleep(1500);
} else if (cmd === 'size') {
  await send('Emulation.setDeviceMetricsOverride', {width: +a1, height: +a2, deviceScaleFactor: 1, mobile: false}); console.log('viewport', a1, a2);
} else if (cmd === 'shot') {
  const p = {format: 'png'};
  if (a2) { const [w, h] = JSON.parse(await ev('JSON.stringify([document.documentElement.scrollWidth, document.documentElement.scrollHeight])'));
    Object.assign(p, {captureBeyondViewport: true, clip: {x: 0, y: 0, width: w, height: Math.min(h, 6000), scale: 1}}); }
  const m = await send('Page.captureScreenshot', p);
  (await import('node:fs')).writeFileSync(a1, Buffer.from(m.result.data, 'base64')); console.log('saved', a1);
} else { console.error('unknown cmd', cmd); process.exit(2); }
ws.close();
