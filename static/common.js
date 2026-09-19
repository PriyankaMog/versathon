const $ = s => document.querySelector(s);
const $$ = s => [...document.querySelectorAll(s)];
const esc = t => String(t ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;', '<':'&lt;', '>':'&gt;', '"':'&quot;', "'":'&#39;'}[c]));
const time = t => new Date(t + 'Z').toLocaleTimeString([], {hour:'2-digit', minute:'2-digit'});
const store = {
  get: k => { try { return localStorage.getItem(k); } catch { return null; } },
  set: (k, v) => { try { localStorage.setItem(k, v); } catch {} },
  del: k => { try { localStorage.removeItem(k); } catch {} }
};
async function api(path, body) {
  const h = {'Content-Type':'application/json'}, t = store.get('or_token');
  if (t) h.Authorization = 'Bearer ' + t;
  const r = await fetch('/api' + path, {method: body === undefined ? 'GET' : 'POST', headers:h, body: body === undefined ? undefined : JSON.stringify(body)});
  const d = await r.json().catch(() => ({}));
  if (!r.ok) throw Object.assign(new Error(typeof d.detail === 'string' ? d.detail : 'Please check the details you entered.'), {status:r.status});
  return d;
}
const get = p => api(p);
const post = (p, b = {}) => api(p, b);
const LOGO = '<svg width="28" height="28" viewBox="0 0 28 28" fill="none" stroke="currentColor" stroke-width="2.2" aria-hidden="true"><circle cx="14" cy="14" r="12"/><circle cx="14" cy="14" r="6.5"/><circle cx="14" cy="14" r="2" fill="currentColor"/></svg>';
