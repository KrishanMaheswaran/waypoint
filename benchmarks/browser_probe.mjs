// Read-only recovery probe, launched by the owner harness against its isolated frontend.
import { spawn } from "node:child_process";
import { mkdtemp, readFile, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
const [origin, vehicle, executable] = process.argv.slice(2);
if (new URL(origin).hostname !== "127.0.0.1")
  throw new Error("loopback required");
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const profile = await mkdtemp(join(tmpdir(), "waypoint-m8b-chrome-"));
const chrome = spawn(
  executable,
  [
    "--headless=new",
    "--remote-debugging-port=0",
    `--user-data-dir=${profile}`,
    "--no-first-run",
    "--no-default-browser-check",
    "--disable-background-networking",
    "--disable-component-update",
    "about:blank",
  ],
  { stdio: "ignore" },
);
let port;
for (let n = 0; n < 100; n++) {
  try {
    port = (await readFile(join(profile, "DevToolsActivePort"), "utf8")).split(
      "\n",
    )[0];
    break;
  } catch {
    await sleep(100);
  }
}
async function probe(mode) {
  const target = await (
    await fetch(`http://127.0.0.1:${port}/json/new?about:blank`, {
      method: "PUT",
    })
  ).json();
  const ws = new WebSocket(target.webSocketDebuggerUrl);
  await new Promise((r, j) => {
    ws.addEventListener("open", r, { once: true });
    ws.addEventListener("error", j, { once: true });
  });
  let seq = 0;
  const pending = new Map();
  const errors = [];
  ws.addEventListener("message", (e) => {
    const m = JSON.parse(e.data);
    if (m.method === "Runtime.exceptionThrown")
      errors.push(m.params.exceptionDetails.text);
    if (m.id) {
      const p = pending.get(m.id);
      if (p) {
        pending.delete(m.id);
        clearTimeout(p.timer);
        m.error
          ? p.reject(new Error(JSON.stringify(m.error)))
          : p.resolve(m.result);
      }
    }
  });
  const call = (method, params = {}) =>
    new Promise((resolve, reject) => {
      const id = ++seq;
      const timer = setTimeout(() => reject(new Error("CDP timeout")), 15000);
      pending.set(id, { resolve, reject, timer });
      ws.send(JSON.stringify({ id, method, params }));
    });
  const evaluate = async (expression) => {
    const r = await call("Runtime.evaluate", {
      expression,
      returnByValue: true,
    });
    if (r.exceptionDetails) throw new Error(JSON.stringify(r.exceptionDetails));
    return r.result.value;
  };
  try {
    await call("Page.enable");
    await call("Runtime.enable");
    await call("Page.addScriptToEvaluateOnNewDocument", {
      source: `(()=>{
 const now=()=>performance.now();window.probe={fetches:[],events:[],render:null,source:null};
 const Native=window.EventSource;window.EventSource=class extends Native{constructor(...a){super(...a);window.probe.source=this;if(${JSON.stringify(mode)}==='polling')this.close();for(const k of ['vehicle','resync'])this.addEventListener(k,()=>window.probe.events.push({kind:k,at:now()}));}};
 const original=window.fetch;let filtered=false;window.fetch=async(...a)=>{if(String(a[0])==='/api/vehicles')window.probe.fetches.push(now());const r=await original(...a);if(String(a[0])==='/api/vehicles'&&!filtered){filtered=true;const rows=await r.json();return new Response(JSON.stringify(rows.filter(v=>v.vehicle_id!==${JSON.stringify(vehicle)})),{headers:{'Content-Type':'application/json'}});}return r;};
 document.addEventListener('DOMContentLoaded',()=>new MutationObserver(()=>{if(!window.probe.render&&Array.from(document.querySelectorAll('button.vehicle-link')).some(b=>b.getAttribute('aria-label')===${JSON.stringify(vehicle + ": view latest state and history")}))window.probe.render=now();}).observe(document.body,{subtree:true,childList:true}));})();`,
    });
    await call("Page.navigate", { url: origin });
    let searched = false,
      result;
    for (let n = 0; n < 320; n++) {
      if (
        !searched &&
        (await evaluate(`!!document.querySelector('input[type="search"]')`))
      ) {
        await evaluate(
          `(()=>{const e=document.querySelector('input[type="search"]');Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value').set.call(e,${JSON.stringify(vehicle)});e.dispatchEvent(new Event('input',{bubbles:true}));})()`,
        );
        searched = true;
      }
      result = await evaluate(
        `window.probe?{fetches:probe.fetches,events:probe.events,render:probe.render,state:probe.source?.readyState}:null`,
      );
      if (result?.render) break;
      await sleep(25);
    }
    if (!result?.render || result.fetches.length !== 2 || errors.length)
      throw new Error(
        "recovery DOM assertion failed " + JSON.stringify({ result, errors }),
      );
    if (mode === "polling" && (result.state !== 2 || result.events.length))
      throw new Error("polling client unexpectedly streamed");
    const last = result.fetches.at(-1);
    return {
      ...result,
      fetch_to_dom_ms: result.render - last,
      fetch_interval_ms: result.fetches[1] - result.fetches[0],
      resync_to_dom_ms:
        mode === "live" ? result.render - result.events[0].at : null,
    };
  } finally {
    await call("Page.navigate", { url: "about:blank" });
    ws.close();
  }
}
try {
  if (!port) throw new Error("isolated Chrome did not start");
  const live = await probe("live");
  const polling = await probe("polling");
  console.log(
    JSON.stringify({
      live,
      polling,
      method:
        "Only the first browser fleet response omits one already-persisted vehicle. All subsequent fetch responses are authoritative. This measures missed-snapshot recovery, not insertion-to-render latency.",
      runtime_errors: 0,
    }),
  );
} finally {
  chrome.kill("SIGTERM");
  await new Promise((r) => {
    if (chrome.exitCode !== null) r();
    else {
      chrome.once("exit", r);
      setTimeout(() => {
        chrome.kill("SIGKILL");
        r();
      }, 3000).unref();
    }
  });
  await rm(profile, { recursive: true, force: true });
}
