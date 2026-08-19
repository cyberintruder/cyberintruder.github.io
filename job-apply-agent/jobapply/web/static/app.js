/* job-apply-agent dashboard — no framework, no build step. */
'use strict';

const API = {
  async get(url) {
    const r = await fetch(url);
    if (!r.ok) throw new Error((await r.json().catch(() => ({}))).error || r.statusText);
    return r.json();
  },
  async send(url, method, body) {
    const r = await fetch(url, {
      method,
      headers: { 'Content-Type': 'application/json' },
      body: body === undefined ? undefined : JSON.stringify(body),
    });
    if (!r.ok) throw new Error((await r.json().catch(() => ({}))).error || r.statusText);
    return r.json();
  },
  post(url, body) { return this.send(url, 'POST', body); },
  patch(url, body) { return this.send(url, 'PATCH', body); },
};

/* ---------------------------------------------------------------- helpers */
const el = (tag, attrs = {}, ...kids) => {
  const n = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (v === null || v === undefined || v === false) continue;
    if (k === 'class') n.className = v;
    else if (k === 'html') n.innerHTML = v;
    else if (k.startsWith('on')) n.addEventListener(k.slice(2), v);
    else n.setAttribute(k, v);
  }
  for (const kid of kids.flat()) {
    if (kid === null || kid === undefined || kid === false) continue;
    n.append(kid instanceof Node ? kid : document.createTextNode(String(kid)));
  }
  return n;
};
const $ = (sel, root = document) => root.querySelector(sel);
const clear = (node) => { while (node.firstChild) node.removeChild(node.firstChild); return node; };

const fmtTime = (iso) => iso ? new Date(iso).toLocaleString(undefined,
  { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' }) : '—';
const fmtRel = (iso) => {
  if (!iso) return '—';
  const diff = (new Date(iso) - Date.now()) / 60000;
  const abs = Math.abs(diff);
  const unit = abs < 60 ? [Math.round(diff), 'min'] :
               abs < 1440 ? [Math.round(diff / 60), 'h'] : [Math.round(diff / 1440), 'd'];
  return diff >= 0 ? `in ${unit[0]}${unit[1]}` : `${-unit[0]}${unit[1]} ago`;
};
const titleCase = (s) => (s || '').replace(/_/g, ' ').replace(/\b\w/g, (c) => c.toUpperCase());
const scoreClass = (n) => n >= 78 ? 'score-hi' : n >= 62 ? 'score-mid' : 'score-lo';
const pill = (status) => el('span', { class: `pill pill-${status}` }, titleCase(status));

function toast(msg, isError = false) {
  const node = $('#toast');
  node.textContent = msg;
  node.className = 'toast' + (isError ? ' err' : '');
  clearTimeout(node._t);
  node._t = setTimeout(() => node.classList.add('hidden'), 5200);
}

function emptyState(text) { return el('div', { class: 'empty' }, text); }

/* ------------------------------------------------------------- mini charts */
function lineChart(container, series, keys, colors) {
  clear(container);
  if (!series.length) { container.append(emptyState('No applications yet.')); return; }
  const W = 640, H = 190, P = { t: 12, r: 10, b: 24, l: 30 };
  const max = Math.max(1, ...series.flatMap((d) => keys.map((k) => d[k] || 0)));
  const x = (i) => P.l + (i * (W - P.l - P.r)) / Math.max(1, series.length - 1);
  const y = (v) => H - P.b - (v / max) * (H - P.t - P.b);

  const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
  svg.setAttribute('viewBox', `0 0 ${W} ${H}`);
  svg.setAttribute('preserveAspectRatio', 'none');
  const ns = (tag, attrs) => {
    const n = document.createElementNS('http://www.w3.org/2000/svg', tag);
    for (const [k, v] of Object.entries(attrs)) n.setAttribute(k, v);
    return n;
  };
  for (let i = 0; i <= 3; i++) {
    const v = (max / 3) * i;
    svg.append(ns('line', { x1: P.l, x2: W - P.r, y1: y(v), y2: y(v),
      stroke: 'currentColor', 'stroke-opacity': 0.12 }));
    const t = ns('text', { x: 4, y: y(v) + 4, fill: 'currentColor',
      'fill-opacity': 0.45, 'font-size': 10 });
    t.textContent = Math.round(v);
    svg.append(t);
  }
  keys.forEach((key, ki) => {
    const pts = series.map((d, i) => `${x(i)},${y(d[key] || 0)}`).join(' ');
    svg.append(ns('polyline', { points: pts, fill: 'none', stroke: colors[ki],
      'stroke-width': 2, 'stroke-linejoin': 'round', 'stroke-linecap': 'round' }));
    series.forEach((d, i) => {
      if ((d[key] || 0) > 0) svg.append(ns('circle', { cx: x(i), cy: y(d[key]), r: 2.5, fill: colors[ki] }));
    });
  });
  container.append(svg);
  container.append(el('div', { class: 'legend' },
    keys.map((k, i) => el('span', {},
      el('i', { style: `background:${colors[i]}` }), titleCase(k)))));
}

function barList(container, rows, labelKey, countKey, color) {
  clear(container);
  if (!rows.length) { container.append(emptyState('Nothing yet.')); return; }
  const max = Math.max(...rows.map((r) => r[countKey])) || 1;
  for (const r of rows) {
    container.append(el('div', { class: 'bar-row' },
      el('div', { class: 'bar-label' }, r[labelKey]),
      el('div', { class: 'bar-count' }, r[countKey]),
      el('div', { class: 'bar-track' },
        el('div', { class: 'bar-fill',
          style: `width:${(r[countKey] / max) * 100}%${color ? `;background:${color}` : ''}` })),
    ));
  }
}

/* ------------------------------------------------------------ global chrome */
const Chrome = {
  init() {
    const tick = () => {
      const c = $('#clock');
      if (c) c.textContent = new Date().toLocaleTimeString();
    };
    tick(); setInterval(tick, 1000);

    document.addEventListener('click', async (ev) => {
      const btn = ev.target.closest('[data-action]');
      if (!btn) return;
      const action = btn.dataset.action;
      if (action === 'refresh') { location.reload(); return; }
      const map = {
        ingest: ['/api/actions/ingest-screenshots', 'Reading screenshots…'],
        discover: ['/api/actions/discover', 'Searching LinkedIn…'],
        'run-due': ['/api/actions/run-due', 'Sending what is due…'],
      };
      if (!map[action]) return;
      const [url, msg] = map[action];
      btn.disabled = true;
      toast(msg);
      try {
        const res = await API.post(url);
        toast(Chrome.describe(action, res));
        setTimeout(() => location.reload(), 1400);
      } catch (e) { toast(e.message, true); }
      finally { btn.disabled = false; }
    });

    this.badges();
  },

  describe(action, res) {
    if (action === 'discover') {
      return `Scanned ${res.scanned}, ${res.matched} matched, ${res.scheduled} scheduled` +
             (res.tailored ? `, ${res.tailored} tailored` : '');
    }
    const n = (res.results || []).length;
    if (action === 'ingest') return n ? `Processed ${n} screenshot(s)` : 'No new screenshots';
    return n ? `Processed ${n} application(s)` : 'Nothing due right now';
  },

  async badges() {
    try {
      const s = await API.get('/api/stats');
      const setBadge = (sel, n) => {
        const node = $(sel);
        if (!node) return;
        node.textContent = n;
        node.classList.toggle('hidden', !n);
      };
      setBadge('#nav-review-badge', s.totals.needs_review);
      setBadge('#nav-queue-badge', s.totals.scheduled);
      const flags = $('#mode-flags');
      if (flags) {
        clear(flags);
        if (s.config.dry_run) flags.append(el('span', { class: 'tag', title:
          'Nothing is actually submitted or sent while dry run is on' }, 'dry run'));
        if (s.config.require_review) flags.append(el('span', { class: 'tag', title:
          'Unanswerable required questions pause the application' }, 'review gate'));
        flags.append(el('span', { class: 'tag' }, `email: ${s.config.email_mode}`));
      }
    } catch { /* badges are cosmetic */ }
  },
};
document.addEventListener('DOMContentLoaded', () => Chrome.init());

/* ------------------------------------------------------------- dashboard */
const Dashboard = {
  async init() {
    try {
      const [stats, events] = await Promise.all([API.get('/api/stats'), API.get('/api/events')]);
      this.kpis(stats.totals, stats.next_scheduled, stats.config);
      lineChart($('#chart-timeseries'), stats.timeseries, ['queued', 'submitted'],
        ['#60a5fa', '#4ade80']);
      barList($('#chart-status'), stats.by_status.filter((r) => r.count), 'status', 'count');
      const statusBars = $('#chart-status');
      statusBars.className = 'bars';
      statusBars.querySelectorAll('.bar-label').forEach((n) => { n.textContent = titleCase(n.textContent); });
      barList($('#chart-resumes'), stats.by_resume, 'resume', 'count', '#a78bfa');
      barList($('#chart-companies'), stats.top_companies, 'company', 'count', '#60a5fa');
      barList($('#chart-channels'), stats.by_channel.map(
        (r) => ({ channel: titleCase(r.channel), count: r.count })), 'channel', 'count', '#fbbf24');
      this.activity(events.items);
    } catch (e) { toast(e.message, true); }
  },

  kpis(t, nextScheduled, config = {}) {
    const cards = [
      ['Submitted', t.submitted, `${t.submit_rate}% of ${t.considered} considered`, 'accent'],
      ['Needs review', t.needs_review, t.needs_review ? 'waiting on you' : 'all clear', 'amber'],
      ['Scheduled', t.scheduled, nextScheduled ? `next ${fmtRel(nextScheduled)}` : 'queue empty', 'blue'],
      ['Sent today', t.sent_today, `daily cap ${t.sent_today}/${config.daily_cap ?? '—'}`, ''],
      ['Avg match', t.avg_score, 'across considered jobs', ''],
      ['Failed', t.failed, t.failed ? 'retry from the table' : 'none', t.failed ? 'red' : ''],
      ['Screenshots', t.screenshots_pending, 'pending in the inbox', ''],
    ];
    const box = clear($('#kpis'));
    for (const [label, value, sub, cls] of cards) {
      box.append(el('div', { class: `kpi ${cls}` },
        el('div', { class: 'label' }, label),
        el('div', { class: 'value' }, value),
        el('div', { class: 'sub' }, sub)));
    }
  },

  activity(items) {
    const box = clear($('#activity'));
    if (!items.length) { box.append(emptyState('No activity yet. Run discovery to start.')); return; }
    const colors = { submitted: 'var(--accent)', scheduled: 'var(--blue)',
      needs_review: 'var(--amber)', error: 'var(--red)', failed: 'var(--red)',
      matched: 'var(--violet)' };
    for (const it of items) {
      const label = it.company ? `${it.title} · ${it.company}` : '';
      box.append(el('div', { class: 'act-row' },
        el('span', { class: 'act-time' }, fmtTime(it.ts)),
        el('span', { class: 'act-kind', style: `color:${colors[it.kind] || 'var(--text-3)'}` },
          it.kind),
        el('span', { class: 'act-msg' },
          it.application_id
            ? el('a', { href: `/applications/${it.application_id}` }, label || it.message)
            : (label || ''),
          label ? ' — ' : '', it.message)));
    }
  },
};

/* ---------------------------------------------------------- applications */
const Applications = {
  init() {
    ['#filter-q', '#filter-status', '#filter-channel'].forEach((sel) => {
      const node = $(sel);
      node.addEventListener('input', () => this.debounced());
      node.addEventListener('change', () => this.load());
    });
    this.load();
  },
  debounced() {
    clearTimeout(this._t);
    this._t = setTimeout(() => this.load(), 260);
  },
  async load() {
    const params = new URLSearchParams({
      q: $('#filter-q').value.trim(),
      status: $('#filter-status').value,
      channel: $('#filter-channel').value,
    });
    try {
      const data = await API.get('/api/applications?' + params);
      $('#result-count').textContent = `${data.items.length} of ${data.total}`;
      this.rows(data.items);
    } catch (e) { toast(e.message, true); }
  },
  rows(items) {
    const body = clear($('#apps-body'));
    if (!items.length) {
      body.append(el('tr', {}, el('td', { colspan: 8 },
        emptyState('Nothing matches. Run discovery or drop a screenshot in the inbox.'))));
      return;
    }
    for (const a of items) {
      body.append(el('tr', {},
        el('td', {},
          el('div', { class: 'cell-title' }, a.title || '—'),
          el('div', { class: 'cell-sub' }, a.location || '')),
        el('td', {}, a.company || '—'),
        el('td', {}, el('span', { class: `score ${scoreClass(a.match_score)}` }, a.match_score)),
        el('td', {}, a.resume_key ? el('span', { class: 'tag' }, a.resume_key) : '—'),
        el('td', {}, el('span', { class: 'cell-sub' },
          a.channel === 'ats' && a.ats_vendor ? a.ats_vendor : titleCase(a.channel || '—'))),
        el('td', {}, pill(a.status),
          a.open_question_count ? el('span', { class: 'cell-sub' },
            ` ${a.open_question_count} open`) : null),
        el('td', { class: 'cell-sub' },
          a.submitted_at ? fmtTime(a.submitted_at) : fmtTime(a.scheduled_for)),
        el('td', {}, el('a', { class: 'btn btn-sm', href: `/applications/${a.id}` }, 'Open'))));
    }
  },
};

/* ---------------------------------------------------------------- detail */
const Detail = {
  async init() {
    this.id = $('#detail').dataset.appId;
    await this.load();
  },
  async load() {
    try {
      this.data = await API.get(`/api/applications/${this.id}`);
      this.render();
    } catch (e) { toast(e.message, true); }
  },
  render() {
    const d = this.data;
    const root = clear($('#detail'));

    root.append(el('div', { class: 'detail-head' },
      el('div', {},
        el('h2', {}, d.title || 'Untitled role'),
        el('div', { class: 'muted' }, [d.company, d.location].filter(Boolean).join(' · ')),
        el('div', { class: 'detail-meta' },
          pill(d.status),
          el('span', { class: `score ${scoreClass(d.match_score)}` }, `match ${d.match_score}`),
          d.resume_key ? el('span', { class: 'tag' }, d.resume_key) : null,
          d.ats_vendor ? el('span', { class: 'tag' }, d.ats_vendor) : null,
          d.source ? el('span', { class: 'tag' }, titleCase(d.source)) : null)),
      el('div', { class: 'btn-row' },
        d.url ? el('a', { class: 'btn btn-sm', href: d.url, target: '_blank',
          rel: 'noopener' }, 'Job post ↗') : null,
        d.resume_path ? el('a', { class: 'btn btn-sm',
          href: `/api/resume-file?path=${encodeURIComponent(d.resume_path)}` }, 'Resume ↓') : null,
        el('button', { class: 'btn btn-sm btn-primary',
          onclick: () => this.act('run', 'Applying now…') }, 'Run now'),
        el('button', { class: 'btn btn-sm',
          onclick: () => this.act('retry', 'Requeued') }, 'Retry'),
        el('button', { class: 'btn btn-sm btn-danger',
          onclick: () => this.act('skip', 'Skipped') }, 'Skip'))));

    const left = el('div', { class: 'stack' });
    const right = el('div', { class: 'stack' });

    if (d.open_questions.length) {
      right.append(this.card('Open questions', el('div', { class: 'q-list' },
        d.open_questions.map((q) => el('div', { class: 'q-item' },
          el('label', {}, q.label, el('span', { class: 'req' }, 'required')),
          el('input', { type: 'text', 'data-qid': q.field_id || q.label,
            placeholder: 'Your answer' }),
          el('div', { class: 'q-why' }, q.rationale || ''))),
        el('div', { class: 'review-actions' },
          el('button', { class: 'btn btn-primary btn-sm',
            onclick: () => this.saveAnswers(true) }, 'Save & approve'),
          el('button', { class: 'btn btn-sm',
            onclick: () => this.saveAnswers(false) }, 'Save only')))));
    }

    right.append(this.card('Why this resume',
      el('ul', { class: 'reasons' },
        (d.match_reasons.length ? d.match_reasons : ['no reasons recorded'])
          .map((r) => el('li', {}, r)),
        d.missing_skills.length
          ? el('li', { style: 'color:var(--amber)' },
              'Gaps: ' + d.missing_skills.join(', '))
          : null)));

    right.append(this.card('Timing', el('dl', { class: 'kv' },
      el('dt', {}, 'Scheduled'), el('dd', {}, fmtTime(d.scheduled_for),
        d.scheduled_for ? el('span', { class: 'muted' }, ` (${fmtRel(d.scheduled_for)})`) : null),
      el('dt', {}, 'Posted'), el('dd', {}, fmtTime(d.posted_at)),
      el('dt', {}, 'Submitted'), el('dd', {}, fmtTime(d.submitted_at)),
      el('dt', {}, 'Attempts'), el('dd', {}, d.attempts),
      el('dt', {}, 'Reason'), el('dd', { class: 'muted' }, d.schedule_reason || '—'))));

    if (d.email) right.append(this.emailCard(d.email));

    if (d.answers.length) {
      left.append(this.card(`Answers given (${d.answers.length})`,
        el('div', { class: 'answers' }, d.answers.map((a) => el('div', { class: 'answer-row' },
          el('div', { class: 'answer-q' }, a.label || a.field_id),
          el('div', { class: 'answer-a' }, a.value),
          el('div', { class: 'answer-src' },
            `${a.source}${a.confidence !== undefined ? ` · confidence ${a.confidence}` : ''}` +
            (a.rationale ? ` · ${a.rationale}` : '')))))));
    }

    if (d.screenshots.length) {
      left.append(this.card('Evidence', el('div', { class: 'shots' },
        d.screenshots.map((p) => {
          const url = `/api/artifact?path=${encodeURIComponent(p)}`;
          return el('a', { href: url, target: '_blank', rel: 'noopener' },
            el('img', { src: url, loading: 'lazy', alt: p }));
        }))));
    }

    left.append(this.card('Timeline', el('div', { class: 'timeline' },
      d.events.slice().reverse().map((e) => el('div', { class: 'tl-item' },
        el('div', { class: `tl-dot ${['submitted', 'approved'].includes(e.kind) ? 'ok'
          : ['needs_review', 'dry_run', 'skipped'].includes(e.kind) ? 'warn'
          : ['error', 'failed'].includes(e.kind) ? 'err' : ''}` }),
        el('div', {},
          el('div', { class: 'tl-msg' }, el('strong', {}, e.kind), ' — ', e.message),
          el('div', { class: 'tl-time' }, fmtTime(e.ts))))))));

    left.append(this.card('Job description',
      el('div', { class: 'jd' }, d.description || 'No description captured.')));

    root.append(el('div', { class: 'detail-cols' }, left, right));
  },

  card(title, ...body) {
    return el('section', { class: 'card' },
      el('div', { class: 'card-head' }, el('h2', {}, title)), ...body);
  },

  emailCard(email) {
    return this.card('Email draft', el('div', { class: 'email-preview' },
      el('label', {}, 'To'),
      el('input', { type: 'email', id: 'em-to', value: email.to || '' }),
      el('label', {}, 'Subject'),
      el('input', { type: 'text', id: 'em-subject', value: email.subject || '' }),
      el('label', {}, 'Body'),
      el('textarea', { id: 'em-body', rows: 12 }, email.body || ''),
      el('div', { class: 'muted' },
        email.attachment_path ? `Attachment: ${email.attachment_path.split('/').pop()}` : 'No attachment',
        email.gmail_draft_id ? ` · Gmail draft ${email.gmail_draft_id}` : ''),
      el('div', { class: 'btn-row' },
        el('button', { class: 'btn btn-sm', onclick: () => this.saveEmail() }, 'Save draft'),
        email.gmail_draft_id
          ? el('a', { class: 'btn btn-sm', target: '_blank', rel: 'noopener',
              href: `https://mail.google.com/mail/u/0/#drafts` }, 'Open in Gmail ↗')
          : null)));
  },

  async saveEmail() {
    try {
      await API.patch(`/api/applications/${this.id}/email`, {
        to_addr: $('#em-to').value, subject: $('#em-subject').value, body: $('#em-body').value,
      });
      toast('Draft saved');
    } catch (e) { toast(e.message, true); }
  },

  async saveAnswers(approve) {
    const answers = {};
    document.querySelectorAll('[data-qid]').forEach((inp) => {
      if (inp.value.trim()) answers[inp.dataset.qid] = inp.value.trim();
    });
    try {
      const res = await API.post(`/api/applications/${this.id}/answers`, { answers, approve });
      toast(approve && !res.open_questions.length
        ? 'Approved — it will go out on the next worker pass'
        : 'Answers saved');
      await this.load();
    } catch (e) { toast(e.message, true); }
  },

  async act(action, msg) {
    try {
      toast(msg);
      await API.post(`/api/applications/${this.id}/${action}`);
      await this.load();
      toast('Done');
    } catch (e) { toast(e.message, true); }
  },
};

/* ---------------------------------------------------------------- review */
const Review = {
  async init() {
    try {
      const data = await API.get('/api/review');
      const box = clear($('#review-list'));
      if (!data.items.length) {
        box.append(emptyState('Nothing waiting. The agent has not been blocked on anything.'));
        return;
      }
      for (const item of data.items) box.append(this.item(item));
    } catch (e) { toast(e.message, true); }
  },

  item(a) {
    const qs = a.open_questions || [];
    const node = el('div', { class: 'review-item' },
      el('div', { class: 'review-head' },
        el('div', {},
          el('div', { class: 'cell-title' },
            el('a', { href: `/applications/${a.id}` }, a.title || 'Untitled')),
          el('div', { class: 'muted' }, [a.company, a.location].filter(Boolean).join(' · ')),
          el('div', { class: 'detail-meta' },
            el('span', { class: `score ${scoreClass(a.match_score)}` }, `match ${a.match_score}`),
            a.resume_key ? el('span', { class: 'tag' }, a.resume_key) : null,
            a.ats_vendor ? el('span', { class: 'tag' }, a.ats_vendor) : null)),
        el('div', { class: 'muted' }, a.error || `${qs.length} open question(s)`)),
      qs.length
        ? el('div', { class: 'q-list' }, qs.map((q) => el('div', { class: 'q-item' },
            el('label', {}, q.label, el('span', { class: 'req' }, 'required')),
            el('input', { type: 'text', 'data-qid': q.field_id || q.label,
              placeholder: 'Your answer' }),
            el('div', { class: 'q-why' }, q.rationale || ''))))
        : el('div', { class: 'q-list' },
            el('div', { class: 'muted' },
              a.email && !a.email.to
                ? 'This post named no email address — add a recipient on the detail page.'
                : 'Nothing to answer: review the detail page and approve when ready.')),
      el('div', { class: 'review-actions' },
        el('button', { class: 'btn btn-primary btn-sm',
          onclick: (e) => this.save(e.target.closest('.review-item'), a.id, true) }, 'Save & approve'),
        el('button', { class: 'btn btn-sm',
          onclick: (e) => this.save(e.target.closest('.review-item'), a.id, false) }, 'Save only'),
        el('a', { class: 'btn btn-sm', href: `/applications/${a.id}` }, 'Open detail'),
        el('button', { class: 'btn btn-sm btn-danger',
          onclick: () => this.skip(a.id) }, 'Skip')));
    return node;
  },

  async save(node, id, approve) {
    const answers = {};
    node.querySelectorAll('[data-qid]').forEach((inp) => {
      if (inp.value.trim()) answers[inp.dataset.qid] = inp.value.trim();
    });
    try {
      const res = await API.post(`/api/applications/${id}/answers`, { answers, approve });
      toast(approve && !res.open_questions.length ? 'Approved and queued' : 'Answers saved');
      this.init();
    } catch (e) { toast(e.message, true); }
  },

  async skip(id) {
    try { await API.post(`/api/applications/${id}/skip`); toast('Skipped'); this.init(); }
    catch (e) { toast(e.message, true); }
  },
};

/* ----------------------------------------------------------------- queue */
const Queue = {
  async init() {
    try {
      const data = await API.get('/api/queue');
      const body = clear($('#queue-body'));
      if (!data.items.length) {
        body.append(el('tr', {}, el('td', { colspan: 6 },
          emptyState('Send queue is empty.'))));
        return;
      }
      for (const a of data.items) {
        body.append(el('tr', {},
          el('td', {},
            el('div', { class: 'cell-title', style: a.overdue ? 'color:var(--amber)' : '' },
              fmtRel(a.scheduled_for)),
            el('div', { class: 'cell-sub' }, fmtTime(a.scheduled_for))),
          el('td', {}, el('a', { href: `/applications/${a.id}` }, a.title || '—')),
          el('td', {}, a.company || '—'),
          el('td', {}, el('span', { class: `score ${scoreClass(a.match_score)}` }, a.match_score)),
          el('td', { class: 'cell-sub' }, a.schedule_reason || '—'),
          el('td', {}, el('button', { class: 'btn btn-sm',
            onclick: () => this.now(a.id) }, 'Send now'))));
      }
    } catch (e) { toast(e.message, true); }
  },
  async now(id) {
    try {
      toast('Applying now…');
      const res = await API.post(`/api/applications/${id}/run`);
      toast((res.results[0] || {}).message || 'Done');
      this.init();
    } catch (e) { toast(e.message, true); }
  },
};

/* --------------------------------------------------------------- resumes */
const Resumes = {
  async init() {
    try {
      const data = await API.get('/api/resumes');
      const grid = clear($('#resume-grid'));
      for (const r of data.items) {
        grid.append(el('div', { class: 'resume-card' },
          el('h3', {}, r.role, r.is_default ? el('span', { class: 'tag',
            style: 'margin-left:8px' }, 'default') : null),
          el('div', { class: 'key' }, r.key),
          el('p', {}, r.summary || 'No summary set.'),
          el('div', { class: 'kw' }, r.keywords.slice(0, 14).map(
            (k) => el('span', {}, k))),
          el('div', { class: 'resume-foot' },
            el('span', { class: 'muted' },
              `${r.used} application${r.used === 1 ? '' : 's'}` +
              (r.exists ? '' : ' · FILE MISSING')),
            r.exists
              ? el('a', { class: 'btn btn-sm',
                  href: `/api/resume-file?path=${encodeURIComponent(r.path)}` }, 'Download')
              : el('span', { class: 'tag', style: 'color:var(--red)' }, r.file))));
      }
      const list = clear($('#tailored-list'));
      if (!data.tailored.length) {
        list.append(el('div', { class: 'muted' },
          'None yet — variants are generated when a job scores above the tailoring threshold.'));
      } else {
        for (const t of data.tailored) {
          list.append(el('div', {},
            el('span', { class: 'tag' }, t.parent || '?'), ' → ',
            el('a', { href: `/api/resume-file?path=${encodeURIComponent(t.path)}` },
              t.path.split('/').pop()),
            el('span', { class: 'muted' }, ` · ${fmtTime(t.created_at)}`)));
        }
      }
    } catch (e) { toast(e.message, true); }
  },
};
