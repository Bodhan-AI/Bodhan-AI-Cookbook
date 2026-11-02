import test from "node:test";
import assert from "node:assert/strict";
import { animateCardOpening, cardOpeningFrames } from "../packages/bhashavoice/src/cardMotion.mjs";

test("opening grows from launcher dimensions and accounts for placement on every side", () => {
  const from = {left:300,top:300,width:40,height:40};
  for (const to of [
    {left:300,top:350,width:280,height:200},
    {left:300,top:50,width:280,height:200},
    {left:0,top:300,width:280,height:200},
    {left:350,top:300,width:280,height:200},
  ]) {
    const frames = cardOpeningFrames(from,to);
    assert.equal(frames[0].transform,`translate(${from.left-to.left}px, ${from.top-to.top}px) scale(${40/280}, ${40/200})`);
    assert.equal(frames.at(-1).transform,"none");
    assert.equal(frames.at(-1).opacity,1);
  }
});

test("animation respects reduced motion and missing or unmeasurable elements", () => {
  const from={left:0,top:0,width:36,height:36};
  const reduced={matchMedia:()=>({matches:true})};
  animateCardOpening({animate:()=>assert.fail("reduced motion must not animate")},from,reduced)();
  animateCardOpening({},from,{})();
  animateCardOpening(null,from,{})();
  animateCardOpening({animate:()=>assert.fail("zero size"),getBoundingClientRect:()=>({width:0,height:0})},from,{matchMedia:()=>({matches:false})})();
});

test("opening runs once and returns cancellation for unmount or interrupted transitions", () => {
  let calls=0, cancelled=0;
  const stop=animateCardOpening({
    getBoundingClientRect:()=>({left:10,top:20,width:280,height:240}),
    animate(frames,options) {
      calls++;
      assert.equal(options.duration,480);
      assert.equal(frames[0].transformOrigin,"top left");
      return {cancel(){cancelled++;}};
    },
  }, {left:10,top:300,width:36,height:36}, {matchMedia:()=>({matches:false})});
  assert.equal(calls,1);
  stop();
  assert.equal(cancelled,1);
});
