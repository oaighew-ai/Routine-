import { createClient } from 'https://esm.sh/@supabase/supabase-js@2.57.4?bundle';
import {
  applyCloudState, getLocalSnapshot, loadEvents, loadHistory, loadPlan, loadChecklist,
  loadNightId, loadNightNo, mergeCloudEvents, mergeCloudHistory
} from './store.js';

const cfg = globalThis.SCC_CONFIG || {};
const url = cfg.supabaseUrl || '';
const key = cfg.supabaseKey || '';
const apiBase = String(cfg.apiBase || '').replace(/\/$/, '');
const configured = Boolean(url && key);
const supabase = configured ? createClient(url, key, {
  auth: { persistSession:true, autoRefreshToken:true, detectSessionInUrl:true }
}) : null;

const state = {
  configured,
  status: configured ? 'starting' : 'local_only',
  user: null,
  household: null,
  role: null,
  stateVersion: 0,
  pending: 0,
  lastSyncedAt: null,
  error: null,
  conflict: null,
  invitePending: null,
  accessMode: null
};
let channel = null;
let started = false;
let applyingRemote = false;
let queuedStateSync = null;
let listenersInstalled = false;
let authSubscription = null;

async function handleAuthSession(session) {
  if (session?.user) {
    await attachUser(session.user);
    return;
  }

  if (channel) {
    const staleChannel = channel;
    channel = null;
    try {
      await supabase.removeChannel(staleChannel);
    } catch (error) {
      console.warn('[cloud] failed to remove realtime channel', error);
    }
  }

  setState({
    status: 'signed_out',
    user: null,
    household: null,
    membership: null,
    members: [],
    conflict: null,
    lastSyncedAt: null,
  });
}

function installBaseListeners() {
  if (listenersInstalled) return;
  listenersInstalled = true;

  window.addEventListener('scc:local-change', onLocalChange);

  window.addEventListener('online', () => {
    if (state.user) {
      attachUser(state.user).catch(reportError);
    } else if (!started) {
      startCloudSync().catch(reportError);
    }
  });
}
function emit() {
  try { window.dispatchEvent(new CustomEvent('scc:cloud-state', { detail:getCloudState() })); } catch {}
}
function setState(patch) { Object.assign(state, patch); emit(); }
function errorText(error) { return String(error?.message || error?.details || error || 'Unknown sync error'); }
function sleep(ms) { return new Promise(r => setTimeout(r, ms)); }
function randomToken() {
  const bytes = new Uint8Array(32);
  crypto.getRandomValues(bytes);
  let binary = '';
  for (const b of bytes) binary += String.fromCharCode(b);
  return btoa(binary).replaceAll('+','-').replaceAll('/','_').replaceAll('=','');
}
function extractAccess() {
  try {
    const u = new URL(window.location.href);
    const setup = u.searchParams.get('setup');
    const invite = u.searchParams.get('invite');
    // Keep one-time access params in the URL until setup/join actually succeeds.
    // This makes refreshes and Safari tab restores resilient.
    if (setup) sessionStorage.setItem('scc.setup.pending', setup);
    if (invite) sessionStorage.setItem('scc.invite.pending', invite);
    const setupPending = setup || sessionStorage.getItem('scc.setup.pending');
    const invitePending = invite || sessionStorage.getItem('scc.invite.pending');
    const mode = setupPending ? 'setup' : (invitePending ? 'invite' : null);
    state.accessMode = mode;
    return { setup:setupPending, invite:invitePending, mode };
  } catch { return { setup:null, invite:null, mode:null }; }
}

function clearAccessParam(kind) {
  try {
    sessionStorage.removeItem(kind === 'setup' ? 'scc.setup.pending' : 'scc.invite.pending');
    const u = new URL(window.location.href);
    u.searchParams.delete(kind);
    history.replaceState({}, '', `${u.pathname}${u.search}${u.hash}`);
  } catch {}
  state.accessMode = null;
}

function eventToRow(event) {
  return {
    household_id: state.household.id,
    event_id: event.eventId,
    night_id: event.nightId,
    occurred_at: event.occurredAt,
    recorded_at: event.recordedAt,
    event_type: event.eventType,
    baby_state: event.babyState || null,
    caregiver_action: event.caregiverAction || null,
    actor_user_id: state.user.id,
    device_id: event.deviceId || getLocalSnapshot().deviceId,
    source: event.source || 'USER_TAP',
    plan_version_id: event.planVersionId || 'unknown',
    supersedes_event_id: event.supersedesEventId || null,
    metadata: event.metadata || {}
  };
}
function rowToEvent(row) {
  return {
    eventId: row.event_id,
    nightId: row.night_id,
    occurredAt: row.occurred_at,
    recordedAt: row.recorded_at,
    eventType: row.event_type,
    babyState: row.baby_state || undefined,
    caregiverAction: row.caregiver_action || undefined,
    actorId: row.actor_user_id,
    deviceId: row.device_id,
    source: row.source,
    planVersionId: row.plan_version_id,
    supersedesEventId: row.supersedes_event_id || undefined,
    metadata: row.metadata || {}
  };
}
function summaryToRow(summary) {
  const clean = structuredClone(summary);
  delete clean.events;
  return {
    household_id: state.household.id,
    night_id: summary.nightId,
    night_number: Number(summary.nightNumber || 1),
    summary: clean,
    updated_by: state.user.id,
    updated_at: new Date().toISOString()
  };
}
function rowToSummary(row) {
  return { ...(row.summary || {}), nightId:row.night_id, nightNumber:row.night_number };
}

export function getCloudState() { return structuredClone(state); }
export function isCloudConfigured() { return configured; }
export function getSupabaseClient() { return supabase; }

export async function startCloudSync() {
  if (!configured || started) return getCloudState();

  started = true;
  extractAccess();
  installBaseListeners();

  if (!authSubscription) {
    const { data } = supabase.auth.onAuthStateChange((event, session2) => {
      if (event === 'INITIAL_SESSION') return;

      setTimeout(() => {
        handleAuthSession(session2).catch(reportError);
      }, 0);
    });

    authSubscription = data?.subscription ?? null;
  }

  try {
    const {
      data: { session },
      error,
    } = await supabase.auth.getSession();

    if (error) throw error;

    await handleAuthSession(session);

    return getCloudState();
  } catch (error) {
    started = false;

    setState({
      status: navigator.onLine ? 'signed_out' : 'offline_cached',
      error: errorText(error),
    });

    throw error;
  }
}
export async function startCloudSync() {
  if (!configured || started) return getCloudState();
  started = true;
  extractAccess();
  window.addEventListener('scc:local-change', onLocalChange);
  window.addEventListener('online', () => { if (state.user) attachUser(state.user).catch(reportError); });

  const { data:{ session }, error } = await supabase.auth.getSession();
  if (error) reportError(error);
  if (session?.user) await attachUser(session.user);
  else setState({ status:'signed_out', user:null, household:null, role:null, error:null });

  supabase.auth.onAuthStateChange(async (_event, session2) => {
    if (session2?.user) await attachUser(session2.user);
    else {
      if (channel) { await supabase.removeChannel(channel); channel = null; }
      setState({ status:'signed_out', user:null, household:null, role:null, stateVersion:0, conflict:null });
    }
  });
  return getCloudState();
}

export async function signInPassword(email, password) {
  if (!configured) throw new Error('Cloud sync is not configured.');
  const clean = String(email || '').trim().toLowerCase();
  const pass = String(password || '');
  if (!/^\S+@\S+\.\S+$/.test(clean)) throw new Error('Enter a valid email address.');
  if (pass.length < 12) throw new Error('Password must be at least 12 characters.');
  setState({ status:'auth_sending', error:null });
  const { error } = await supabase.auth.signInWithPassword({ email:clean, password:pass });
  if (error) {
    const message = errorText(error);
    setState({ status:'signed_out', error:message });
    throw new Error(message);
  }
  setState({ error:null });
}

async function accessSignup(path, payload) {
  if (!apiBase) throw new Error('Account setup endpoint is not configured.');
  const res = await fetch(`${apiBase}${path}`, {
    method:'POST',
    headers:{ 'content-type':'application/json' },
    body:JSON.stringify(payload)
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data?.error || `Account setup failed (${res.status}).`);
  return data;
}

export async function signUpAccess(email, password, familyName = '') {
  if (!configured) throw new Error('Cloud sync is not configured.');
  const clean = String(email || '').trim().toLowerCase();
  const pass = String(password || '');
  if (!/^\S+@\S+\.\S+$/.test(clean)) throw new Error('Enter a valid email address.');
  if (pass.length < 12) throw new Error('Password must be at least 12 characters.');
  const access = extractAccess();
  const setup = access.setup;
  const invite = access.invite;
  if (!setup && !invite) throw new Error('This link does not contain an active setup or caregiver invite.');
  setState({ status:'auth_sending', error:null });
  try {
    if (setup) {
      const family = String(familyName || '').trim();
      if (!family) throw new Error('Enter a family name.');
      await accessSignup('/api/bootstrap-signup', { token:setup, email:clean, password:pass, familyName:family });
      clearAccessParam('setup');
    } else {
      await accessSignup('/api/invite-signup', { inviteToken:invite, email:clean, password:pass });
      clearAccessParam('invite');
    }
    const { error } = await supabase.auth.signInWithPassword({ email:clean, password:pass });
    if (error) throw error;
    setState({ error:null });
  } catch (error) {
    const message = errorText(error);
    state.accessMode = setup ? 'setup' : 'invite';
    setState({ status:'signed_out', error:message });
    throw new Error(message);
  }
}

export async function signOutCloud() {
  if (!supabase) return;
  const { error } = await supabase.auth.signOut();
  if (error) throw error;
}

async function loadMembership() {
  const { data, error } = await supabase
    .from('household_members')
    .select('household_id,role,households!inner(id,name)')
    .eq('user_id', state.user.id)
    .limit(1);
  if (error) throw error;
  const row = data?.[0];
  if (!row) return null;
  const h = Array.isArray(row.households) ? row.households[0] : row.households;
  return { household:{ id:row.household_id, name:h?.name || 'Family' }, role:row.role };
}

async function attachUser(user) {
  if (!navigator.onLine) {
    setState({ user, status:'offline_cached', error:null });
    return;
  }
  setState({ user, status:'loading_family', error:null });
  const pendingInvite = sessionStorage.getItem('scc.invite.pending');
  if (pendingInvite) {
    const { error } = await supabase.rpc('join_household_with_invite', { p_token:pendingInvite });
    if (!error) { sessionStorage.removeItem('scc.invite.pending'); state.accessMode = null; }
    else if (!/already|duplicate/i.test(errorText(error))) {
      sessionStorage.removeItem('scc.invite.pending');
      setState({ invitePending:null, error:'That family invite is invalid or expired.' });
    }
  }
  const membership = await loadMembership();
  if (!membership) {
    setState({ status:'needs_household', household:null, role:null, stateVersion:0 });
    return;
  }
  setState({ household:membership.household, role:membership.role, status:'syncing', conflict:null });
  await bootstrapHousehold();
  await subscribeRealtime();
  setState({ status:'ready', lastSyncedAt:new Date().toISOString(), error:null });
}

export async function createHousehold(name) {
  if (!state.user) throw new Error('Sign in first.');
  const clean = String(name || '').trim();
  if (!clean) throw new Error('Enter a family name.');
  const { error } = await supabase.rpc('create_household', { p_name:clean });
  if (error) throw error;
  await attachUser(state.user);
}

export async function createFamilyInvite() {
  if (!state.household) throw new Error('No family workspace is connected.');
  if (state.role !== 'owner') throw new Error('Only the family owner can create an invite.');
  const token = randomToken();
  const { data, error } = await supabase.rpc('create_household_invite', {
    p_household_id:state.household.id,
    p_token:token,
    p_expires_hours:168
  });
  if (error) throw error;
  const base = new URL(window.location.href);
  base.search = '';
  base.hash = '';
  const link = `${base.toString()}?invite=${encodeURIComponent(token)}`;
  setState({ invitePending:{ link, expiresAt:data } });
  return { link, expiresAt:data };
}

async function bootstrapHousehold() {
  const local = getLocalSnapshot();
  const { data:remoteState, error:stateError } = await supabase
    .from('household_state').select('*').eq('household_id', state.household.id).maybeSingle();
  if (stateError) throw stateError;

  if (!remoteState) {
    state.stateVersion = 0;
    await pushState(true);
    await pushEvents(local.events);
    await pushHistory(local.history);
    return;
  }

  state.stateVersion = Number(remoteState.state_version || 0);
  const localMeaningful = local.events.length > 0;
  if (localMeaningful && local.nightId !== remoteState.current_night_id) {
    try { localStorage.setItem('scc.conflictBackup.v1', JSON.stringify({ savedAt:new Date().toISOString(), snapshot:local })); } catch {}
    setState({
      status:'conflict',
      conflict:{ localNightId:local.nightId, cloudNightId:remoteState.current_night_id, cloudState:remoteState },
      error:'This device has a different active night than the family workspace. Your local data was backed up on this device.'
    });
    return;
  }

  applyingRemote = true;
  applyCloudState({
    plan:remoteState.plan, checklist:remoteState.checklist,
    nightNo:remoteState.current_night_no, nightId:remoteState.current_night_id
  });
  applyingRemote = false;

  const [{ data:eventRows, error:eventError }, { data:summaryRows, error:summaryError }] = await Promise.all([
    supabase.from('sleep_events').select('*').eq('household_id', state.household.id).eq('night_id', remoteState.current_night_id).order('occurred_at'),
    supabase.from('night_summaries').select('*').eq('household_id', state.household.id).order('night_number')
  ]);
  if (eventError) throw eventError;
  if (summaryError) throw summaryError;
  mergeCloudEvents((eventRows || []).map(rowToEvent));
  mergeCloudHistory((summaryRows || []).map(rowToSummary));

  // Push any local event IDs that were not already in the cloud after merge.
  await pushEvents(loadEvents());
  await pushHistory(loadHistory());
}

export async function acceptCloudConflict() {
  if (!state.conflict?.cloudState) return;
  const remoteState = state.conflict.cloudState;
  applyingRemote = true;
  applyCloudState({ plan:remoteState.plan, checklist:remoteState.checklist, nightNo:remoteState.current_night_no, nightId:remoteState.current_night_id });
  applyingRemote = false;
  state.stateVersion = Number(remoteState.state_version || 0);
  setState({ status:'syncing', conflict:null, error:null });
  await bootstrapHousehold();
  await subscribeRealtime();
  setState({ status:'ready', lastSyncedAt:new Date().toISOString() });
}

async function pushEvent(event) {
  if (!readyForWrite()) return;
  state.pending += 1; emit();
  try {
    const { error } = await supabase.from('sleep_events').upsert(eventToRow(event), {
      onConflict:'household_id,event_id', ignoreDuplicates:true
    });
    if (error) throw error;
    setState({ lastSyncedAt:new Date().toISOString(), error:null });
  } finally { state.pending = Math.max(0,state.pending-1); emit(); }
}
async function pushEvents(events = []) {
  if (!readyForWrite() || !events.length) return;
  const rows = events.map(eventToRow);
  const { error } = await supabase.from('sleep_events').upsert(rows, {
    onConflict:'household_id,event_id', ignoreDuplicates:true
  });
  if (error) throw error;
}
async function pushSummary(summary) {
  if (!readyForWrite() || !summary?.nightId) return;
  const { error } = await supabase.from('night_summaries').upsert(summaryToRow(summary), { onConflict:'household_id,night_id' });
  if (error) throw error;
}
async function pushHistory(history = []) {
  if (!readyForWrite()) return;
  for (const summary of history) await pushSummary(summary);
}

async function pushState(initial = false) {
  if (!readyForWrite()) return;
  if (queuedStateSync) return queuedStateSync;
  queuedStateSync = (async () => {
    await sleep(80);
    const local = getLocalSnapshot();
    const { data, error } = await supabase.rpc('save_household_state', {
      p_household_id:state.household.id,
      p_plan:local.plan,
      p_checklist:local.checklist,
      p_current_night_id:local.nightId,
      p_current_night_no:local.nightNo,
      p_expected_version:initial ? 0 : state.stateVersion
    });
    if (error) {
      if (/state_version_conflict/i.test(errorText(error))) {
        const { data:latest, error:latestError } = await supabase.from('household_state').select('*').eq('household_id',state.household.id).single();
        if (latestError) throw latestError;
        if (latest.current_night_id !== local.nightId && local.events.length) {
          setState({ status:'conflict', conflict:{localNightId:local.nightId,cloudNightId:latest.current_night_id,cloudState:latest}, error:'Family sync detected two different active nights.' });
          return;
        }
        state.stateVersion = Number(latest.state_version || 0);
        // Retry once. Local plan/checklist are intentional user edits; night identity must match.
        const retry = await supabase.rpc('save_household_state', {
          p_household_id:state.household.id,
          p_plan:local.plan,
          p_checklist:local.checklist,
          p_current_night_id:local.nightId,
          p_current_night_no:local.nightNo,
          p_expected_version:state.stateVersion
        });
        if (retry.error) throw retry.error;
        state.stateVersion = Number(retry.data?.state_version || state.stateVersion + 1);
      } else throw error;
    } else state.stateVersion = Number(data?.state_version || state.stateVersion + 1);
    setState({ lastSyncedAt:new Date().toISOString(), error:null });
  })().finally(() => { queuedStateSync = null; });
  return queuedStateSync;
}

function readyForWrite() {
  return Boolean(configured && state.user && state.household && navigator.onLine && state.status !== 'conflict');
}

async function onLocalChange(event) {
  if (applyingRemote || !readyForWrite()) return;
  try {
    const detail = event.detail || {};
    if (detail.kind === 'event') await pushEvent(detail.event);
    else if (detail.kind === 'events') await pushEvents(detail.events || []);
    else if (detail.kind === 'summary') await pushSummary(detail.summary);
    else if (detail.kind === 'history') await pushHistory(detail.history || []);
    else await pushState(false);
  } catch (error) { reportError(error); }
}

async function subscribeRealtime() {
  if (!state.household) return;
  if (channel) await supabase.removeChannel(channel);
  channel = supabase.channel(`scc:${state.household.id}`)
    .on('postgres_changes', { event:'INSERT', schema:'public', table:'sleep_events', filter:`household_id=eq.${state.household.id}` }, payload => {
      applyingRemote = true; mergeCloudEvents([rowToEvent(payload.new)]); applyingRemote = false;
      setState({ lastSyncedAt:new Date().toISOString() });
    })
    .on('postgres_changes', { event:'UPDATE', schema:'public', table:'household_state', filter:`household_id=eq.${state.household.id}` }, payload => {
      const r = payload.new;
      state.stateVersion = Number(r.state_version || state.stateVersion);
      const local = getLocalSnapshot();
      if (local.nightId !== r.current_night_id && local.events.length) {
        setState({ status:'conflict', conflict:{localNightId:local.nightId,cloudNightId:r.current_night_id,cloudState:r}, error:'Another device advanced the family night while this device still has local events.' });
        return;
      }
      applyingRemote = true;
      applyCloudState({ plan:r.plan, checklist:r.checklist, nightNo:r.current_night_no, nightId:r.current_night_id });
      applyingRemote = false;
      setState({ lastSyncedAt:new Date().toISOString() });
    })
    .on('postgres_changes', { event:'*', schema:'public', table:'night_summaries', filter:`household_id=eq.${state.household.id}` }, payload => {
      if (!payload.new?.night_id) return;
      applyingRemote = true; mergeCloudHistory([rowToSummary(payload.new)]); applyingRemote = false;
      setState({ lastSyncedAt:new Date().toISOString() });
    })
    .subscribe(status => {
      if (status === 'SUBSCRIBED' && state.status !== 'conflict') setState({ status:'ready', error:null });
      if (status === 'CHANNEL_ERROR') setState({ error:'Realtime family sync lost connection. Local logging is still active.' });
    });
}

function reportError(error) {
  setState({ error:errorText(error), status: navigator.onLine ? state.status : 'offline_cached' });
}