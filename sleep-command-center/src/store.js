import { defaultPlan, makeEvent, planVersionId, uid } from './engine.js';

const KEYS = {
  events: 'scc.events.v3',
  plan: 'scc.plan.v3',
  checklist: 'scc.checklist.v3',
  nightNo: 'scc.nightNo.v3',
  nightId: 'scc.nightId.v3',
  history: 'scc.history.v3',
  deviceId: 'scc.deviceId.v1'
};

const LEGACY_KEYS = {
  events: 'scc.events.v2', plan: 'scc.plan.v2', checklist: 'scc.checklist.v2',
  nightNo: 'scc.nightNo.v2', nightId: 'scc.nightId.v2', history: 'scc.history.v2'
};

const memory = new Map();
let persistence = { available: true, lastError: null };

function hasLocalStorage() {
  try { return typeof localStorage !== 'undefined'; } catch { return false; }
}
function readRaw(key) {
  if (hasLocalStorage()) {
    try {
      const value = localStorage.getItem(key);
      if (value != null) memory.set(key, value);
      persistence = { available: true, lastError: null };
      return value ?? memory.get(key) ?? null;
    } catch (error) {
      persistence = { available: false, lastError: String(error?.message || error) };
    }
  } else {
    persistence = { available: false, lastError: 'Persistent browser storage is unavailable.' };
  }
  return memory.get(key) ?? null;
}
function writeRaw(key, value) {
  memory.set(key, value);
  if (hasLocalStorage()) {
    try {
      localStorage.setItem(key, value);
      persistence = { available: true, lastError: null };
      return true;
    } catch (error) {
      persistence = { available: false, lastError: String(error?.message || error) };
      return false;
    }
  }
  persistence = { available: false, lastError: 'Persistent browser storage is unavailable.' };
  return false;
}
function removeRaw(key) {
  memory.delete(key);
  if (hasLocalStorage()) {
    try { localStorage.removeItem(key); }
    catch (error) { persistence = { available: false, lastError: String(error?.message || error) }; }
  }
}
function parse(raw, fallback) {
  if (raw == null) return fallback;
  try { return JSON.parse(raw); } catch { return fallback; }
}
function deepPlanMerge(saved = {}) {
  const base = structuredClone(defaultPlan);
  return {
    ...base,
    ...saved,
    checkIn: { ...base.checkIn, ...(saved.checkIn || {}) },
    feeding: {
      ...base.feeding,
      ...(saved.feeding || {}),
      windows: Array.isArray(saved?.feeding?.windows) ? saved.feeding.windows : base.feeding.windows
    }
  };
}
function notify(detail) {
  try {
    if (typeof window !== 'undefined') window.dispatchEvent(new CustomEvent('scc:local-change', { detail }));
  } catch {}
}
function migrateLegacy() {
  if (!hasLocalStorage()) return;
  try {
    for (const [name, oldKey] of Object.entries(LEGACY_KEYS)) {
      const newKey = KEYS[name];
      if (!newKey || localStorage.getItem(newKey) != null) continue;
      const legacy = localStorage.getItem(oldKey);
      if (legacy != null) localStorage.setItem(newKey, legacy);
    }
  } catch {}
}
migrateLegacy();

export function getPersistenceStatus() { return { ...persistence }; }
export function loadPlan() { const raw = readRaw(KEYS.plan) ?? readRaw(LEGACY_KEYS.plan); return deepPlanMerge(parse(raw, {})); }
export function savePlan(plan, { silent = false } = {}) {
  writeRaw(KEYS.plan, JSON.stringify(plan));
  if (!silent) notify({ kind:'plan', plan: structuredClone(plan) });
}
export function loadEvents() { return parse(readRaw(KEYS.events), []); }
export function saveEvents(events, { silent = false } = {}) {
  writeRaw(KEYS.events, JSON.stringify(events));
  if (!silent) notify({ kind:'events-replaced', events: structuredClone(events) });
}
export function loadChecklist() { return parse(readRaw(KEYS.checklist), {}); }
export function saveChecklist(v, { silent = false } = {}) {
  writeRaw(KEYS.checklist, JSON.stringify(v));
  if (!silent) notify({ kind:'state', field:'checklist' });
}
export function loadNightNo() { return Math.max(1, Math.min(14, Number(readRaw(KEYS.nightNo) || 1))); }
export function saveNightNo(n, { silent = false } = {}) {
  writeRaw(KEYS.nightNo, String(Math.max(1, Math.min(14, Number(n) || 1))));
  if (!silent) notify({ kind:'state', field:'nightNo' });
}
export function loadHistory() { return parse(readRaw(KEYS.history), []); }
export function saveHistory(history, { silent = false } = {}) {
  writeRaw(KEYS.history, JSON.stringify(history));
  if (!silent) notify({ kind:'history', history: structuredClone(history) });
}

export function getDeviceId() {
  let id = readRaw(KEYS.deviceId);
  if (!id) {
    id = uid('device');
    writeRaw(KEYS.deviceId, id);
  }
  return id;
}

export function loadNightId() {
  let id = readRaw(KEYS.nightId);
  if (!id) {
    id = uid('night');
    writeRaw(KEYS.nightId, id);
  }
  return id;
}

export function appendEvent(type, opts = {}) {
  const events = loadEvents();
  const plan = loadPlan();
  const e = makeEvent(type, {
    nightId: loadNightId(),
    planVersionId: planVersionId(plan),
    deviceId: getDeviceId(),
    ...opts
  });
  events.push(e);
  saveEvents(events, { silent:true });
  notify({ kind:'event', event: structuredClone(e) });
  return e;
}

export function appendEvents(defs = [], shared = {}) {
  const events = loadEvents();
  const plan = loadPlan();
  const nightId = loadNightId();
  const pv = planVersionId(plan);
  const recordedAt = shared.recordedAt || new Date().toISOString();
  const deviceId = getDeviceId();
  const created = defs.map(def => makeEvent(def.eventType, {
    nightId,
    planVersionId: pv,
    deviceId,
    recordedAt,
    occurredAt: def.occurredAt || shared.occurredAt || recordedAt,
    ...shared,
    ...def,
    metadata: { ...(shared.metadata || {}), ...(def.metadata || {}) }
  }));
  saveEvents([...events, ...created], { silent:true });
  notify({ kind:'events', events: structuredClone(created) });
  return created;
}

export function deleteEvent(eventId) {
  return appendEvent('EVENT_DELETED', { supersedesEventId: eventId, source: 'CORRECTION' });
}

export function archiveNight(summary, events = loadEvents(), { silent = false } = {}) {
  const history = loadHistory();
  const entry = { ...summary, events: structuredClone(events) };
  const index = history.findIndex(x => x.nightId === summary.nightId);
  if (index >= 0) history[index] = { ...history[index], ...entry, review: history[index].review || entry.review || null };
  else history.push(entry);
  history.sort((a,b) => Number(a.nightNumber) - Number(b.nightNumber) || String(a.completedAt).localeCompare(String(b.completedAt)));
  saveHistory(history, { silent:true });
  if (!silent) notify({ kind:'summary', summary: structuredClone(entry) });
  return entry;
}

export function updateNightReview(nightId, review, { silent = false } = {}) {
  const history = loadHistory();
  const index = history.findIndex(x => x.nightId === nightId);
  if (index < 0) return null;
  history[index] = { ...history[index], review: { ...(history[index].review || {}), ...review } };
  saveHistory(history, { silent:true });
  if (!silent) notify({ kind:'summary', summary: structuredClone(history[index]) });
  return history[index];
}

export function prepareNextNight() {
  const current = loadNightNo();
  if (current >= 14) return { advanced: false, nightNo: current };
  saveNightNo(current + 1, { silent:true });
  saveEvents([], { silent:true });
  saveChecklist({}, { silent:true });
  writeRaw(KEYS.nightId, uid('night'));
  notify({ kind:'night-transition', nightId:loadNightId(), nightNo:loadNightNo() });
  return { advanced: true, nightNo: current + 1 };
}

export function resetCycleKeepPlan() {
  saveEvents([], { silent:true });
  saveChecklist({}, { silent:true });
  saveHistory([], { silent:true });
  saveNightNo(1, { silent:true });
  writeRaw(KEYS.nightId, uid('night'));
  notify({ kind:'reset-cycle' });
}

export function resetAll() {
  Object.values(KEYS).filter(k => k !== KEYS.deviceId).forEach(removeRaw);
  notify({ kind:'reset-all' });
}

// Cloud hydration functions are deliberately silent to prevent sync echo loops.
export function applyCloudState({ plan, checklist, nightNo, nightId } = {}) {
  if (plan) savePlan(plan, { silent:true });
  if (checklist) saveChecklist(checklist, { silent:true });
  if (nightNo != null) saveNightNo(nightNo, { silent:true });
  if (nightId) writeRaw(KEYS.nightId, String(nightId));
}

export function mergeCloudEvents(remoteEvents = []) {
  const local = loadEvents();
  const byId = new Map(local.map(e => [e.eventId, e]));
  for (const e of remoteEvents) {
    if (!e?.eventId) continue;
    const existing = byId.get(e.eventId);
    if (!existing || String(e.recordedAt || '') > String(existing.recordedAt || '')) byId.set(e.eventId, e);
  }
  const merged = [...byId.values()].sort((a,b) =>
    String(a.occurredAt).localeCompare(String(b.occurredAt)) || String(a.recordedAt).localeCompare(String(b.recordedAt))
  );
  saveEvents(merged, { silent:true });
  return merged;
}

export function mergeCloudHistory(entries = []) {
  const local = loadHistory();
  const byId = new Map(local.map(x => [x.nightId, x]));
  for (const entry of entries) {
    if (!entry?.nightId) continue;
    const previous = byId.get(entry.nightId) || {};
    byId.set(entry.nightId, { ...previous, ...entry, review: entry.review || previous.review || null });
  }
  const merged = [...byId.values()].sort((a,b) => Number(a.nightNumber) - Number(b.nightNumber));
  saveHistory(merged, { silent:true });
  return merged;
}

export function getLocalSnapshot() {
  return {
    plan: loadPlan(), checklist: loadChecklist(), nightNo: loadNightNo(), nightId: loadNightId(),
    events: loadEvents(), history: loadHistory(), deviceId: getDeviceId()
  };
}