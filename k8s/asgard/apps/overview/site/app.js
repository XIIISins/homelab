// Niflheim overview: fills in the numbers on a page that is already complete
// as static HTML (machines, app buttons), so first paint never waits on this file.
//
// Metrics come from /api/v1/query on this same origin; Caddy forwards that one
// read-only path to VictoriaMetrics. All kube_* queries pin
// job="kube-state-metrics": kube-state-metrics is also picked up by the
// kubernetes-service-endpoints job, which doubles every sum otherwise.
'use strict';

(() => {
  const POLL_MS = 30_000;       // vmagent scrapes every 30 s, so faster is pointless
  const STALE_MS = 120_000;
  const PROBE_MS = 120_000;
  const TIMEOUT_MS = 10_000;
  const HIGH = 0.85;            // CPU or memory share that counts as "watch this"

  const KSM = 'job="kube-state-metrics"';
  const QUERIES = {
    nodeReady: `max by(node)(kube_node_status_condition{${KSM},condition="Ready",status="true"})`,
    cpuUse: 'sum by(instance)(rate(container_cpu_usage_seconds_total{image!=""}[5m]))',
    cpuCap: `max by(node)(kube_node_status_allocatable{${KSM},resource="cpu"})`,
    memUse: 'sum by(instance)(container_memory_working_set_bytes{image!=""})',
    memCap: `max by(node)(kube_node_status_allocatable{${KSM},resource="memory"})`,
    pods: `sum(kube_pod_status_phase{${KSM},phase="Running"})`,
    podsBad: `sum(kube_pod_status_phase{${KSM},phase=~"Pending|Failed|Unknown"})`,
    restarts: `sum(increase(kube_pod_container_status_restarts_total{${KSM}}[1h]))`,
    deploysShort: `count(kube_deployment_spec_replicas{${KSM}} > kube_deployment_status_replicas_available{${KSM}}) or vector(0)`,
    volumes: 'topk(3, 100 * kubelet_volume_stats_used_bytes / kubelet_volume_stats_capacity_bytes)',
    traffic: 'sum(rate(traefik_service_requests_total[5m]))',
    certsReady: 'count(certmanager_certificate_ready_status{condition="True"} == 1)',
    certsAll: 'count(certmanager_certificate_ready_status{condition="True"})',
    certExpiry: 'min(certmanager_certificate_expiration_timestamp_seconds) - time()',
  };

  const FAVICON = { ok: '#1b7360', warn: '#9a5b00', bad: '#ae3526', unknown: '#86a1b0' };

  const $ = (sel, root = document) => root.querySelector(sel);
  const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));
  const pl = (n, one, many) => (n === 1 ? one : many);

  // ---------- Reading metrics ----------

  async function instant(expr, signal) {
    const res = await fetch('/api/v1/query?' + new URLSearchParams({ query: expr }), { signal, cache: 'no-store' });
    if (!res.ok) throw new Error('HTTP ' + res.status);
    const body = await res.json();
    if (body.status !== 'success') throw new Error(body.error || 'query failed');
    return body.data.result;
  }

  async function readAll() {
    const ctl = new AbortController();
    const timer = setTimeout(() => ctl.abort(), TIMEOUT_MS);
    try {
      const entries = await Promise.all(Object.entries(QUERIES).map(async ([key, expr]) => {
        try { return [key, await instant(expr, ctl.signal)]; } catch { return [key, null]; }
      }));
      if (entries.every(([, v]) => v === null)) throw new Error('no answer');
      return Object.fromEntries(entries);
    } finally {
      clearTimeout(timer);
    }
  }

  // A scalar query: null when the query failed, 0 when it returned nothing.
  const scalar = (r) => (r === null ? null : r.length ? Number(r[0].value[1]) : 0);
  const byLabel = (r, label) => new Map((r || []).map((s) => [s.metric[label], Number(s.value[1])]));

  // ---------- Machine blocks ----------

  const vms = new Map();
  for (const el of $$('.vm')) vms.set(el.dataset.node, bindVM(el));

  function bindVM(el) {
    const meter = (kind) => {
      const m = $(`.meter[data-kind="${kind}"]`, el);
      return { bar: $('.m-ring', m), val: $('.m-val', m), sub: $('.m-sub', m) };
    };
    return { el, flag: $('.vm-flag', el), cpu: meter('cpu'), mem: meter('mem') };
  }

  // A node the page does not list (a future worker, say) still gets shown.
  function ensureVM(node) {
    if (vms.has(node)) return vms.get(node);
    let card = $('.machine[data-extra]');
    if (!card) {
      card = document.createElement('article');
      card.className = 'machine';
      card.dataset.extra = '';
      const title = document.createElement('h3');
      title.className = 'machine-name';
      title.textContent = 'Other nodes';
      card.append(title);
      $('.machines').append(card);
    }
    const el = $('.vm').cloneNode(true);
    el.dataset.node = node;
    $('.vm-name', el).textContent = node;
    $('.vm-role', el).remove();   // the role of an unlisted node is not known
    for (const ring of $$('.m-ring', el)) {
      const kind = ring.closest('.meter').dataset.kind === 'cpu' ? 'CPU' : 'Memory';
      ring.setAttribute('aria-label', `${node} ${kind} use`);
    }
    card.append(el);
    const vm = bindVM(el);
    vms.set(node, vm);
    return vm;
  }

  function setMeter(m, frac, valText, subText) {
    if (frac === null) {
      m.bar.style.setProperty('--v', 0);
      m.bar.removeAttribute('data-high');
      m.bar.removeAttribute('aria-valuenow');
      m.val.textContent = '–';
      m.sub.textContent = subText || ' ';
      return;
    }
    const clamped = Math.max(0, Math.min(1, frac));
    m.bar.style.setProperty('--v', clamped);
    m.bar.toggleAttribute('data-high', clamped >= HIGH);
    m.bar.setAttribute('aria-valuenow', String(Math.round(clamped * 100)));
    m.bar.setAttribute('aria-valuetext', `${valText}, ${subText}`);
    m.val.textContent = valText;
    m.sub.textContent = subText;
  }

  const percent = (f) => (f > 0 && f < 0.01 ? '<1%' : Math.round(f * 100) + '%');
  const cores = (n) => (n < 10 ? n.toFixed(2) : n.toFixed(1));
  const gib = (bytes) => {
    const g = bytes / 2 ** 30;
    return g >= 10 ? String(Math.round(g)) : g.toFixed(1);
  };

  function showVM(node, ready, cpu, cpuCap, mem, memCap) {
    const vm = ensureVM(node);
    const cpuFrac = cpu != null && cpuCap ? cpu / cpuCap : null;
    const memFrac = mem != null && memCap ? mem / memCap : null;
    setMeter(vm.cpu, cpuFrac, cpuFrac === null ? '' : percent(cpuFrac),
      cpuFrac === null ? '' : `${cores(cpu)} of ${cpuCap} ${pl(cpuCap, 'core', 'cores')}`);
    setMeter(vm.mem, memFrac, memFrac === null ? '' : percent(memFrac),
      memFrac === null ? '' : `${gib(mem)} of ${gib(memCap)} GiB`);
    // ready: 1 ready, 0 not ready, undefined absent from the cluster, null unknown (no data).
    const flag = ready === 0 ? 'Not ready' : ready === undefined ? 'Not in the cluster' : '';
    vm.flag.textContent = flag;
    vm.flag.hidden = flag === '';
    return { cpuFrac, memFrac };
  }

  // ---------- Verdict and facts ----------

  function setVerdict(state, title, detail, issues) {
    const section = $('#verdict');
    section.dataset.state = state;
    $('#verdict-title').textContent = title;
    $('#verdict-detail').textContent = detail || ' ';
    const list = $('#attention');
    list.replaceChildren(...issues.map((i) => {
      const li = document.createElement('li');
      li.dataset.level = i.level;
      li.textContent = i.text;
      return li;
    }));
    list.hidden = issues.length === 0;

    const short = state === 'ok' ? 'all running' : state === 'unknown' ? 'no data'
      : `${issues.length} to check`;
    document.title = `Niflheim: ${short}`;
    const svg = `<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 32 32'><circle cx='16' cy='16' r='13' fill='${FAVICON[state]}'/></svg>`;
    $('#favicon').href = 'data:image/svg+xml,' + encodeURIComponent(svg);
  }

  function setFact(id, text, small, level) {
    const fact = $('#' + id);
    const dd = $('dd', fact);
    dd.replaceChildren(text);
    if (small) {
      const s = document.createElement('small');
      s.textContent = small;
      dd.append(s);
    }
    if (level) fact.dataset.level = level; else fact.removeAttribute('data-level');
  }

  function duration(seconds) {
    const days = Math.floor(seconds / 86400);
    if (days >= 1) return `${days} ${pl(days, 'day', 'days')}`;
    const hours = Math.max(0, Math.floor(seconds / 3600));
    return `${hours} ${pl(hours, 'hour', 'hours')}`;
  }

  function render(d) {
    const issues = [];
    const add = (level, text) => issues.push({ level, text });

    const ready = byLabel(d.nodeReady, 'node');
    const cpuUse = byLabel(d.cpuUse, 'instance');
    const cpuCap = byLabel(d.cpuCap, 'node');
    const memUse = byLabel(d.memUse, 'instance');
    const memCap = byLabel(d.memCap, 'node');

    // Nodes listed on the page, then any extra the cluster reports.
    const nodes = new Set([...vms.keys(), ...ready.keys()]);
    for (const node of nodes) {
      const r = d.nodeReady === null ? null : ready.get(node);
      const { cpuFrac, memFrac } = showVM(node, r, cpuUse.get(node), cpuCap.get(node), memUse.get(node), memCap.get(node));
      if (r === undefined) add('bad', `${node} is missing from the cluster.`);
      else if (r === 0) add('bad', `${node} is not ready.`);
      if (cpuFrac !== null && cpuFrac >= HIGH) add('warn', `${node} is using ${percent(cpuFrac)} of its CPU.`);
      if (memFrac !== null && memFrac >= HIGH) add('warn', `${node} is using ${percent(memFrac)} of its memory.`);
    }

    const total = ready.size;
    const readyCount = Array.from(ready.values()).filter((v) => v === 1).length;
    const pods = scalar(d.pods);
    const podsBad = scalar(d.podsBad);
    const restarts = d.restarts === null ? null : Math.round(scalar(d.restarts));
    const short = scalar(d.deploysShort);

    if (podsBad) add('bad', `${podsBad} ${pl(podsBad, 'pod is', 'pods are')} pending or failing.`);
    if (short) add('warn', `${short} ${pl(short, 'deployment has', 'deployments have')} fewer replicas than wanted.`);
    if (restarts !== null && restarts >= 5) add('warn', `${restarts} containers restarted in the last hour.`);

    // Facts
    const traffic = scalar(d.traffic);
    setFact('f-traffic', traffic === null ? '–' : `${traffic < 10 ? traffic.toFixed(1) : Math.round(traffic)} requests/s`, 'Through Traefik');

    setFact('f-restarts', restarts === null ? '–' : restarts === 0 ? 'None' : String(restarts),
      restarts === null ? '' : 'Container restarts', restarts >= 5 ? 'warn' : '');

    const vol = d.volumes && d.volumes[0];
    if (vol) {
      const pct = Math.round(Number(vol.value[1]));
      const level = pct >= 95 ? 'bad' : pct >= 85 ? 'warn' : '';
      setFact('f-volume', `${pct}% full`, `${vol.metric.persistentvolumeclaim} in ${vol.metric.namespace}`, level);
      if (level) add(level, `Volume ${vol.metric.persistentvolumeclaim} in ${vol.metric.namespace} is ${pct}% full.`);
    } else {
      setFact('f-volume', '–', '');
    }

    const certsAll = scalar(d.certsAll);
    const certsReady = scalar(d.certsReady);
    const expiry = scalar(d.certExpiry);
    if (certsAll === null) {
      setFact('f-certs', '–', '');
    } else {
      const days = expiry === null ? null : expiry / 86400;
      const level = certsReady < certsAll || (days !== null && days < 7) ? 'bad' : days !== null && days < 14 ? 'warn' : '';
      setFact('f-certs', `${certsReady} of ${certsAll} valid`, expiry === null ? '' : `Soonest expiry in ${duration(expiry)}`, level);
      if (certsReady < certsAll) add('bad', `${certsAll - certsReady} ${pl(certsAll - certsReady, 'certificate is', 'certificates are')} not ready.`);
      else if (level) add(level, `A certificate expires in ${duration(expiry)}.`);
    }

    // Headline
    issues.sort((a, b) => (a.level === b.level ? 0 : a.level === 'bad' ? -1 : 1));
    const bad = issues.filter((i) => i.level === 'bad').length;
    let state = 'ok';
    let title = 'Everything is running.';
    if (bad) {
      state = 'bad';
      title = `${issues.length} ${pl(issues.length, 'thing needs', 'things need')} attention.`;
    } else if (issues.length) {
      state = 'warn';
      title = `Running, with ${issues.length} ${pl(issues.length, 'thing', 'things')} to watch.`;
    }

    const parts = [];
    if (total) parts.push(readyCount === total ? `All ${total} nodes are ready` : `${readyCount} of ${total} nodes are ready`);
    if (pods !== null) parts.push(`${pods} ${pl(pods, 'pod is', 'pods are')} running`);
    let detail = parts.length ? parts.join(' and ') + '.' : '';
    if (restarts === 0) detail += ' Nothing restarted in the last hour.';
    else if (restarts) detail += ` ${restarts} ${pl(restarts, 'container restarted', 'containers restarted')} in the last hour.`;

    setVerdict(state, title, detail.trim(), issues);
  }

  function renderOutage() {
    for (const [node] of vms) showVM(node, null, null, null, null, null);
    for (const id of ['f-traffic', 'f-restarts', 'f-volume', 'f-certs']) setFact(id, '–', '');
    setVerdict('unknown', "Can't read metrics.",
      'VictoriaMetrics did not answer, so the figures are blank. The app buttons still work. Trying again every 30 seconds.', []);
  }

  // ---------- Polling and timestamp ----------

  let lastOk = 0;
  let lastTry = 0;
  let firstRender = true;
  let timer;

  function tickStamp() {
    const pill = $('#stamp');
    const label = $('#stamp-text');
    pill.removeAttribute('data-stale');
    pill.removeAttribute('data-down');
    if (!lastOk) {
      if (lastTry) pill.dataset.down = '';
      label.textContent = lastTry ? 'No metrics yet' : 'Reading metrics…';
      return;
    }
    const age = Date.now() - lastOk;
    const secs = Math.round(age / 1000);
    const text = secs < 10 ? 'just now' : secs < 90 ? `${secs} seconds ago` : `${Math.round(secs / 60)} minutes ago`;
    if (age > STALE_MS) {
      pill.dataset.stale = '';
      label.textContent = `Last good reading ${text}`;
    } else {
      label.textContent = `Updated ${text}`;
    }
  }

  async function refresh() {
    clearTimeout(timer);
    lastTry = Date.now();
    try {
      const data = await readAll();
      if (firstRender) staggerMeters();   // the delay must be set before --v changes
      render(data);
      lastOk = Date.now();
      if (firstRender) settleMotion();
    } catch {
      renderOutage();
    }
    tickStamp();
    schedule();
    // Probes are the lowest priority: they wait for the first metrics attempt
    // (success or not, since an outage is when the dots matter most) and for idle time.
    if (!probing) {
      if ('requestIdleCallback' in window) window.requestIdleCallback(startProbes, { timeout: 2000 });
      else setTimeout(startProbes, 300);
    }
  }

  function schedule() {
    clearTimeout(timer);
    timer = setTimeout(() => (document.hidden ? schedule() : refresh()), POLL_MS);
  }

  // The meters fill in once, staggered. After that, updates should be immediate.
  function staggerMeters() {
    $$('.m-ring').forEach((ring, i) => ring.style.setProperty('--i', i));
  }

  function settleMotion() {
    firstRender = false;
    setTimeout(() => $$('.m-ring').forEach((ring) => ring.style.removeProperty('--i')), 2000);
  }

  document.addEventListener('visibilitychange', () => {
    if (document.hidden) return;
    if (Date.now() - lastTry > POLL_MS) refresh();
    // A tab opened in the background has never probed; one left for a while is stale.
    if (probing && Date.now() - lastProbe >= PROBE_MS / 2) runProbes();
  });
  setInterval(tickStamp, 5000);

  // ---------- Reachability dots ----------
  // A no-cors fetch from this browser: it only tells whether something answered
  // over HTTPS, which is exactly "can I open this from here".

  async function probe(a) {
    const ctl = new AbortController();
    const t = setTimeout(() => ctl.abort(), 5000);
    let up = 0;
    try {
      await fetch(a.href, { mode: 'no-cors', cache: 'no-store', credentials: 'omit', signal: ctl.signal, priority: 'low' });
      up = 1;
    } catch { /* unreachable, blocked or a bad certificate */ }
    clearTimeout(t);
    a.dataset.up = String(up);
    $('.status', a).textContent = up ? 'Reachable' : 'Not reachable from here';
  }

  let probing = false;
  let lastProbe = 0;

  function runProbes() {
    if (document.hidden) return;
    lastProbe = Date.now();
    $$('.app-link[data-probe]').forEach((a, i) => setTimeout(() => probe(a), i * 80));
  }

  function startProbes() {
    if (probing) return;
    probing = true;
    runProbes();
    setInterval(() => { if (Date.now() - lastProbe >= PROBE_MS) runProbes(); }, 10_000);
  }

  // ---------- Finder ----------

  const find = $('#find');
  const none = $('#none');

  find.addEventListener('input', () => {
    const words = find.value.trim().toLowerCase().split(/\s+/).filter(Boolean);
    let any = false;
    for (const li of $$('.app')) {
      const hit = words.every((w) => li.dataset.keywords.includes(w));
      li.hidden = !hit;
      any = any || hit;
    }
    for (const g of $$('.group')) g.hidden = $$('.app:not([hidden])', g).length === 0;
    none.hidden = any;
  });

  // Rail first, then the everyday tiles: the order the arrow keys walk in.
  const visibleLinks = () => $$('.rail .app:not([hidden]) a, .quick .app:not([hidden]) a');

  find.addEventListener('keydown', (e) => {
    if (e.key === 'Enter') {
      const first = visibleLinks()[0];
      if (first && find.value.trim()) first.click();
    } else if (e.key === 'ArrowDown') {
      const first = visibleLinks()[0];
      if (first) { e.preventDefault(); first.focus(); }
    } else if (e.key === 'Escape') {
      find.value = '';
      find.dispatchEvent(new Event('input'));
      find.blur();
    }
  });

  // Arrow keys walk the visible buttons; going up from the first returns to the search box.
  document.addEventListener('keydown', (e) => {
    const forward = e.key === 'ArrowDown' || e.key === 'ArrowRight';
    const back = e.key === 'ArrowUp' || e.key === 'ArrowLeft';
    if (!forward && !back) return;
    const links = visibleLinks();
    const i = links.indexOf(document.activeElement);
    if (i === -1) return;
    e.preventDefault();
    if (forward) (links[i + 1] || links[i]).focus();
    else if (i === 0) find.focus();
    else links[i - 1].focus();
  });

  document.addEventListener('keydown', (e) => {
    const typing = /^(INPUT|TEXTAREA|SELECT)$/.test(document.activeElement && document.activeElement.tagName);
    const slash = e.key === '/' && !e.metaKey && !e.ctrlKey && !e.altKey && !typing;
    const palette = e.key.toLowerCase() === 'k' && (e.metaKey || e.ctrlKey);
    if (slash || palette) {
      e.preventDefault();
      find.focus();
      find.select();
    }
  });

  // ---------- Pinning the rail ----------
  // Hover opens the rail over the page; the pin keeps it open and gives it its own
  // column. The choice is remembered in this browser (and works without storage).

  const shell = $('#shell');
  const pin = $('#pin');
  const PIN_KEY = 'overview.railPinned';

  function setPinned(on) {
    shell.toggleAttribute('data-pinned', on);
    pin.setAttribute('aria-pressed', String(on));
    pin.title = on ? 'Let the app list close again' : 'Keep the app list open';
    $('.vh', pin).textContent = pin.title;
    try { localStorage.setItem(PIN_KEY, on ? '1' : '0'); } catch { /* private mode */ }
  }

  let pinned = false;
  try { pinned = localStorage.getItem(PIN_KEY) === '1'; } catch { /* private mode */ }
  if (pinned) setPinned(true);
  pin.addEventListener('click', () => setPinned(!shell.hasAttribute('data-pinned')));

  refresh();
})();
