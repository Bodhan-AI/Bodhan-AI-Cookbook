// Code-only DOM/media simulation: no browser, recording, or operating-system UI.
const fs = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const page = fs.readFileSync(process.argv[2], 'utf8');
const payload = page.match(/<script id="review-data" type="application\/json">([\s\S]*?)<\/script>/)[1];
const script = page.match(/<script>\s*([\s\S]*?)<\/script>/)[1];
assert(!/getUserMedia|MediaRecorder|displayMedia|<iframe|https?:\/\//.test(script), 'Review must not record or use remote resources.');
(async()=>{
const ids = new Map();
class Element {
  constructor(id='') {this.id=id;this.children=[];this.dataset={};this.style={};this.attrs={};this.textContent='';this.disabled=false;this.listeners={};this.classes=new Set();this.value='';
    this.classList={toggle:(name,on)=>on?this.classes.add(name):this.classes.delete(name),remove:name=>this.classes.delete(name)};}
  addEventListener(name,fn){(this.listeners[name]??=[]).push(fn);}
  removeEventListener(name,fn){this.listeners[name]=(this.listeners[name]??[]).filter(f=>f!==fn);}
  fire(name){return Promise.all([...(this.listeners[name]??[])].map(fn=>fn()));}
  setAttribute(k,v){this.attrs[k]=v;}
  append(...items){this.children.push(...items);}
  replaceChildren(...items){this.children=[...items];}
  insertBefore(item,before){const i=this.children.indexOf(before);this.children.splice(i<0?this.children.length:i,0,item);}
  click(){return this.fire('click');}
}
for(const match of page.matchAll(/\bid="([^"]+)"/g))ids.set(match[1],new Element(match[1]));
ids.get('review-data').textContent=payload;
ids.get('context').value='0';
ids.get('replay').disabled=true;
const audio=ids.get('audio');let currentTime=0;const played=[];let muted=false;Object.defineProperty(audio,'muted',{get:()=>muted,set:value=>{muted=value;if(!value && !audio.paused)played.push(currentTime);}});audio.readyState=0;audio.seeking=false;audio.paused=true;
Object.defineProperty(audio,'currentTime',{get:()=>currentTime,set:value=>{if(audio.readyState<1){currentTime=0;return;}audio.seeking=true;queueMicrotask(()=>{currentTime=value;audio.seeking=false;audio.fire('seeked');});}});
audio.load=()=>queueMicrotask(()=>{audio.readyState=1;currentTime=0;audio.fire('loadedmetadata');});
audio.play=()=>{audio.paused=false;if(!audio.readyState)audio.load();if(!audio.muted)played.push(currentTime);return Promise.resolve();};audio.pause=()=>{audio.paused=true;};
const descendants=el=>[el,...el.children.flatMap(descendants)];
const document={querySelector:selector=>selector==='h1'?new Element():null,getElementById:id=>ids.get(id),createElement:()=>new Element(),querySelectorAll:selector=>selector==='.tabs button'?['filter-speech','filter-gaps','filter-all'].map(id=>ids.get(id)):[...descendants(ids.get('timeline')),...descendants(ids.get('list'))].filter(el=>el.dataset.region)};
let timerId=0;const timers=new Map();
const sandbox={document,console,setInterval:fn=>{timers.set(++timerId,fn);return timerId;},clearInterval:id=>timers.delete(id),setTimeout,clearTimeout};
vm.createContext(sandbox);vm.runInContext(script,sandbox,{timeout:2000});
const data=JSON.parse(payload);
assert.equal(ids.get('list').children.length,data.speech.length);
assert.equal(ids.get('speech-count').textContent,data.speech.length);
const first=data.speech[0];
await ids.get('list').children[0].click();
assert.equal(audio.paused,false);
assert.equal(audio.currentTime,first.start_seconds);assert.equal(played[0],first.start_seconds);
const stop=first.end_seconds;
audio.currentTime=stop+0.02;await Promise.resolve();await audio.fire('timeupdate');await Promise.resolve();
assert.equal(audio.paused,true);assert.equal(audio.currentTime,stop);assert.equal(timers.size,0);
assert.match(ids.get('playback-status').textContent,/Selection finished/);
ids.get('filter-gaps').click();assert.equal(ids.get('list').children.length,data.gaps.length);
ids.get('context').value='0';ids.get('context').fire('change');
await ids.get('list').children[0].click();assert.equal(audio.currentTime,data.gaps[0].start_seconds);
audio.currentTime=data.gaps[0].end_seconds;await Promise.resolve();await audio.fire('timeupdate');await Promise.resolve();assert.equal(audio.paused,true);
ids.get('filter-all').click();assert.equal(ids.get('list').children.length,data.speech.length+data.gaps.length);
await ids.get('play-full').click();assert.equal(audio.currentTime,0);assert.equal(audio.paused,false);assert.equal(timers.size,0);
await ids.get('replay').click();assert.equal(audio.currentTime,data.gaps[0].start_seconds);
ids.get('context').value='1';ids.get('context').fire('change');assert.equal(audio.paused,true);assert.equal(timers.size,0);
await audio.fire('error');assert.match(ids.get('audio-error').textContent,/Cannot load audio/);
console.log('UI simulation passed (including delayed metadata and asynchronous seeks): filters, seeking, context, stop, replay, full playback, errors, and no recording/network APIs. Real browser decoding/timing is not established by this simulation.');

})().catch(error=>{console.error(error);process.exitCode=1;});
