import test from 'node:test';
import assert from 'node:assert/strict';
import { imageRect, imagePoint, boxRect } from '../src/guide-target.js';

test('portrait, landscape letterboxing and front-camera mirroring select the actual image point', () => {
  const rect = imageRect(400,300,900,1600);
  assert.equal(imagePoint(10,100,rect),null); // Never select inside the black margin.
  const p = imagePoint(rect.x+rect.width*.2, rect.y+rect.height*.4,rect,true);
  assert.deepEqual(p,{x:.8,y:.4});
  const b = boxRect([.7,.3,.2,.2],rect,true);
  assert.ok(Math.abs(b.left-(rect.x+.1*rect.width))<.00001);
  const wide=imageRect(300,400,1600,900);
  assert.equal(imagePoint(150,5,wide),null);
  assert.deepEqual(imagePoint(150,200,wide),{x:.5,y:.5});
  assert.equal(imageRect(300,400,0,0),null);
});

test('full-bleed crop maps visible taps and mirrored boxes back to the camera frame', () => {
  const rect = imageRect(300, 400, 1200, 800, 'cover');
  assert.deepEqual(rect, { x: -150, y: 0, width: 600, height: 400 });
  assert.deepEqual(imagePoint(0, 200, rect), { x: .25, y: .5 });
  assert.deepEqual(imagePoint(0, 200, rect, true), { x: .75, y: .5 });
  assert.deepEqual(boxRect([.5, .25, .25, .5], rect, true),
    { left: 0, top: 100, width: 150, height: 200 });
});
