// aiops/tests/zabbix-n8n-webhook.test.js
//
// Behavioural test for ansible/roles/zabbix-server/templates/n8n-webhook.js, the
// Zabbix media-type script that sends native events to the AIOps agent (10d2).
// Zabbix runs it in Duktape (ES5) inside the server process, so this shims the
// runtime (HttpRequest, Zabbix.log, the `value` parameter) and runs the script
// as a function body.
//
// Run with the Zabbix server's timezone, since the script converts the server's
// local {EVENT.DATE}/{EVENT.TIME} to UTC with `new Date(y, m, d, ...)`:
//     TZ=Europe/Amsterdam node aiops/tests/zabbix-n8n-webhook.test.js
// (CI: the `aiops` job.) Plain node, no dependencies. Exit 0 = all ok.
'use strict';
const fs = require('fs');
const path = require('path');

const SRC = path.join(__dirname, '..', '..', 'ansible', 'roles', 'zabbix-server', 'templates', 'n8n-webhook.js');
const code = fs.readFileSync(SRC, 'utf8');

let failures = 0;
const check = (name, cond, extra) => {
  console.log((cond ? 'ok   ' : 'FAIL ') + name + (cond ? '' : '  -> ' + extra));
  if (!cond) failures++;
};

// Cheap ES5 tripwire (Duktape has no let/const/arrow/template strings). Not a parser:
// a reviewer still reads the diff, but a stray ES6 token fails CI instead of Zabbix.
const stripped = code.replace(/\/\/.*$/gm, '').replace(/'(?:[^'\\\n]|\\.)*'/g, "''");
check('no ES6 syntax (let/const/arrow/backtick/spread)',
  !/(^|[^\w$.])(let|const)\s/m.test(stripped) && !/=>/.test(stripped) && !/`/.test(stripped) && !/\.\.\./.test(stripped),
  'found an ES6 token');
if (!process.env.TZ) {
  console.log('NOTE: TZ is not set; the timezone cases below assume TZ=Europe/Amsterdam');
}

function run(params, httpStatus = 200) {
  const sent = { headers: [], url: null, body: null };
  const logs = [];
  function HttpRequest() {
    this.addHeader = (h) => sent.headers.push(h);
    this.post = (u, b) => { sent.url = u; sent.body = b; return 'resp'; };
    this.getStatus = () => httpStatus;
  }
  const Zabbix = { log: (l, m) => logs.push(m) };
  const fn = new Function('value', 'HttpRequest', 'Zabbix', code);
  let result, error;
  try { result = fn(JSON.stringify(params), HttpRequest, Zabbix); } catch (e) { error = String(e); }
  return { sent, logs, result, error, payload: sent.body ? JSON.parse(sent.body) : null };
}

const base = {
  n8n_url: 'http://gna.example/webhook/aiops/zabbix', token: 'SECRET-TOKEN-VALUE',
  event_id: '42', event_value: '1', event_name: 'Zabbix agent is unreachable for 5m', severity: 'High',
  host_name: 'canary-1', host_id: '10587', host_ip: '10.0.11.190', host_groups: 'Linux servers,Asgard/LXCs/Canary',
  trigger_id: '23456', trigger_description: 'agent down', trigger_expression: 'nodata(/canary-1/agent.ping,5m)=1', trigger_url: '',
  event_tags_json: '[{"tag":"runbook_id","value":"RB-HOST-HARD-FREEZE"},{"tag":"scope","value":"availability"}]',
  event_date: '2026.10.02', event_time: '19:42:38',
  event_recovery_id: '*UNKNOWN*', event_recovery_date: '*UNKNOWN*', event_recovery_time: '*UNKNOWN*',
  opdata: 'Uptime: 5m',
  item1_name: 'Zabbix agent ping', item1_key: 'agent.ping', item1_value: '0',
  item2_name: '*UNKNOWN*', item2_key: '*UNKNOWN*', item2_value: '*UNKNOWN*',
  item3_name: '{ITEM.NAME3}', item3_key: '{ITEM.KEY3}', item3_value: '{ITEM.VALUE3}',
};

const amsterdam = process.env.TZ === 'Europe/Amsterdam';

let r = run(base);
check('problem posts and returns OK', !r.error && /^OK/.test(r.result), r.error);
if (amsterdam) {
  check('summer time converted to UTC (CEST, -2h)', r.payload.fired_at === '2026-10-02T17:42:38Z', r.payload.fired_at);
}
check('status PROBLEM, severity/host/trigger carried',
  r.payload.status === 'PROBLEM' && r.payload.severity === 'High' && r.payload.host === 'canary-1' && r.payload.trigger_name.indexOf('Zabbix agent') === 0,
  JSON.stringify(r.payload));
check('runbook_id lifted from the trigger tag', r.payload.runbook_id === 'RB-HOST-HARD-FREEZE', r.payload.runbook_id);
check('tags kept as a list', r.payload.tags.length === 2 && r.payload.tags[1].tag === 'scope', JSON.stringify(r.payload.tags));
check('only resolved items kept (1 of 3)', r.payload.items.length === 1 && r.payload.items[0].key === 'agent.ping', JSON.stringify(r.payload.items));
check('recovery fields empty on a problem', r.payload.resolved_at === '' && r.payload.recovery_event_id === '', JSON.stringify([r.payload.resolved_at, r.payload.recovery_event_id]));
check('schema_version and source set', r.payload.schema_version === 'aiops.zabbix-event/v1' && r.payload.source === 'zabbix', JSON.stringify(r.payload));
check('sent_at is UTC ISO', /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$/.test(r.payload.sent_at), r.payload.sent_at);
check('token sent as a header', r.sent.headers.indexOf('X-AIOPS-Token: SECRET-TOKEN-VALUE') >= 0, JSON.stringify(r.sent.headers));
check('token NOT in the body', r.sent.body.indexOf('SECRET-TOKEN') < 0, 'token leaked into the payload');
check('token NOT in the return value or logs', String(r.result).indexOf('SECRET') < 0 && r.logs.join('|').indexOf('SECRET') < 0, 'token leaked');
check('sent to the configured URL', r.sent.url === base.n8n_url, r.sent.url);

if (amsterdam) {
  r = run(Object.assign({}, base, { event_date: '2026.12.01', event_time: '10:00:00' }));
  check('winter time converted to UTC (CET, -1h)', r.payload.fired_at === '2026-12-01T09:00:00Z', r.payload.fired_at);
}

r = run(Object.assign({}, base, { event_value: '0', event_recovery_id: '77', event_recovery_date: '2026.10.02', event_recovery_time: '19:50:00' }));
check('recovery -> RESOLVED with recovery id', r.payload.status === 'RESOLVED' && r.payload.recovery_event_id === '77', JSON.stringify(r.payload));
if (amsterdam) {
  check('recovery time converted to UTC', r.payload.resolved_at === '2026-10-02T17:50:00Z', r.payload.resolved_at);
}

r = run(Object.assign({}, base, { event_tags_json: 'not json', event_date: '{EVENT.DATE}', event_time: '*UNKNOWN*', trigger_description: '{TRIGGER.DESCRIPTION}' }));
check('bad tags JSON / unresolved date do not throw',
  !r.error && r.payload.tags.length === 0 && r.payload.fired_at === '' && r.payload.trigger_description === '', r.error + JSON.stringify(r.payload));
r = run(Object.assign({}, base, { event_tags_json: '[{"tag":"runbook_id","value":"rm -rf /"}]' }));
check('a runbook_id tag that is not RB-... is ignored', r.payload.runbook_id === '', r.payload.runbook_id);

r = run(base, 502);
check('HTTP 502 -> throws, logs the status, never the token',
  /failed/.test(r.error || '') && r.logs.join('|').indexOf('502') >= 0 && (r.error + r.logs.join('|')).indexOf('SECRET') < 0, r.error + r.logs);
r = run(base, 403);
check('HTTP 403 (bad token) -> throws', /403/.test(r.error || ''), r.error);

console.log(failures ? failures + ' FAILURE(S)' : 'all ok');
process.exit(failures ? 1 : 0);
