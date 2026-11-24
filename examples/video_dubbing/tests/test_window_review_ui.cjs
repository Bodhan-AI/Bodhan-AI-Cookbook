// Local code simulation only: never launches or records a browser.
const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const page=fs.readFileSync(process.argv[2],'utf8');
const payload=page.match(/<script type="application\/json" id="window-data">([\s\S]*?)<\/script>/)[1];
const script=page.match(/<script>\s*([\s\S]*?)<\/script>/)[1];
assert(!/getUserMedia|MediaRecorder|displayMedia|https?:\/\//.test(script));
(async()=>{
const ids=new Map();
class Element{
  constructor(id=''){this.id=id;this.children=[];this.dataset={};this.attrs={};this.listeners={};this.textContent='';this.disabled=false;this.classes=new Set();this.classList={add:c=>this.classes.add(c),remove:c=>this.classes.delete(c),toggle:(c,v)=>v?this.classes.add(c):this.classes.delete(c)};}
  append(...elements){this.children.push(...elements);}
  addEventListener(event,fn){(this.listeners[event]??=[]).push(fn);}
  fire(event){return Promise.all((this.listeners[event]??[]).map(fn=>fn()));}
  click(){return this.fire('click');}
  setAttribute(k,v){this.attrs[k]=v;}
}
for(const match of page.matchAll(/\bid="([^"]+)"/g))ids.set(match[1],new Element(match[1]));
ids.get('window-data').textContent=payload;
const audio=ids.get('audio'),played=[];audio.currentTime=0;audio.paused=true;audio.pause=()=>audio.paused=true;audio.load=()=>{audio.currentTime=0;};audio.play=()=>{played.push(audio.src);audio.paused=false;return Promise.resolve();};
const document={getElementById:id=>ids.get(id),createElement:()=>new Element(),querySelectorAll:()=>ids.get('rows').children};
vm.runInNewContext(script,{document,console},{timeout:1000});
const data=JSON.parse(payload);
assert.equal(ids.get('rows').children.length,data.windows.length);
assert(data.windows.every(w=>w.duration<=30));
await ids.get('rows').children[0].click();assert.equal(played.at(-1),data.windows[0].file);assert.equal(audio.currentTime,0);
assert.match(ids.get('status').textContent,/Playing the actual request audio/);
if(data.windows.length>1){await ids.get('rows').children[1].click();assert.equal(played.at(-1),data.windows[1].file);audio.currentTime=0.5;await audio.fire('timeupdate');const expected=data.windows[1].start+0.5;const ms=Math.round(expected*1000);assert.equal(ids.get('clock').textContent,`${Math.floor(ms/60000)}:${String(Math.floor(ms%60000/1000)).padStart(2,'0')}.${String(ms%1000).padStart(3,'0')}`);}
const before=played.at(-1);await ids.get('replay').click();assert.equal(played.at(-1),before);
await audio.fire('ended');assert.equal(ids.get('status').textContent,'File finished.');
await ids.get('full').click();assert.equal(played.at(-1),'review_audio.mp3');assert.equal(ids.get('replay').disabled,true);
await audio.fire('error');assert.match(ids.get('status').textContent,/Cannot load/);
console.log('Exact-window UI simulation passed: click plays matching WAV, all durations <=30s, source-clock mapping, replay, full mode, ending and error handling. Real audible output remains a manual check.');
})().catch(error=>{console.error(error);process.exitCode=1;});
