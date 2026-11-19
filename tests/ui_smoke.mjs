// Headless smoke test of ui/index.html logic: minimal DOM shim + real fetch against a running edge server.
// usage: node tests/ui_smoke.mjs http://127.0.0.1:PORT
import fs from "node:fs";
const base = process.argv[2];
const html = fs.readFileSync(new URL("../ui/index.html", import.meta.url), "utf8");
const src = html.match(/<script>([\s\S]*)<\/script>/)[1];

class Txt { constructor(t){this.nodeType=3;this.t=String(t);} get textContent(){return this.t;} }
class El {
  constructor(tag){this.nodeType=1;this.tag=tag;this.children=[];this.attrs={};this.className="";this.style={};this.value="";this.disabled=false;this.l={};}
  setAttribute(k,v){this.attrs[k]=v;if(k==="class")this.className=v;}
  append(...k){for(const x of k)this.children.push(typeof x==="string"?new Txt(x):x);}
  replaceChildren(...k){this.children=[];this.append(...k);}
  addEventListener(e,f){this.l[e]=f;}
  set textContent(v){this.children=[new Txt(v)];}
  get textContent(){return this.children.map(c=>c.textContent).join("");}
  get classList(){const s=this;return{toggle(){},add(){},remove(){}};}
  find(pred,out=[]){if(pred(this))out.push(this);for(const c of this.children)if(c.find)c.find(pred,out);return out;}
}
const reg = {};
const document = {
  createElement:t=>new El(t), createTextNode:t=>new Txt(t),
  querySelector(s){ if(s.startsWith("#")) return reg[s]||(reg[s]=new El("div")); if(s.includes(":checked")) return {value: globalThis.__mode||"answer"}; return new El("div"); },
  querySelectorAll(){return [];},
};
const store={};
const localStorage={getItem:k=>store[k]??null,setItem:(k,v)=>{store[k]=v;}};
const f=(p,o)=>fetch(base+p,o);
const fn=new Function("document","fetch","localStorage","setInterval","setTimeout","clearTimeout","alert",src+"\nreturn {ask,refreshDev,refreshStatus};");
const api=fn(document,f,localStorage,()=>0,()=>0,()=>{},()=>{});
const $=s=>document.querySelector(s);
$("#lang").value="en";
let failed=0; const ok=(c,m)=>{console.log((c?"PASS ":"FAIL ")+m); if(!c)failed++;};

await api.refreshStatus();
ok($("#s-pack").textContent.includes("pack"), "status bar shows pack version");
ok(/DEV PACK/.test($("#s-chan").textContent), "dev pack is labelled on every screen");

$("#q").value="What is the phototherapy threshold for a healthy newborn on day 2?"; await api.ask();
let hl=$("#result").find(e=>e.className==="hl"||(e.attrs&&/\bhl\b/.test(e.attrs.class||"")));
ok(hl.length>0 && hl.some(e=>e.textContent.includes("260 mmol/L")), "table cell Day 2 / phototherapy is highlighted");
ok(!/Any visible jaundice/.test($("#result").find(e=>e.className==="answer").map(e=>e.textContent).join("")), "answer does not mix in the Day 1 cell");
ok(/verified/.test($("#result").textContent), "verified badge shown");
ok(/⚠/.test($("#result").textContent), "unit warning marker shown on mmol/L cells");

$("#q").value="What is the capital of France?"; await api.ask();
ok(/No reliable source found/.test($("#result").textContent), "out-of-scope question shows the abstain state");

globalThis.__mode="passages"; $("#q").value="ketamine dose for intubation"; await api.ask();
ok(/Source passages|Closest documents/.test($("#result").textContent), "passages mode renders cards");

await api.refreshDev();
ok($("#p-stages").find(e=>e.tag==="tr").length>1, "pipeline stages table rendered");
ok($("#e-gates").find(e=>e.tag==="tr").length>=8, "eval gates table rendered");
ok(/NOT release eligible|Release eligible/.test($("#e-banner").textContent), "release banner rendered");
ok($("#u-state").textContent.includes("Active"), "update panel rendered");
process.exit(failed?1:0);
