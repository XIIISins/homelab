// ansible/roles/zabbix-server/templates/hermod-webhook.js
//
// Zabbix → Hermod webhook script.
//
// Receives serialized media-type parameters in `value`. Maps Zabbix trigger
// severity to an Apprise tag (Hermod's routing key) and event_value to an
// Apprise type (Discord embed colour). POSTs JSON to the Hermod /notify
// endpoint.
//
// Severity → tag (lives in lockstep with docs/services/notifications.md
// "Source-side severity mapping"):
//   Disaster (5), High (4) → "critical" → Hrist channel (red on problem,
//                                         green on recovery)
//   Average (3)            → "alert"    → Mist channel
//   Warning (2), Info (1), Not classified (0) → suppressed (logs only)
//
// event_value → type:
//   1 (PROBLEM)  → "failure" → red embed
//   0 (RECOVERY) → "success" → green embed

try {
    var params = JSON.parse(value);

    // Map severity name string → Apprise tag. Zabbix passes the LABEL
    // (e.g. "High") via {TRIGGER.SEVERITY}, not the numeric ID.
    var tagMap = {
        'Disaster':       'critical',
        'High':           'critical',
        'Average':        'alert',
        'Warning':         null,
        'Information':     null,
        'Not classified':  null
    };
    var tag = tagMap[params.severity];

    if (tag === undefined || tag === null) {
        // Severity below threshold — VL audit trail captures these via
        // syslog; Discord stays quiet.
        return 'OK (suppressed: severity ' + params.severity + ' below alert threshold)';
    }

    // event_value: "1" = PROBLEM (red), "0" = RECOVERY (green).
    var apprise_type = params.event_value === '0' ? 'success' : 'failure';

    // Body — markdown-formatted. Zabbix's {ALERT.MESSAGE} already contains
    // the rendered template; we just wrap it with a clear header.
    var body_lines = [
        '**Host:** ' + params.host_name,
        '**Severity:** ' + params.severity,
        '',
        params.message
    ];

    var payload = {
        title:  params.subject || params.event_name || 'Zabbix alert',
        body:   body_lines.join('\n'),
        type:   apprise_type,
        tag:    tag,
        format: 'markdown'
    };

    var req = new HttpRequest();
    req.addHeader('Content-Type: application/json');

    var response = req.post(params.hermod_url, JSON.stringify(payload));
    var status = req.getStatus();

    if (status < 200 || status >= 300) {
        Zabbix.log(3, '[Hermod webhook] HTTP ' + status + ': ' + response);
        throw 'Hermod POST returned HTTP ' + status;
    }

    return 'OK (tag=' + tag + ', type=' + apprise_type + ')';

} catch (error) {
    Zabbix.log(3, '[Hermod webhook] ' + error);
    throw 'Hermod webhook failed: ' + error;
}
