import {
  BabyState, CaregiverAction, SessionState, decide, deriveMetrics, msSince,
  normalizeEvents, planVersionId, validateTransition
} from './engine.js';
import {
  appendEvent, appendEvents, archiveNight, deleteEvent, getPersistenceStatus,
  loadChecklist, loadEvents, loadHistory, loadNightId, loadNightNo, loadPlan,
  prepareNextNight, resetAll, saveChecklist, savePlan, updateNightReview
} from './store.js';
import {
  acceptCloudConflict, createFamilyInvite, createHousehold, getCloudState,
  isCloudConfigured, signInPassword, signOutCloud, signUpAccess, startCloudSync
} from './cloud.js';

const app = document.querySelector('#app');
let view = 'tonight';
let modal = null;
let toast = null;
let now = new Date();
let lastCheckInAlertKey = null;
let cloud = getCloudState();

const checklistItems = [
  'Full daytime feeds', 'Appropriate daytime naps', 'Last nap recorded',
  'Final wake window reviewed', 'Final feed completed', 'Calm bedtime routine',
  'Non-weighted sleep clothing ready', 'Sound machine', 'Dark room',
  'Sleep space prepared', 'Feeding plan selected', 'Morning time selected'
];


function fmtElapsed(ms = 0) {
  const total = Math.max(0, Math.floor(ms / 1000));
  const h = Math.floor(total / 3600), m = Math.floor((total % 3600) / 60), s = total % 60;
  return h > 0
    ? `${String(h).padStart(2,'0')}:${String(m).padStart(2,'0')}:${String(s).padStart(2,'0')}`
    : `${String(m).padStart(2,'0')}:${String(s).padStart(2,'0')}`;
}
function fmtTime(ts) { return ts ? new Date(ts).toLocaleTimeString([], { hour:'numeric', minute:'2-digit' }) : '—'; }
function fmtDate(ts = new Date()) { return new Date(ts).toLocaleDateString([], { month:'short', day:'numeric' }); }
function fmtDuration(ms) {
  if (ms == null) return '—';
  const m = Math.round(ms / 60000);
  if (m < 60) return `${m}m`;
  const h = Math.floor(m / 60), r = m % 60;
  return r ? `${h}h ${r}m` : `${h}h`;
}
function esc(s = '') { return String(s).replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c])); }
function planVersion(plan) { return planVersionId(plan); }

function current() {
  const events = loadEvents(), plan = loadPlan();
  const preliminary = decide({ now, events, plan });
  const endAt = preliminary.state.nightCompletedAt ? new Date(preliminary.state.nightCompletedAt) : now;
  return { events, plan, d: preliminary, metrics: deriveMetrics(events, endAt) };
}
function setView(v) { view = v; modal = null; render(); }

function maybeAlertCheckIn(d) {
  if (d.primaryAction !== 'CHECK_IN' || document.visibilityState !== 'visible') return;
  const key = `${d.state.currentWakingStartedAt || ''}:${d.state.checkInsThisWaking}:${d.nextActionAt || ''}`;
  if (key === lastCheckInAlertKey) return;
  lastCheckInAlertKey = key;
  toast = { message:'Check-in available.', undoable:false, eventId:null, until:Date.now()+5000 };
  try { navigator.vibrate?.([50,70,50]); } catch {}
}

function showToast(message, undoable = false, eventId = null) {
  toast = { message, undoable, eventId, until: Date.now() + 8000 };
  render();
  setTimeout(() => {
    if (toast?.until <= Date.now()) { toast = null; render(); }
  }, 8100);
}

function syncArchiveIfComplete() {
  const { events, plan, d, metrics } = current();
  if (d.state.session !== SessionState.COMPLETE) return;
  const completion = d.state.nightCompletedAt || new Date().toISOString();
  archiveNight(buildSummary(events, plan, deriveMetrics(events, new Date(completion)), completion), events);
}

function fire(type, opts = {}, { toastMessage = null, undoable = true } = {}) {
  if (type === 'CARE_CONCERN_STARTED') view = 'tonight';
  const { events } = current();
  const valid = validateTransition(events, type);
  if (!valid.ok) { showToast(valid.reason, false); return null; }
  const e = appendEvent(type, opts);
  syncArchiveIfComplete();
  showToast(toastMessage || type.replaceAll('_',' ').toLowerCase(), undoable, undoable ? e.eventId : null);
  return e;
}

function undo(eventId) {
  deleteEvent(eventId);
  toast = null;
  syncArchiveIfComplete();
  render();
}

function beginNight() {
  const { d } = current();
  if (d.state.session !== SessionState.PREP) return;
  const at = new Date().toISOString();
  appendEvents([
    { eventType:'NIGHT_STARTED' },
    { eventType:'PLACED_IN_CRIB' },
    { eventType:'AWAKE_CALM' }
  ], { occurredAt: at, recordedAt: at, source:'USER_TAP' });
  view = 'tonight';
  showToast('Night started. Crib time recorded.', false);
}

function buildSummary(events, plan, metrics, completedAt) {
  const normalized = normalizeEvents(events);
  const start = normalized.find(e => e.eventType === 'NIGHT_STARTED');
  const crib = normalized.find(e => e.eventType === 'PLACED_IN_CRIB');
  const firstSleep = normalized.find(e => e.eventType === 'ASLEEP');
  return {
    nightId: loadNightId(),
    nightNumber: loadNightNo(),
    date: start?.occurredAt || completedAt,
    completedAt,
    planVersionId: start?.planVersionId || planVersion(plan),
    planSnapshot: structuredClone(plan),
    cribAt: crib?.occurredAt || start?.occurredAt || null,
    firstAsleepAt: firstSleep?.occurredAt || null,
    morningWakeAt: completedAt,
    metrics: structuredClone(metrics)
  };
}

function completeNight() {
  const { events, plan } = current();
  const valid = validateTransition(events, 'NIGHT_COMPLETED');
  if (!valid.ok) { showToast(valid.reason, false); return; }
  const e = appendEvent('NIGHT_COMPLETED');
  const updated = loadEvents();
  const metrics = deriveMetrics(updated, new Date(e.occurredAt));
  archiveNight(buildSummary(updated, plan, metrics, e.occurredAt), updated);
  view = 'tonight';
  showToast('Night complete. Review saved locally.', false);
}

function startCheckIn() {
  view = 'tonight';
  const { events, d } = current();
  const valid = validateTransition(events, 'CHECKIN_STARTED');
  if (!valid.ok) { showToast(valid.reason, false); return; }
  const defs = [];
  if (d.primaryAction === 'WAIT' && d.nextActionAt && new Date(d.nextActionAt) > new Date()) {
    defs.push({ eventType:'PARENT_OVERRIDE', metadata:{ action:'CHECKIN_STARTED', reason:'EARLY_CHECKIN' } });
  }
  defs.push({ eventType:'CHECKIN_STARTED' });
  appendEvents(defs, { occurredAt:new Date().toISOString() });
  showToast('Check-in started.', false);
}

function startFeed(source = 'PARENT') {
  view = 'tonight';
  const { events, d } = current();
  const valid = validateTransition(events, 'FEED_STARTED');
  if (!valid.ok) { showToast(valid.reason, false); return; }
  const defs = [];
  if (d.primaryAction !== 'FEED') defs.push({ eventType:'PARENT_OVERRIDE', metadata:{ action:'FEED_STARTED', reason:'PARENT_CHOICE', source } });
  defs.push({ eventType:'FEED_STARTED' });
  appendEvents(defs, { occurredAt:new Date().toISOString() });
  modal = null;
  showToast('Feed started.', false);
}

function resolveSafety() {
  view = 'tonight';
  const { events } = current();
  const valid = validateTransition(events, 'CARE_CONCERN_RESOLVED');
  if (!valid.ok) { showToast(valid.reason, false); return; }
  appendEvent('CARE_CONCERN_RESOLVED');
  modal = { type:'mark-state', title:'What is baby doing now?' };
  toast = null;
  render();
}

function correctLastState() {
  const active = normalizeEvents(loadEvents()).filter(e => ['ASLEEP','AWAKE_CALM','FUSSING','CRYING'].includes(e.eventType));
  const target = active.at(-1);
  if (!target) { showToast('No baby-state event to correct yet.', false); return; }
  modal = { type:'correct-state', eventId:target.eventId, originalAt:target.occurredAt };
  render();
}

function applyStateCorrection(eventId) {
  const state = document.querySelector('#correct-state')?.value;
  const offset = Number(document.querySelector('#correct-offset')?.value || 0);
  if (!state) return;
  const { d } = current();
  const base = d.state.nightCompletedAt ? new Date(d.state.nightCompletedAt) : new Date();
  const candidate = new Date(base.getTime() - offset * 60000);
  const floor = d.state.nightStartedAt ? new Date(d.state.nightStartedAt) : candidate;
  const ceiling = d.state.nightCompletedAt ? new Date(d.state.nightCompletedAt) : new Date();
  const occurredAt = new Date(Math.min(Math.max(candidate.getTime(), floor.getTime()), ceiling.getTime())).toISOString();
  appendEvent(state, { source:'CORRECTION', supersedesEventId:eventId, occurredAt, metadata:{ offsetMinutes:offset } });
  modal = null;
  syncArchiveIfComplete();
  showToast('State corrected.', false);
}

function saveReview(feeling = null) {
  const { d } = current();
  const nightId = loadNightId();
  const notes = document.querySelector('#review-notes')?.value ?? undefined;
  const patch = {};
  if (feeling) patch.feeling = feeling;
  if (notes !== undefined) patch.notes = notes;
  updateNightReview(nightId, patch);
  render();
}

function nextNight() {
  const result = prepareNextNight();
  if (!result.advanced) { setView('progress'); return; }
  view = 'tonight';
  modal = null;
  toast = null;
  render();
}

function render() {
  now = new Date();
  const { events, plan, d, metrics } = current();
  const night = d.state.session === SessionState.ACTIVE || d.state.session === SessionState.CARE_SUSPENDED;
  const focus = night && (d.state.safetyState === 'CARE_CONCERN' || d.state.caregiverAction !== CaregiverAction.NONE);
  if (night) maybeAlertCheckIn(d);
  document.body.className = `${night ? 'night' : 'day'} web-app`;
  app.innerHTML = `<main class="shell ${night?'night':'day'} ${night?'has-night-dock':''}">
    ${topBar(plan,d)}${storageBanner()}${syncBanner(d)}${content(events,plan,d,metrics)}${nightDock(d)}${nav(d,focus)}
  </main>${modalHtml(d)}${toastHtml()}`;
  bind();
}

function topBar(plan,d) {
  const connectivity = navigator.onLine ? '' : '<span class="offline-pill">OFFLINE</span>';
  const sync = syncBadge();
  return `<div class="topbar"><div><div class="eyebrow">Night ${loadNightNo()} of 14 · ${fmtDate(d.state.nightStartedAt || now)}</div><div class="title">${esc(plan.babyName)} Sleep</div></div><div class="top-right">${connectivity}${sync}<div class="clock">${now.toLocaleTimeString([], {hour:'numeric',minute:'2-digit'})}</div></div></div>`;
}
function syncBadge() {
  if (!isCloudConfigured()) return '';
  const map = {
    ready:['SYNCED','sync-ok'], syncing:['SYNCING','sync-warn'], loading_family:['SYNCING','sync-warn'],
    signed_out:[cloud.accessMode==='invite'?'JOIN':'SIGN IN','sync-muted'], needs_household:['SET UP','sync-warn'],
    conflict:['SYNC ISSUE','sync-bad'], offline_cached:['OFFLINE CACHE','sync-warn']
  };
  const [label, cls] = map[cloud.status] || ['SYNC','sync-muted'];
  const pending = cloud.pending ? ` · ${cloud.pending}` : '';
  return `<button class="sync-pill ${cls}" id="sync-status" aria-label="Family sync status">${label}${pending}</button>`;
}
function syncBanner(d) {
  if (!isCloudConfigured()) return '';
  if (cloud.status === 'conflict') return `<button class="sync-banner sync-danger" id="sync-banner-open"><strong>Family sync needs attention</strong><span>Your local night is preserved. Tap to resolve before switching devices.</span></button>`;
  if (cloud.error && d.state.session === SessionState.PREP) return `<button class="sync-banner" id="sync-banner-open"><strong>Sync warning</strong><span>${esc(cloud.error)}</span></button>`;
  if (!navigator.onLine && d.state.session !== SessionState.PREP) return `<div class="sync-banner passive"><strong>Logging offline</strong><span>Events stay on this device and will sync when connection returns.</span></div>`;
  return '';
}
function storageBanner() {
  const status = getPersistenceStatus();
  if (status.available) return '';
  return `<div class="storage-warning"><strong>Storage warning</strong><span>Events are being kept in memory, but may not survive a reload on this device. Keep this page open until storage is restored.</span></div>`;
}
function content(events,plan,d,metrics) {
  // Safety and active caregiving always outrank navigation. A parent can trigger NEEDS CARE from any tab.
  if (d.state.safetyState === 'CARE_CONCERN') return safetyView();
  if (d.state.caregiverAction === CaregiverAction.CHECK_IN) return checkinView(d);
  if (d.state.caregiverAction === CaregiverAction.FEEDING) return feedView(d);
  if (d.state.caregiverAction !== CaregiverAction.NONE) return genericCareView(d);
  // Never block an active night because auth/network changed. During PREP, cloud setup can gate the shared-family workflow.
  if (d.state.session === SessionState.PREP && isCloudConfigured()) {
    if (['signed_out','auth_sending','starting'].includes(cloud.status)) return authView();
    if (cloud.status === 'needs_household') return householdSetupView();
    if (cloud.status === 'conflict') return conflictView();
  }
  if (view === 'timeline') return timeline(events,d);
  if (view === 'progress') return progress(metrics,d);
  if (view === 'plan') return planView(plan,d);
  if (d.state.session === SessionState.PREP) return prepView(plan);
  if (d.state.session === SessionState.COMPLETE) return reviewView(events,metrics);
  return nightView(events,plan,d,metrics);
}

function authView() {
  const mode = cloud.accessMode;
  const setup = mode === 'setup';
  const invite = mode === 'invite';
  const busy = cloud.status === 'auth_sending';
  const title = setup ? 'Set up your private family workspace' : (invite ? 'Join your family workspace' : 'Sign in to your family workspace');
  const action = setup ? 'CREATE OWNER ACCOUNT' : (invite ? 'CREATE & JOIN' : 'SIGN IN');
  return `<section class="card auth-card"><div class="eyebrow">Private family sync</div><h2>${title}</h2><p class="micro">One shared night across phones. Sleep events remain append-only, and access is limited to authenticated family members.</p>
    ${setup?`<div class="field"><label>Family name</label><input id="auth-family" maxlength="80" autocomplete="organization" value="Aighewi’s" placeholder="Family name"></div>`:''}
    <div class="field"><label>Email</label><input id="auth-email" type="email" autocomplete="email" inputmode="email" value="${setup?'aighewifamily@gmail.com':''}" placeholder="you@example.com"></div>
    <div class="field"><label>Password</label><input id="auth-password" type="password" autocomplete="${setup||invite?'new-password':'current-password'}" minlength="12" placeholder="12+ characters" aria-describedby="password-help"></div>
    <div id="password-help" class="micro">${setup||invite?'Use a new password with at least 12 characters.':'Enter your account password.'}</div>
    <button class="primary" id="${setup||invite?'access-signup':'password-signin'}" ${busy?'disabled':''}>${busy?'WORKING…':action}</button>
    ${invite?'<p class="micro">If you already have an account, open the normal app URL and sign in first, then reopen this invite link.</p>':''}
    ${setup?'<p class="micro">This one-time setup link expires after use. Save the normal app URL after setup.</p>':''}
  </section>`;
}
function householdSetupView() {
  return `<section class="card auth-card"><div class="eyebrow">Family workspace</div><h2>Create your private family space</h2><p class="micro">Create it once, then invite the other caregiver with a one-time link.</p><div class="field"><label>Family name</label><input id="household-name" maxlength="80" placeholder="Atlas's Family"></div><button class="primary" id="create-household">CREATE FAMILY SPACE</button></section>`;
}
function conflictView() {
  return `<section class="card auth-card conflict-card"><div class="eyebrow danger-text">Sync conflict protected</div><h2>Two different active nights were found.</h2><p>Your local events have been backed up on this device. To avoid merging unrelated nights, the app will not guess.</p><button class="primary" id="accept-cloud">USE FAMILY CLOUD NIGHT</button><p class="micro">This replaces the active view on this device with the family version. The local conflict backup is retained in browser storage for recovery.</p></section>`;
}

function prepView(plan) {
  const checks = loadChecklist();
  const ready = checklistItems.every(x => checks[x]);
  const feedText = plan.feeding.mode === 'NONE'
    ? 'No planned feed'
    : (plan.feeding.windows?.[0] ? `${plan.feeding.windows[0].start}–${plan.feeding.windows[0].end}` : 'Custom');
  return `<section class="card prep-hero"><div class="eyebrow">Tonight's plan</div><h2>Prepare once. Think less later.</h2><p class="micro">The guide follows the rules saved before bedtime. Feeding and caregiving rules never change automatically overnight.</p>
    <div class="plan-strip"><div><span>Bedtime</span><strong>${esc(plan.bedtimeTarget)}</strong></div><div><span>Morning</span><strong>${esc(plan.morningStart)}</strong></div><div><span>Handling</span><strong>${esc(plan.caregiver || 'Parent')}</strong></div></div>
  </section>
  <div class="section-title">Bedtime checklist</div><section class="check-list">${checklistItems.map((x,i)=>`<label class="check"><input data-check="${i}" type="checkbox" ${checks[x]?'checked':''}><span>${esc(x)}</span></label>`).join('')}</section>
  <section class="card"><div class="metric"><span>Last nap ended</span><strong>${esc(plan.lastNapEnded || 'Not set')}</strong></div><div class="metric"><span>Final wake window</span><strong>${plan.finalWakeWindowMinutes ? `${Math.round(plan.finalWakeWindowMinutes/60*10)/10}h` : 'Not set'}</strong></div><div class="metric"><span>Check-ins</span><strong>${esc((plan.checkIn.intervalsMinutes||[]).join(' / '))} min</strong></div><div class="metric"><span>Planned feed</span><strong>${esc(feedText)}</strong></div></section>
  <button class="primary" id="begin" ${ready?'':'disabled'}>PLACE IN CRIB AWAKE · BEGIN NIGHT</button>
  ${ready?'':'<p class="micro centered">Complete the checklist to begin.</p>'}
  <p class="micro centered">Safe sleep baseline: back for sleep, firm and flat approved sleep surface, fitted sheet only, clear sleep space, and non-weighted sleep products.</p>`;
}

function quickActions(state) {
  const map = {
    [BabyState.CRYING]: [['AWAKE_CALM','MARK CALM'],['ASLEEP','ASLEEP']],
    [BabyState.FUSSING]: [['CRYING','CRYING'],['ASLEEP','ASLEEP']],
    [BabyState.AWAKE_CALM]: [['CRYING','CRYING'],['ASLEEP','ASLEEP']],
    [BabyState.ASLEEP]: [['AWAKE_CALM','WOKE CALM'],['CRYING','WOKE CRYING']],
    [BabyState.UNKNOWN]: [['AWAKE_CALM','AWAKE / CALM'],['CRYING','CRYING']]
  };
  return map[state] || map[BabyState.UNKNOWN];
}

function nightView(events,plan,d,metrics) {
  const state = d.state.babyState;
  const elapsed = msSince(d.timerStartedAt, now);
  const inCrib = msSince(d.state.cribPlacedAt, now);
  const next = d.nextActionAt ? Math.max(0, new Date(d.nextActionAt) - now) : null;
  const actionable = { CHECK_IN:'START CHECK-IN', FEED:'START FEED', START_DAY:'START DAY', MARK_STATE:'MARK CURRENT STATE' };
  const quick = quickActions(state);
  const waitOverride = d.primaryAction === 'WAIT' ? '<button class="secondary full early-checkin" id="check-in-now">CHECK IN EARLY</button>' : '';
  const primary = actionable[d.primaryAction]
    ? `<button class="primary" data-primary="${d.primaryAction}">${actionable[d.primaryAction]}</button>`
    : '';
  return `<section class="card hero state-${state}"><div class="status-row"><span class="status-pill"><span class="dot"></span>${esc(state.replaceAll('_',' '))}</span><div class="status-meta"><span class="micro">${esc(d.context.replaceAll('_',' '))}</span><span class="in-crib">IN CRIB · ${fmtElapsed(inCrib)}</span></div></div>
    <div class="timer-label">${state === 'CRYING' ? 'CRYING' : 'CURRENT STATE'}</div><div class="timer">${fmtElapsed(elapsed)}</div>
    ${next != null ? `<div class="next">Next check-in available in <strong>${fmtElapsed(next)}</strong></div>` : ''}
    <div class="instruction"><div class="instruction-kicker">Right now</div><h2>${esc(d.headline)}</h2><p>${esc(d.instruction)}</p></div>
    ${primary}${waitOverride}
    <div class="secondary-grid compact">${quick.map(([event,label])=>`<button class="secondary" data-event="${event}">${label}</button>`).join('')}</div>
    <button class="more-btn" id="more-care">MORE OPTIONS</button>
  </section>
  <details class="card tonight-details"><summary>Tonight so far</summary><div class="metric"><span>Sleep</span><strong>${fmtDuration(metrics.totalNightSleepMs)}</strong></div><div class="metric"><span>Wakings</span><strong>${metrics.wakingCount}</strong></div><div class="metric"><span>Check-ins</span><strong>${metrics.checkInCount}</strong></div><div class="metric"><span>Feeds</span><strong>${metrics.feedCount}</strong></div></details>`;
}

function checkinView(d) {
  return `<section class="care-screen"><div class="eyebrow">Check-in</div><div class="timer">${fmtElapsed(msSince(d.timerStartedAt,now))}</div><div class="card"><div class="care-list"><div class="care-item">Quiet voice</div><div class="care-item">Minimal stimulation</div><div class="care-item">Reassure</div><div class="care-item">Keep lights low</div><div class="care-item">Avoid restarting bedtime</div><div class="care-item">Leave while baby is awake</div></div><p class="micro">Optional phrase: “You’re safe. It’s sleepy time. I love you.”</p></div><button class="primary" id="leave-room">LEAVE ROOM</button></section>`;
}
function feedView(d) {
  return `<section class="care-screen"><div class="eyebrow">Night feed</div><div class="timer">${fmtElapsed(msSince(d.timerStartedAt,now))}</div><div class="card"><div class="care-list"><div class="care-item">Keep room dark</div><div class="care-item">Keep interaction quiet</div><div class="care-item">No play</div></div></div><button class="primary" id="finish-feed">RETURN TO CRIB</button></section>`;
}
function genericCareView(d) {
  const labels = { DIAPER:'DIAPER CARE', COMFORTING:'COMFORTING', OTHER_CARE:'CARE IN PROGRESS' };
  const endMap = { DIAPER:'DIAPER_ENDED', COMFORTING:'COMFORT_ENDED', OTHER_CARE:'OTHER_CARE_ENDED' };
  const end = endMap[d.state.caregiverAction] || 'OTHER_CARE_ENDED';
  return `<section class="care-screen"><div class="eyebrow">Care mode</div><div class="timer">${fmtElapsed(msSince(d.timerStartedAt,now))}</div><div class="card"><h2 class="care-title">${labels[d.state.caregiverAction]||'CARE IN PROGRESS'}</h2><p class="micro">Sleep-training guidance is paused while you handle the care you selected.</p></div><button class="primary" data-care-end="${end}">CARE COMPLETE</button></section>`;
}
function safetyView() {
  return `<section class="care-screen safety-screen"><div class="card safety-card"><div class="eyebrow danger-text">Safety override</div><h1>GO IN</h1><p>Check baby whenever something seems wrong. Sleep-training timers and prior care actions are suspended.</p><p class="micro">Examples include illness, breathing concern, vomiting, injury, dirty or leaking diaper, unusual or distressed crying, or parent concern.</p></div><button class="primary safety-primary" id="resolve-safety">CARE RESOLVED</button></section>`;
}

function timeline(events,d) {
  const ordered = normalizeEvents(events).slice().sort((a,b)=>new Date(b.occurredAt)-new Date(a.occurredAt));
  return `<div class="section-title">Tonight's timeline</div><section class="card timeline">${ordered.length ? ordered.map(e => `<div class="event"><div class="event-time">${fmtTime(e.occurredAt)}</div><div class="event-name">${esc(e.eventType.replaceAll('_',' '))}${e.source==='CORRECTION'?'<span class="corrected-tag">CORRECTED</span>':''}</div><span></span></div>`).join('') : '<p class="micro">No events yet.</p>'}</section>
    ${d.state.session!==SessionState.PREP?'<button class="secondary full" id="correct-last">CORRECT LAST BABY STATE</button>':''}`;
}

function progress(metrics,d) {
  const history = loadHistory();
  const byNight = new Map(history.map(x => [Number(x.nightNumber), x]));
  const latest = history.at(-1);
  const first = history[0];
  const delta = (field, invert = false) => {
    if (!first || !latest || first.nightId === latest.nightId) return '—';
    const a = first.metrics?.[field], b = latest.metrics?.[field];
    if (a == null || b == null) return '—';
    const diff = b-a;
    const arrow = diff === 0 ? '→' : diff < 0 ? '↓' : '↑';
    return `${arrow} ${fmtDuration(Math.abs(diff))}`;
  };
  return `<div class="section-title">14-night cycle</div><section class="card"><div class="progress-grid">${Array.from({length:14},(_,i)=>{const n=i+1; const complete=byNight.has(n); const current=n===loadNightNo()&&d.state.session!==SessionState.COMPLETE; return `<div class="night-dot ${complete?'complete':''} ${current?'current':''}">${n}</div>`}).join('')}</div></section>
  ${history.length ? `<section class="card"><div class="eyebrow">Change from Night ${first.nightNumber} to ${latest.nightNumber}</div><div class="metric"><span>Time to sleep</span><strong>${delta('timeToSleepMs')}</strong></div><div class="metric"><span>Longest stretch</span><strong>${delta('longestSleepStretchMs')}</strong></div><div class="metric"><span>Crying</span><strong>${delta('cryDurationMs')}</strong></div></section>` : ''}
  <section class="card"><div class="eyebrow">${d.state.session===SessionState.COMPLETE?'Latest night':'Current night'}</div><div class="metric"><span>Time to sleep</span><strong>${fmtDuration(metrics.timeToSleepMs)}</strong></div><div class="metric"><span>Night wakings</span><strong>${metrics.wakingCount}</strong></div><div class="metric"><span>Check-ins</span><strong>${metrics.checkInCount}</strong></div><div class="metric"><span>Longest stretch</span><strong>${fmtDuration(metrics.longestSleepStretchMs)}</strong></div><div class="metric"><span>Awake overnight</span><strong>${fmtDuration(metrics.awakeOvernightMs)}</strong></div><div class="metric"><span>Crying</span><strong>${fmtDuration(metrics.cryDurationMs)}</strong></div></section>
  ${history.length ? `<div class="night-history">${history.slice().reverse().map(h=>`<section class="card history-card"><div class="history-head"><strong>Night ${h.nightNumber}</strong><span>${fmtDate(h.date)}</span></div><div class="history-grid"><div><span>Sleep onset</span><strong>${fmtDuration(h.metrics?.timeToSleepMs)}</strong></div><div><span>Wakings</span><strong>${h.metrics?.wakingCount ?? '—'}</strong></div><div><span>Longest</span><strong>${fmtDuration(h.metrics?.longestSleepStretchMs)}</strong></div><div><span>Crying</span><strong>${fmtDuration(h.metrics?.cryDurationMs)}</strong></div></div></section>`).join('')}</div>`:''}
  <p class="micro">No composite sleep score. Metrics stay separate so the data does not imply a judgment about the parent or baby.</p>`;
}

function planView(plan,d) {
  const locked = d.state.session !== SessionState.PREP;
  const feed = plan.feeding.windows?.[0] || { start:'01:00', end:'03:00', feedOnWaking:true, wakeToFeed:false };
  if (locked) return `<div class="section-title">Tonight's rules</div><section class="card locked-plan"><div class="eyebrow">Locked for this night</div><h2>Tonight’s rules stay fixed.</h2><p class="micro">This prevents an accidental 2 AM edit from changing the protocol mid-night. Start the next night before editing the plan.</p><div class="metric"><span>Mode</span><strong>${plan.mode==='TRACK_ONLY'?'Track only':'Guide + track'}</strong></div><div class="metric"><span>Morning</span><strong>${esc(plan.morningStart)}</strong></div><div class="metric"><span>Check-ins</span><strong>${esc(plan.checkIn.intervalsMinutes.join(' / '))} min</strong></div><div class="metric"><span>Feeding</span><strong>${plan.feeding.mode==='NONE'?'None planned':`${feed.start}–${feed.end}`}</strong></div></section>`;
  return `<div class="section-title">Tonight's rules</div><section class="card"><div class="field"><label>Baby name</label><input id="baby-name" value="${esc(plan.babyName)}"></div>
    <div class="row"><div class="field"><label>Bedtime target</label><input type="time" id="bedtime" value="${esc(plan.bedtimeTarget)}"></div><div class="field"><label>Morning start</label><input type="time" id="morning" value="${esc(plan.morningStart)}"></div></div>
    <div class="field"><label>Who is handling tonight</label><input id="caregiver" value="${esc(plan.caregiver || 'Parent')}"></div>
    <div class="row"><div class="field"><label>Last nap ended</label><input type="time" id="last-nap" value="${esc(plan.lastNapEnded || '')}"></div><div class="field"><label>Final wake window, min</label><input inputmode="numeric" id="wake-window" value="${esc(plan.finalWakeWindowMinutes || 150)}"></div></div>
    <div class="row"><div class="field"><label>Last feed</label><input type="time" id="last-feed" value="${esc(plan.lastFeedTime || '')}"></div><div class="field"><label>Mode</label><select id="mode"><option value="GUIDE_TRACK" ${plan.mode==='GUIDE_TRACK'?'selected':''}>Guide + track</option><option value="TRACK_ONLY" ${plan.mode==='TRACK_ONLY'?'selected':''}>Track only</option></select></div></div>
    <div class="field"><label>Check-in intervals, minutes</label><input id="intervals" value="${esc(plan.checkIn.intervalsMinutes.join(', '))}"></div>
    <div class="field"><label>Overnight feeding plan</label><select id="feed-mode"><option value="NONE" ${plan.feeding.mode==='NONE'?'selected':''}>None — clinician approved</option><option value="ONE_PLANNED_FEED" ${plan.feeding.mode!=='NONE'?'selected':''}>One planned feed</option></select></div>
    <div class="row"><div class="field"><label>Feed window starts</label><input type="time" id="feed-start" value="${esc(feed.start)}"></div><div class="field"><label>Feed window ends</label><input type="time" id="feed-end" value="${esc(feed.end)}"></div></div>
    <label class="check compact-check"><input type="checkbox" id="feed-on-waking" ${feed.feedOnWaking?'checked':''}><span>Offer planned feed when baby wakes in this window</span></label>
    <label class="check compact-check"><input type="checkbox" id="wake-to-feed" ${feed.wakeToFeed?'checked':''}><span>Wake for this feed during the window</span></label>
    <p class="micro">The app records and follows the feeding rules you enter. It does not decide that an overnight feed is no longer needed. Reducing overnight nutrition should follow your pediatric clinician’s guidance.</p>
    <button class="primary" id="save-plan">SAVE NEW PLAN VERSION</button></section><button class="secondary full" id="reset">RESET LOCAL DATA</button>`;
}

function reviewView(events,metrics) {
  const history = loadHistory();
  const saved = history.find(x => x.nightId === loadNightId());
  const review = saved?.review || {};
  const started = normalizeEvents(events).find(e=>e.eventType==='NIGHT_STARTED')?.occurredAt;
  const crib = normalizeEvents(events).find(e=>e.eventType==='PLACED_IN_CRIB')?.occurredAt;
  const first = normalizeEvents(events).find(e=>e.eventType==='ASLEEP')?.occurredAt;
  const complete = normalizeEvents(events).find(e=>e.eventType==='NIGHT_COMPLETED')?.occurredAt;
  const feelings = ['Easier','Manageable','Neutral','Difficult','Very difficult'];
  return `<section class="card"><div class="eyebrow">Night ${loadNightNo()} complete</div><h2 class="review-title">Morning review</h2><div class="metric"><span>In crib</span><strong>${fmtTime(crib||started)}</strong></div><div class="metric"><span>Asleep</span><strong>${fmtTime(first)}</strong></div><div class="metric"><span>Time to sleep</span><strong>${fmtDuration(metrics.timeToSleepMs)}</strong></div><div class="metric"><span>Night wakings</span><strong>${metrics.wakingCount}</strong></div><div class="metric"><span>Check-ins</span><strong>${metrics.checkInCount}</strong></div><div class="metric"><span>Feeds</span><strong>${metrics.feedCount}</strong></div><div class="metric"><span>Longest stretch</span><strong>${fmtDuration(metrics.longestSleepStretchMs)}</strong></div><div class="metric"><span>Total night sleep</span><strong>${fmtDuration(metrics.totalNightSleepMs)}</strong></div><div class="metric"><span>Morning wake</span><strong>${fmtTime(complete)}</strong></div></section>
    <section class="card"><div class="eyebrow">How did tonight feel?</div><div class="chips review-chips">${feelings.map(x=>`<button class="chip ${review.feeling===x?'selected':''}" data-feel="${x}">${x}</button>`).join('')}</div><div class="field"><label>Optional notes</label><textarea id="review-notes" rows="3">${esc(review.notes||'')}</textarea></div><button class="secondary full" id="save-review">SAVE NOTES</button></section>
    ${loadNightNo()<14?'<button class="primary" id="next-night">PREPARE NIGHT '+(loadNightNo()+1)+'</button>':'<button class="primary" data-view="progress">VIEW 14-NIGHT REVIEW</button>'}`;
}

function nightDock(d) {
  if (d.state.session !== SessionState.ACTIVE || d.state.safetyState === 'CARE_CONCERN') return '';
  return `<div class="night-dock"><button class="dock-help" id="what-now">WHAT DO I DO?</button><button class="dock-care" data-event="CARE_CONCERN_STARTED">NEEDS CARE</button></div>`;
}
function nav(d,focus) {
  if (focus) return '';
  return `<nav class="bottom-nav"><button class="nav-btn ${view==='tonight'?'active':''}" data-view="tonight">TONIGHT</button><button class="nav-btn ${view==='timeline'?'active':''}" data-view="timeline">TIMELINE</button><button class="nav-btn ${view==='progress'?'active':''}" data-view="progress">PROGRESS</button><button class="nav-btn ${view==='plan'?'active':''}" data-view="plan">PLAN</button></nav>`;
}

function modalHtml(d) {
  if (!modal) return '';
  const type = typeof modal === 'string' ? modal : modal.type;
  if (type === 'sync') {
    if (!isCloudConfigured()) return '';
    if (cloud.status === 'signed_out' || cloud.status === 'auth_sending') return `<div class="modal-wrap" role="dialog" aria-modal="true"><div class="sheet"><div class="eyebrow">Family sync</div>${authView()}<button class="link-btn" id="cancel-modal">Close</button></div></div>`;
    if (cloud.status === 'needs_household') return `<div class="modal-wrap" role="dialog" aria-modal="true"><div class="sheet"><div class="eyebrow">Family sync</div>${householdSetupView()}<button class="link-btn" id="cancel-modal">Close</button></div></div>`;
    if (cloud.status === 'conflict') return `<div class="modal-wrap" role="dialog" aria-modal="true"><div class="sheet">${conflictView()}<button class="link-btn" id="cancel-modal">Close</button></div></div>`;
    const invite = cloud.invitePending;
    return `<div class="modal-wrap" role="dialog" aria-modal="true"><div class="sheet sync-sheet"><div class="eyebrow">Family sync</div><h2>${esc(cloud.household?.name || 'Family')}</h2><div class="sync-detail"><span>Status</span><strong>${esc(cloud.status==='ready'?'Synced':cloud.status.replaceAll('_',' '))}</strong></div><div class="sync-detail"><span>Signed in</span><strong>${esc(cloud.user?.email || '')}</strong></div><div class="sync-detail"><span>Role</span><strong>${esc(cloud.role || '')}</strong></div>${invite?`<div class="invite-box"><label>One-time invite link</label><div class="invite-link">${esc(invite.link)}</div><button class="secondary full" id="copy-invite" data-link="${esc(invite.link)}">COPY INVITE LINK</button><p class="micro">Expires in 7 days and can be used once.</p></div>`:(cloud.role==='owner'?'<button class="secondary full" id="create-invite">CREATE CAREGIVER INVITE</button>':'')}<button class="link-btn" id="cloud-signout">Sign out</button><button class="link-btn" id="cancel-modal">Close</button></div></div>`;
  }
  if (type === 'after-checkin') return `<div class="modal-wrap" role="dialog" aria-modal="true"><div class="sheet"><div class="eyebrow">Leaving room</div><h2>What is baby doing now?</h2><div class="state-grid"><button data-after="CRYING">Crying</button><button data-after="FUSSING">Fussing</button><button data-after="AWAKE_CALM">Calm</button><button data-after="ASLEEP">Asleep</button></div><button class="link-btn" id="cancel-modal">Cancel</button></div></div>`;
  if (type === 'after-feed') return `<div class="modal-wrap" role="dialog" aria-modal="true"><div class="sheet"><div class="eyebrow">Return to crib</div><h2>How did baby return?</h2><div class="state-grid"><button data-feed-return="AWAKE_CALM">Awake</button><button data-feed-return="AWAKE_CALM">Drowsy</button><button data-feed-return="ASLEEP">Asleep</button></div><button class="link-btn" id="cancel-modal">Cancel</button></div></div>`;
  if (type === 'mark-state') return `<div class="modal-wrap" role="dialog" aria-modal="true"><div class="sheet"><div class="eyebrow">Current state</div><h2>${esc(modal.title || 'What is baby doing now?')}</h2><div class="state-grid"><button data-mark="AWAKE_CALM">Awake / calm</button><button data-mark="FUSSING">Fussing</button><button data-mark="CRYING">Crying</button><button data-mark="ASLEEP">Asleep</button></div></div></div>`;
  if (type === 'more-care') return `<div class="modal-wrap" role="dialog" aria-modal="true"><div class="sheet"><div class="eyebrow">More options</div><h2>Log what is happening.</h2><div class="state-grid"><button data-care="FEED_STARTED">Feed</button><button data-care="DIAPER_STARTED">Diaper</button><button data-care="COMFORT_STARTED">Comfort</button><button id="correct-from-more">Correct last state</button></div><button class="link-btn" id="cancel-modal">Cancel</button></div></div>`;
  if (type === 'correct-state') return `<div class="modal-wrap" role="dialog" aria-modal="true"><div class="sheet"><div class="eyebrow">Correct timeline</div><h2>What actually happened?</h2><div class="field"><label>State</label><select id="correct-state"><option value="ASLEEP">Asleep</option><option value="AWAKE_CALM">Awake / calm</option><option value="FUSSING">Fussing</option><option value="CRYING">Crying</option></select></div><div class="field"><label>When</label><select id="correct-offset"><option value="0">Now</option><option value="5">About 5 min ago</option><option value="10">About 10 min ago</option><option value="15">About 15 min ago</option><option value="30">About 30 min ago</option></select></div><button class="primary" id="apply-correction" data-target="${esc(modal.eventId)}">SAVE CORRECTION</button><button class="link-btn" id="cancel-modal">Cancel</button></div></div>`;
  if (type === 'what-now') {
    const next = d.nextActionAt ? Math.max(0,new Date(d.nextActionAt)-now) : null;
    return `<div class="modal-wrap" role="dialog" aria-modal="true"><div class="sheet command-sheet"><div class="eyebrow">Right now</div><h2>${esc(d.headline)}</h2><p>${esc(d.instruction)}</p>${next!=null?`<div class="command-next">Next check-in in <strong>${fmtElapsed(next)}</strong></div>`:''}<p class="micro">Based on the current state and tonight’s saved plan.</p>${['CHECK_IN','FEED','START_DAY','MARK_STATE'].includes(d.primaryAction)?`<button class="primary" data-modal-primary="${d.primaryAction}">${d.primaryAction==='CHECK_IN'?'START CHECK-IN':d.primaryAction==='FEED'?'START FEED':d.primaryAction==='START_DAY'?'START DAY':'MARK STATE'}</button>`:''}<button class="link-btn" id="cancel-modal">Close</button></div></div>`;
  }
  return '';
}
function toastHtml() { return toast ? `<div class="toast" role="status"><span>${esc(toast.message)}</span>${toast.undoable?`<button data-toast-undo="${toast.eventId}">UNDO</button>`:''}</div>` : ''; }

function handlePrimary(action) {
  if (action === 'CHECK_IN') startCheckIn();
  else if (action === 'FEED') startFeed('PLAN');
  else if (action === 'START_DAY') completeNight();
  else if (action === 'MARK_STATE') { modal = { type:'mark-state' }; render(); }
}

function bind() {
  const syncStatus = document.querySelector('#sync-status'); if (syncStatus) syncStatus.onclick = () => { modal={type:'sync'}; render(); };
  const syncBannerOpen = document.querySelector('#sync-banner-open'); if (syncBannerOpen) syncBannerOpen.onclick = () => { modal={type:'sync'}; render(); };
  const passwordSignin = document.querySelector('#password-signin'); if (passwordSignin) passwordSignin.onclick = async () => {
    try { await signInPassword(document.querySelector('#auth-email')?.value, document.querySelector('#auth-password')?.value); cloud=getCloudState(); render(); }
    catch (e) { showToast(String(e?.message||e), false); }
  };
  const accessSignup = document.querySelector('#access-signup'); if (accessSignup) accessSignup.onclick = async () => {
    try { await signUpAccess(document.querySelector('#auth-email')?.value, document.querySelector('#auth-password')?.value, document.querySelector('#auth-family')?.value); cloud=getCloudState(); render(); }
    catch (e) { showToast(String(e?.message||e), false); }
  };
  const createFamily = document.querySelector('#create-household'); if (createFamily) createFamily.onclick = async () => {
    try { createFamily.disabled=true; await createHousehold(document.querySelector('#household-name')?.value); cloud=getCloudState(); modal=null; render(); }
    catch (e) { showToast(String(e?.message||e), false); }
  };
  const inviteBtn = document.querySelector('#create-invite'); if (inviteBtn) inviteBtn.onclick = async () => {
    try { await createFamilyInvite(); cloud=getCloudState(); render(); }
    catch (e) { showToast(String(e?.message||e), false); }
  };
  const copyInvite = document.querySelector('#copy-invite'); if (copyInvite) copyInvite.onclick = async () => {
    try { await navigator.clipboard.writeText(copyInvite.dataset.link); showToast('Invite link copied.', false); }
    catch { showToast('Copy failed. Press and hold the invite link instead.', false); }
  };
  const signout = document.querySelector('#cloud-signout'); if (signout) signout.onclick = async () => { await signOutCloud(); modal=null; };
  const acceptCloud = document.querySelector('#accept-cloud'); if (acceptCloud) acceptCloud.onclick = async () => {
    try { acceptCloud.disabled=true; await acceptCloudConflict(); cloud=getCloudState(); modal=null; render(); }
    catch (e) { showToast(String(e?.message||e), false); }
  };
  document.querySelectorAll('[data-view]').forEach(b => b.onclick = () => setView(b.dataset.view));
  document.querySelectorAll('[data-event]').forEach(b => b.onclick = () => fire(b.dataset.event));
  document.querySelectorAll('[data-undo]').forEach(b => b.onclick = () => undo(b.dataset.undo));
  document.querySelectorAll('[data-toast-undo]').forEach(b => b.onclick = () => undo(b.dataset.toastUndo));
  document.querySelectorAll('[data-check]').forEach(inp => inp.onchange = () => {
    const c = loadChecklist(); c[checklistItems[Number(inp.dataset.check)]] = inp.checked; saveChecklist(c); render();
  });

  const begin = document.querySelector('#begin'); if (begin) begin.onclick = beginNight;
  document.querySelectorAll('[data-primary]').forEach(b => b.onclick = () => handlePrimary(b.dataset.primary));
  document.querySelectorAll('[data-modal-primary]').forEach(b => b.onclick = () => { modal = null; handlePrimary(b.dataset.modalPrimary); });
  const checkNow = document.querySelector('#check-in-now'); if (checkNow) checkNow.onclick = startCheckIn;
  const leave = document.querySelector('#leave-room'); if (leave) leave.onclick = () => { modal = { type:'after-checkin' }; render(); };
  const finish = document.querySelector('#finish-feed'); if (finish) finish.onclick = () => { modal = { type:'after-feed' }; render(); };
  const more = document.querySelector('#more-care'); if (more) more.onclick = () => { modal = { type:'more-care' }; render(); };
  const what = document.querySelector('#what-now'); if (what) what.onclick = () => { modal = { type:'what-now' }; render(); };
  const resolve = document.querySelector('#resolve-safety'); if (resolve) resolve.onclick = resolveSafety;
  const cancel = document.querySelector('#cancel-modal'); if (cancel) cancel.onclick = () => { modal = null; render(); };

  document.querySelectorAll('[data-after]').forEach(b => b.onclick = () => {
    const at = new Date().toISOString();
    appendEvents([{eventType:'CHECKIN_ENDED'},{eventType:b.dataset.after}], { occurredAt:at });
    modal = null; showToast('Check-in complete.', false);
  });
  document.querySelectorAll('[data-feed-return]').forEach(b => b.onclick = () => {
    const at = new Date().toISOString();
    appendEvents([
      { eventType:'FEED_ENDED', metadata:{ returned:b.textContent.trim() } },
      { eventType:b.dataset.feedReturn }
    ], { occurredAt:at });
    modal = null; showToast('Feed complete. Return logged.', false);
  });
  document.querySelectorAll('[data-mark]').forEach(b => b.onclick = () => { modal = null; fire(b.dataset.mark,{},{undoable:true}); });
  document.querySelectorAll('[data-care]').forEach(b => b.onclick = () => {
    if (b.dataset.care === 'FEED_STARTED') startFeed('MORE_OPTIONS');
    else { modal = null; fire(b.dataset.care, {}, { undoable:false }); }
  });
  document.querySelectorAll('[data-care-end]').forEach(b => b.onclick = () => {
    const { events } = current();
    const valid = validateTransition(events, b.dataset.careEnd);
    if (!valid.ok) { showToast(valid.reason, false); return; }
    appendEvent(b.dataset.careEnd);
    modal = { type:'mark-state', title:'What is baby doing now?' };
    toast = null;
    render();
  });

  const correctLast = document.querySelector('#correct-last'); if (correctLast) correctLast.onclick = correctLastState;
  const correctFromMore = document.querySelector('#correct-from-more'); if (correctFromMore) correctFromMore.onclick = correctLastState;
  const applyCorrection = document.querySelector('#apply-correction'); if (applyCorrection) applyCorrection.onclick = () => applyStateCorrection(applyCorrection.dataset.target);

  const save = document.querySelector('#save-plan'); if (save) save.onclick = () => {
    const { d } = current();
    if (d.state.session !== SessionState.PREP) { showToast('Tonight’s plan is locked after the night begins.', false); return; }
    const p = loadPlan();
    const ints = document.querySelector('#intervals').value.split(',').map(x=>Number(x.trim())).filter(x=>Number.isFinite(x)&&x>0&&x<=60);
    const feedMode = document.querySelector('#feed-mode').value;
    const window = {
      ...(p.feeding.windows?.[0] || { id:'feed-1', wakeToFeed:false }),
      start: document.querySelector('#feed-start').value || '01:00',
      end: document.querySelector('#feed-end').value || '03:00',
      feedOnWaking: document.querySelector('#feed-on-waking').checked,
      wakeToFeed: document.querySelector('#wake-to-feed').checked
    };
    const next = {
      ...p,
      version:(p.version||1)+1,
      babyName:document.querySelector('#baby-name').value.trim()||'Baby',
      bedtimeTarget:document.querySelector('#bedtime').value||p.bedtimeTarget,
      morningStart:document.querySelector('#morning').value||p.morningStart,
      caregiver:document.querySelector('#caregiver').value.trim()||'Parent',
      lastNapEnded:document.querySelector('#last-nap').value,
      lastFeedTime:document.querySelector('#last-feed').value,
      finalWakeWindowMinutes:Math.max(30,Math.min(360,Number(document.querySelector('#wake-window').value)||150)),
      mode:document.querySelector('#mode').value,
      checkIn:{...p.checkIn,intervalsMinutes:ints.length?ints:[3,5,10]},
      feeding:{...p.feeding,mode:feedMode,windows:feedMode==='NONE'?[]:[window]}
    };
    savePlan(next);
    appendEvent('PLAN_VERSION_CHANGED',{ metadata:{version:next.version,planVersionId:planVersion(next)}, source:'USER_TAP' });
    showToast(`Plan v${next.version} saved.`, false);
  };

  const reset = document.querySelector('#reset'); if (reset) reset.onclick = () => {
    if (confirm('Reset all local Sleep Command Center data on this device?')) { resetAll(); view='tonight'; modal=null; render(); }
  };
  document.querySelectorAll('[data-feel]').forEach(b => b.onclick = () => saveReview(b.dataset.feel));
  const saveReviewBtn = document.querySelector('#save-review'); if (saveReviewBtn) saveReviewBtn.onclick = () => saveReview();
  const next = document.querySelector('#next-night'); if (next) next.onclick = nextNight;
}

window.addEventListener('online', render);
window.addEventListener('offline', render);
window.addEventListener('scc:cloud-state', e => { cloud = e.detail || getCloudState(); render(); });
window.addEventListener('scc:local-change', () => { if (document.visibilityState === 'visible') render(); });
document.addEventListener('visibilitychange', () => { if (document.visibilityState === 'visible') render(); });
setInterval(() => { if (document.visibilityState === 'visible') render(); }, 1000);

render();
startCloudSync().catch(e => { console.error('Cloud sync bootstrap failed', e); });