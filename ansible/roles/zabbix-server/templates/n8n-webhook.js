// ansible/roles/zabbix-server/templates/n8n-webhook.js
//
// Zabbix -> n8n (Gna, the AIOps diagnosis agent) webhook script. Phase 10d2.
//
// This is the ANALYSIS path. It is independent of the Hermod media type next
// door (hermod-webhook.js, the human-notification path): a failure here only
// fails this media's alert operation in Zabbix and never touches Discord
// delivery. Unlike Hermod's flattened title/body it sends the full event
// context the agent needs: event + recovery ids, trigger id/expression/tags,
// host id/groups, the first item values, and timestamps already converted to
// UTC.
//
// Contract: aiops/schema/zabbix-event.v1.schema.json (fixtures in
// aiops/fixtures/zabbix-native/). The adapter that turns it into an alert is
// aiops/tools/zabbix_event.py. Keep the three in lockstep.
//
// Severity: this script sends whatever the media type's severity bitmask lets
// through (High + Disaster, set in n8n-mediatype.yml). There is deliberately NO
// canary cap here (Hermod caps canaries at `info`): the agent receives the
// canary's real severity and the adapter marks it non-prod, so it is diagnosed
// at `info` priority instead of being invisible.
//
// Duktape (ES5): no let/const/arrow functions/template strings/Array.includes.
// Never log the params object: it carries the ingest token.

function clean(v) {
    // Unresolved Zabbix macros come through as "*UNKNOWN*" or the raw "{MACRO}".
    if (v === null || v === undefined) {
        return '';
    }
    var s = String(v);
    if (s === '*UNKNOWN*' || /^\{[A-Za-z0-9_.:#\/-]+\}$/.test(s)) {
        return '';
    }
    return s;
}

function pad(n) {
    return (n < 10 ? '0' : '') + n;
}

// Zabbix renders {EVENT.DATE} "YYYY.MM.DD" and {EVENT.TIME} "HH:MM:SS" in the
// Zabbix SERVER's local timezone (Hugin: Europe/Amsterdam) with no offset. This
// script runs inside the server process, so `new Date(y, m, d, ...)` interprets
// the components in that same local zone and toISOString() yields exact UTC.
function toIsoUtc(dateStr, timeStr) {
    var d = /^(\d{4})\.(\d{2})\.(\d{2})$/.exec(clean(dateStr));
    var t = /^(\d{2}):(\d{2}):(\d{2})$/.exec(clean(timeStr));
    if (!d || !t) {
        return '';
    }
    var dt = new Date(parseInt(d[1], 10), parseInt(d[2], 10) - 1, parseInt(d[3], 10),
                      parseInt(t[1], 10), parseInt(t[2], 10), parseInt(t[3], 10));
    if (isNaN(dt.getTime())) {
        return '';
    }
    return dt.getUTCFullYear() + '-' + pad(dt.getUTCMonth() + 1) + '-' + pad(dt.getUTCDate()) +
        'T' + pad(dt.getUTCHours()) + ':' + pad(dt.getUTCMinutes()) + ':' + pad(dt.getUTCSeconds()) + 'Z';
}

function nowIsoUtc() {
    var dt = new Date();
    return dt.getUTCFullYear() + '-' + pad(dt.getUTCMonth() + 1) + '-' + pad(dt.getUTCDate()) +
        'T' + pad(dt.getUTCHours()) + ':' + pad(dt.getUTCMinutes()) + ':' + pad(dt.getUTCSeconds()) + 'Z';
}

function parseTags(raw) {
    // {EVENT.TAGSJSON} -> [{"tag": "...", "value": "..."}]
    var s = clean(raw);
    if (!s) {
        return [];
    }
    try {
        var arr = JSON.parse(s);
        var out = [];
        for (var i = 0; i < arr.length; i++) {
            if (arr[i] && arr[i].tag) {
                out.push({tag: String(arr[i].tag), value: String(arr[i].value === undefined ? '' : arr[i].value)});
            }
        }
        return out;
    } catch (e) {
        return [];
    }
}

try {
    var p = JSON.parse(value);

    var problem = p.event_value === '1';
    var tags = parseTags(p.event_tags_json);
    var runbook = '';
    for (var i = 0; i < tags.length; i++) {
        if (tags[i].tag === 'runbook_id' && /^RB-[A-Z0-9]+(-[A-Z0-9]+)*$/.test(tags[i].value)) {
            runbook = tags[i].value;
        }
    }

    var items = [];
    for (var n = 1; n <= 3; n++) {
        var iname = clean(p['item' + n + '_name']);
        var ivalue = clean(p['item' + n + '_value']);
        if (iname || ivalue) {
            items.push({name: iname, key: clean(p['item' + n + '_key']), value: ivalue});
        }
    }

    var payload = {
        schema_version: 'aiops.zabbix-event/v1',
        source: 'zabbix',
        status: problem ? 'PROBLEM' : 'RESOLVED',
        event_id: clean(p.event_id),
        recovery_event_id: problem ? '' : clean(p.event_recovery_id),
        severity: clean(p.severity),
        host: clean(p.host_name),
        host_id: clean(p.host_id),
        host_ip: clean(p.host_ip),
        host_groups: clean(p.host_groups),
        trigger_id: clean(p.trigger_id),
        trigger_name: clean(p.event_name),
        trigger_description: clean(p.trigger_description),
        trigger_expression: clean(p.trigger_expression),
        trigger_url: clean(p.trigger_url),
        tags: tags,
        runbook_id: runbook,
        items: items,
        opdata: clean(p.opdata),
        fired_at: toIsoUtc(p.event_date, p.event_time),
        resolved_at: problem ? '' : toIsoUtc(p.event_recovery_date, p.event_recovery_time),
        sent_at: nowIsoUtc()
    };

    var req = new HttpRequest();
    req.addHeader('Content-Type: application/json');
    req.addHeader('X-AIOPS-Token: ' + p.token);

    var response = req.post(p.n8n_url, JSON.stringify(payload));
    var status = req.getStatus();

    if (status < 200 || status >= 300) {
        // Log the status and the (token-free) response only.
        Zabbix.log(3, '[n8n webhook] HTTP ' + status + ': ' + response);
        throw 'n8n POST returned HTTP ' + status;
    }

    return 'OK (event ' + payload.event_id + ' ' + payload.status + ', HTTP ' + status + ')';

} catch (error) {
    Zabbix.log(3, '[n8n webhook] ' + error);
    throw 'n8n webhook failed: ' + error;
}
