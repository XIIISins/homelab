import assert from 'node:assert/strict';
import { test } from 'node:test';
import { cmpNumeric, debCompare, upstreamOf } from '../versions.ts';

test('upstreamOf strips distro wrappers', () => {
  assert.equal(upstreamOf('8.4.27-1+0~20261021.57+debian13~1.gbp7636b9'), '8.4.27');
  assert.equal(upstreamOf('8.2.34-1~deb12u1'), '8.2.34');
  assert.equal(upstreamOf('1:8.4.27-r0'), '8.4.27');
  assert.equal(upstreamOf('8.4.27'), '8.4.27');
  assert.equal(upstreamOf('stable'), null);
});

test('cmpNumeric compares numerically, not as text', () => {
  assert.equal(cmpNumeric('8.4.9', '8.4.27'), -1);
  assert.equal(cmpNumeric('8.4.27', '8.4.27'), 0);
  assert.equal(cmpNumeric('8.5.0', '8.4.99'), 1);
});

test('debCompare follows dpkg ordering', () => {
  assert.ok(debCompare('8.4.26-1~deb13u1', '8.4.24-1~deb13u1') > 0);
  assert.ok(debCompare('8.2.32-1~deb12u1', '8.2.34-1~deb12u1') < 0);
  assert.equal(debCompare('8.4.26-1', '8.4.26-1'), 0);
  assert.ok(debCompare('8.4.26-1~deb13u1', '8.4.26-1') < 0, '~ sorts before the plain revision');
  assert.ok(debCompare('1:1.0-1', '9.9-1') > 0, 'epoch wins');
  assert.ok(debCompare('8.4.27-1', '8.4.27-1+b1') < 0);
});
