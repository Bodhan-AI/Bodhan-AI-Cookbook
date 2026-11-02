import test from 'node:test';
import assert from 'node:assert/strict';
import { selectedField, replacementValue, sameSelection, selectionPosition } from '../packages/bhashavoice/src/selection.mjs';
const field = (extra={}) => ({tagName:'TEXTAREA', value:'Before hello after', selectionStart:7, selectionEnd:12, isConnected:true, ...extra});
test('captures just selected text and preserves surrounding field contents',()=>{
 const selection=selectedField(field());
 assert.equal(selection.text,'hello');
 assert.equal(replacementValue(selection,'नमस्ते'),'Before नमस्ते after');
});
test('password and unsupported fields are excluded',()=>{
 assert.equal(selectedField(field({tagName:'INPUT',type:'password'})),null);
 assert.equal(selectedField(field({tagName:'INPUT',type:'number'})),null);
 assert.equal(selectedField(field({selectionEnd:7})),null);
});
test('read-only selections can be translated but are not replaceable',()=>{
 assert.equal(selectedField(field({readOnly:true})).editable,false);
 assert.equal(selectedField(field({disabled:true})).editable,false);
});
test('replacement rejects intervening edits and detached fields',()=>{
 const source=field(); const selection=selectedField(source);
 source.value='Changed'; assert.throws(()=>replacementValue(selection,'translated'),/changed/);
 source.value=selection.value; source.isConnected=false;
 assert.throws(()=>replacementValue(selection,'translated'),/changed/);
});

test('launcher follows the selected line, without reserving space for an unopened card', () => {
 const anchor = {left:340, right:430, top:420, bottom:442};
 const result = selectionPosition(anchor, 68, 32, {width:800, height:700});
 assert.equal(result.left, 340);
 assert.equal(result.top, 450);
});

test('cards flip above low selections and stay inside narrow viewport edges', () => {
 const result = selectionPosition({left:295, top:530, bottom:550}, 280, 220, {width:320, height:600});
 assert.equal(result.left, 28);
 assert.equal(result.top, 302);
 assert.equal(result.origin, 'bottom left');
 assert.ok(result.top + 220 < 530);
});

test('long cards receive only the available height without covering their selected line', () => {
 const result = selectionPosition({left:40, right:320, top:250, bottom:270}, 280, 470, {width:360, height:600});
 assert.equal(result.top, 278);
 assert.equal(result.maxHeight, 310);
});

test('repeated field selection events preserve the same popover while edits and offsets invalidate it', () => {
 const source = field();
 const original = selectedField(source);
 assert.equal(sameSelection(original, selectedField(source)), true);
 source.selectionEnd++;
 assert.equal(sameSelection(original, selectedField(source)), false);
 source.selectionEnd--;
 source.value += ' changed';
 assert.equal(sameSelection(original, selectedField(source)), false);
 assert.equal(sameSelection(null, original), false);
});

test('equal paragraph text at different locations is a new selection', () => {
 const node = {};
 const a = {text:'hello', range:{startContainer:node,endContainer:node,startOffset:0,endOffset:5}};
 assert.equal(sameSelection(a, {...a,range:{...a.range}}), true);
 assert.equal(sameSelection(a, {...a,range:{...a.range,startOffset:6,endOffset:11}}), false);
});

test('launcher prefers above selection and flips below when the top is blocked', () => {
 const viewport = {width:600,height:500};
 const above = selectionPosition({left:100,right:160,top:200,bottom:220},70,32,viewport,'top');
 assert.equal(above.side,'top');
 assert.equal(above.top,160);
 const below = selectionPosition({left:100,right:160,top:5,bottom:25},70,32,viewport,'top');
 assert.equal(below.side,'bottom');
 assert.equal(below.top,33);
});

test('tall cards use the right or left side when neither vertical direction fits', () => {
 const viewport = {width:900,height:500};
 const right = selectionPosition({left:40,right:150,top:230,bottom:250},280,350,viewport);
 assert.equal(right.side,'right');
 assert.equal(right.left,158);
 const left = selectionPosition({left:750,right:860,top:230,bottom:250},280,350,viewport);
 assert.equal(left.side,'left');
 assert.equal(left.left,462);
});

test('oversized cards have a bounded height for internal scrolling', () => {
 const position = selectionPosition({left:20,right:310,top:230,bottom:250},280,600,{width:330,height:500});
 assert.ok(position.maxHeight < 600);
 assert.ok(position.top >= 12);
 assert.ok(position.top + position.maxHeight <= 488);
});
