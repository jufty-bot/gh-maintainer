const $ = (selector) => document.querySelector(selector);
const results = $('#results');
let view = 'queue', offset = 0, total = 0, overview, generation = 0;
const limit = 50;
function el(tag, text, className) {
  const node = document.createElement(tag);
  if (text !== undefined) node.textContent = text;
  if (className) node.className = className;
  return node;
}
function safeLink(url, text) {
  const link = el('a', text);
  try { const parsed = new URL(url); if (parsed.protocol === 'https:' && parsed.hostname === 'github.com') link.href = parsed.href; } catch {}
  link.target = '_blank'; link.rel = 'noopener noreferrer';
  return link;
}
function pullIcon() {
  const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
  svg.setAttribute('viewBox', '0 0 24 24');
  svg.setAttribute('fill', 'none');
  svg.setAttribute('stroke', 'currentColor');
  svg.setAttribute('stroke-width', '1.6');
  svg.setAttribute('aria-hidden', 'true');
  for (const [tag, attrs] of [['circle', {cx: 6, cy: 5, r: 2.5}], ['circle', {cx: 6, cy: 19, r: 2.5}], ['circle', {cx: 18, cy: 19, r: 2.5}], ['path', {d: 'M6 7.5v9M18 16.5V9a4 4 0 0 0-4-4h-2m2-2-2 2 2 2'}]]) {
    const child = document.createElementNS('http://www.w3.org/2000/svg', tag);
    for (const [name, value] of Object.entries(attrs)) child.setAttribute(name, value);
    svg.append(child);
  }
  return svg;
}
async function api(path) {
  const response = await fetch(path);
  if (!response.ok) throw new Error(`Local API returned ${response.status}`);
  return response.json();
}
async function loadOverview() {
  overview = await api('/api/overview');
  $('#owner').textContent = overview.owner ? `@${overview.owner} / portfolio` : 'Your workspace';
  for (const [key, value] of Object.entries(overview.counts)) {
    const target = $(`#metric-${key}`); if (target) target.textContent = value;
  }
  const run = overview.latest_run;
  const incomplete = overview.counts.incomplete_repositories;
  $('#sync-status').classList.toggle('warn', !!incomplete || (run && run.status !== 'complete'));
  $('#sync-status').textContent = run
    ? `${run.status === 'complete' ? '●' : '◐'} Last sync: ${run.status} · ${new Date(run.finished_at || run.started_at).toLocaleString()} · ${incomplete ? `${incomplete} repositories incomplete` : 'View repository timestamps for coverage'}`
    : 'No sync yet. Run gh-maintainer sync, then refresh this page. Or use demo data to explore.';
  const selected = $('#repo').value;
  $('#repo').replaceChildren(new Option('All repositories', ''), ...overview.repos.map(repo => new Option(repo.name, repo.name)));
  $('#repo').value = selected;
  $('#generated').textContent = `Index read at ${new Date(overview.generated_at).toLocaleTimeString()}`;
}
function renderItem(item) {
  const row = el('article', undefined, 'item-row');
  const icon = el('span', item.kind === 'pr' ? '' : '◎', `item-icon ${item.kind}`);
  if (item.kind === 'pr') icon.append(pullIcon());
  row.append(icon);
  const center = el('div');
  const title = el('button', item.title, 'item-title');
  title.addEventListener('click', () => showDetail(item.id)); center.append(title);
  const meta = el('div', undefined, 'item-meta');
  meta.append(safeLink(item.url, `${item.repo} #${item.number}`), el('span', item.draft ? 'draft' : item.state), el('span', item.disposition));
  for (const label of item.labels.slice(0, 3)) meta.append(el('span', label));
  center.append(meta); row.append(center);
  const side = el('div', undefined, 'item-side');
  if (item.reasons.length) side.append(el('span', item.reasons[0], `reason ${item.score >= 60 ? 'warning' : ''}`));
  side.append(el('span', `${item.age_days}d since item update`, 'timestamp'));
  row.append(side); return row;
}
function renderRepos() {
  const search = $('#search').value.toLowerCase();
  const repos = overview.repos.filter(repo => repo.name.toLowerCase().includes(search) && (!$('#repo').value || repo.name === $('#repo').value));
  results.replaceChildren(...repos.map(repo => {
    const card = el('article', undefined, 'repo-card');
    const title = el('h3'); title.append(safeLink(repo.url, repo.name));
    card.append(title, el('p', repo.raw.description || 'No description yet.'));
    const meta = el('div', undefined, 'item-meta');
    meta.append(el('span', `${repo.open_prs} open PRs`), el('span', `${repo.open_issues} open issues`), el('span', repo.archived ? 'Archived' : (repo.raw.language || 'No language')),
      el('span', repo.raw.private ? 'Private' : 'Public'));
    card.append(meta);
    const status = repo.error || (!repo.present ? 'No longer visible in owner inventory' : (repo.synced_at ? `Items synced ${new Date(repo.synced_at).toLocaleString()}` : 'Not indexed yet'));
    card.append(el('p', status, repo.error ? 'detail-signal' : ''));
    return card;
  }));
  $('#total').textContent = repos.length; $('.pagination').hidden = true;
}
async function loadItems() {
  const currentGeneration = ++generation;
  if (view === 'repos') { renderRepos(); return; }
  $('.pagination').hidden = false;
  const query = new URLSearchParams({ q: $('#search').value, repo: $('#repo').value, state: $('#state').value, limit, offset });
  if (view === 'queue') { query.set('queue', 'true'); query.set('state', 'open'); }
  if (view === 'pr' || view === 'issue') query.set('kind', view);
  try {
    const data = await api(`/api/items?${query}`);
    if (currentGeneration !== generation) return;
    total = data.total; $('#total').textContent = total;
    results.replaceChildren(...data.items.map(renderItem));
    if (!data.items.length) results.append(el('div', 'Nothing here yet. Try another filter or sync your repositories.', 'empty'));
    $('#previous').disabled = offset === 0; $('#next').disabled = offset + limit >= total;
    $('#page').textContent = total ? `${offset + 1}–${Math.min(offset + limit, total)} of ${total}` : '0 items';
  } catch (error) { results.replaceChildren(el('div', error.message, 'empty')); }
}
async function showDetail(id) {
  const dialog = $('#detail'), content = $('#detail-content');
  content.replaceChildren(el('p', 'Loading item…')); dialog.showModal();
  try {
    const { item, changes } = await api(`/api/items/${encodeURIComponent(id)}`);
    content.replaceChildren(el('div', `${item.repo} #${item.number}`, 'eyebrow'), el('h2', item.title));
    const source = el('p'); source.append(safeLink(item.url, 'Open on GitHub ↗')); content.append(source);
    content.append(el('p', `${item.kind} · ${item.state} · ${item.disposition}`));
    if (item.kind === 'pr') content.append(el('p', `Checks: ${item.checks} · Review: ${item.review} · Signals observed: ${item.signals_at || 'never'}`, 'detail-signal'));
    if (item.repo_error) content.append(el('p', `Incomplete synchronization: ${item.repo_error}`, 'detail-signal'));
    if (!item.available || !item.present) content.append(el('p', 'Retained history: this item or its repository is no longer visible in the latest inventory.'));
    content.append(el('div', 'Description', 'detail-label'), el('pre', item.raw.body || 'No description provided.'));
    content.append(el('div', 'Local triage', 'detail-label'), el('p', 'Set a disposition using the CLI. This does not change the item on GitHub.'), el('pre', `gh-maintainer triage ${item.id} planned --note "Your next action"`));
    if (item.note) content.append(el('p', `Note: ${item.note}`));
    content.append(el('div', `${changes.length} recent local observations`, 'detail-label'));
    for (const change of changes.slice(0, 8)) content.append(el('p', `${new Date(change.observed_at).toLocaleString()} · ${change.before_json ? 'Source data changed' : 'First indexed'}`));
  } catch (error) { content.replaceChildren(el('p', error.message)); }
}
const names = { queue: ['Maintenance queue', 'Your next moves'], repos: ['Repositories', 'The whole portfolio'], pr: ['Pull requests', 'Changes in motion'], issue: ['Issues', 'Work to understand'], all: ['All indexed items', 'The complete local index'] };
for (const button of document.querySelectorAll('nav button')) button.addEventListener('click', () => {
  view = button.dataset.view; offset = 0;
  document.querySelectorAll('nav button').forEach(node => node.classList.toggle('active', node === button));
  $('#title').textContent = names[view][0]; $('#list-title').textContent = names[view][1];
  $('#state').hidden = view === 'queue' || view === 'repos'; loadItems();
});
let searchTimer;
$('#search').addEventListener('input', () => { clearTimeout(searchTimer); searchTimer = setTimeout(() => { offset = 0; loadItems(); }, 180); });
for (const selector of ['#repo', '#state']) $(selector).addEventListener('change', () => { offset = 0; loadItems(); });
$('#previous').addEventListener('click', () => { offset = Math.max(0, offset - limit); loadItems(); });
$('#next').addEventListener('click', () => { offset += limit; loadItems(); });
$('#close-detail').addEventListener('click', () => $('#detail').close());
async function refresh() { try { await loadOverview(); await loadItems(); } catch (error) { $('#sync-status').textContent = error.message; } }
$('#refresh').addEventListener('click', refresh);
$('#state').hidden = true;
refresh();
