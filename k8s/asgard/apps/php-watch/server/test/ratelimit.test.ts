import assert from 'node:assert/strict';
import { test } from 'node:test';
import { Budget, WindowLimiter } from '../ratelimit.ts';

test('WindowLimiter allows N per window per key', () => {
  let t = 0;
  const l = new WindowLimiter(2, 1000, () => t);
  assert.deepEqual([l.allow('a'), l.allow('a'), l.allow('a'), l.allow('b')], [true, true, false, true]);
  t = 1001;
  assert.equal(l.allow('a'), true);
});

test('WindowLimiter.prune drops idle keys', () => {
  let t = 0;
  const l = new WindowLimiter(1, 1000, () => t);
  l.allow('a');
  t = 5000;
  l.prune();
  assert.equal(l.allow('a'), true);
});

test('Budget is a token bucket', () => {
  let t = 0;
  const b = new Budget(2, 1000, () => t);
  assert.deepEqual([b.take(), b.take(), b.take()], [true, true, false]);
  t = 500; // refills 1 token per 500 ms
  assert.deepEqual([b.take(), b.take()], [true, false]);
});
