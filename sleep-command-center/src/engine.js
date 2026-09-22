export const SessionState = Object.freeze({
  PREP: 'PREP', ACTIVE: 'ACTIVE', CARE_SUSPENDED: 'CARE_SUSPENDED', COMPLETE: 'COMPLETE'
});
export const BabyState = Object.freeze({
  UNKNOWN: 'UNKNOWN', ASLEEP: 'ASLEEP', AWAKE_CALM: 'AWAKE_CALM', FUSSING: 'FUSSING', CRYING: 'CRYING'
});
export const CaregiverAction = Object.freeze({
  NONE: 'NONE', CHECK_IN: 'CHECK_IN', FEEDING: 'FEEDING', DIAPER: 'DIAPER', COMFORTING: 'COMFORTING', OTHER_CARE: 'OTHER_CARE'
});
export const SafetyState = Object.freeze({ CLEAR: 'CLEAR', CARE_CONCERN: 'CARE_CONCERN' });
export const NightContext = Object.freeze({
  BEDTIME_ONSET: 'BEDTIME_ONSET', ROUTINE_WAKING: 'ROUTINE_WAKING', FEED_ELIGIBLE: 'FEED_ELIGIBLE', EARLY_MORNING: 'EARLY_MORNING', MORNING: 'MORNING'
});

export const defaultPlan = Object.freeze({
  id: 'plan-default', version: 1, babyName: 'Baby', mode: 'GUIDE_TRACK',
  bedtimeTarget: '19:30', morningStart: '06:30', earlyMorningWindowMinutes: 120,
  lastNapEnded: '', finalWakeWindowMinutes: 150, lastFeedTime: '', caregiver: 'Parent',
  checkIn: {
    strategy: 'TIMED_CHECKINS', intervalsMinutes: [3, 5, 10],
    afterFinalInterval: 'REPEAT_FINAL', calmBehavior: 'RESET_INTERVAL'
  },
  feeding: {
    mode: 'ONE_PLANNED_FEED',
    windows: [{ id: 'feed-1', start: '01:00', end: '03:00', feedOnWaking: true, wakeToFeed: false }],
    specialInstructions: '', approvedChangesAcknowledged: false
  },
  specialInstructions: ''
});

export const BEHAVIOR_EVENTS = new Set(['ASLEEP', 'AWAKE_CALM', 'FUSSING', 'CRYING']);
const CARE_START = {
  CHECKIN_STARTED: CaregiverAction.CHECK_IN,
  FEED_STARTED: CaregiverAction.FEEDING,
  DIAPER_STARTED: CaregiverAction.DIAPER,
  COMFORT_STARTED: CaregiverAction.COMFORTING,
  OTHER_CARE_STARTED: CaregiverAction.OTHER_CARE
};
const CARE_END_FOR = {
  CHECKIN_ENDED: CaregiverAction.CHECK_IN,
  FEED_ENDED: CaregiverAction.FEEDING,
  DIAPER_ENDED: CaregiverAction.DIAPER,
  COMFORT_ENDED: CaregiverAction.COMFORTING,
  OTHER_CARE_ENDED: CaregiverAction.OTHER_CARE
};
const CORRECTION_EVENTS = new Set(['EVENT_CORRECTED', 'EVENT_DELETED']);

export function uid(prefix = 'evt') {
  if (globalThis.crypto?.randomUUID) return `${prefix}-${globalThis.crypto.randomUUID()}`;
  return `${prefix}-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 10)}`;
}

export function planVersionId(plan = defaultPlan) {
  return `${plan.id || 'plan'}-v${plan.version || 1}`;
}

export function makeEvent(eventType, opts = {}) {
  const recordedAt = opts.recordedAt || new Date().toISOString();
  return {
    eventId: opts.eventId || uid(),
    nightId: opts.nightId || 'night-current',
    occurredAt: opts.occurredAt || recordedAt,
    recordedAt,
    eventType,
    babyState: opts.babyState,
    caregiverAction: opts.caregiverAction,
    actorId: opts.actorId || 'local-parent',
    deviceId: opts.deviceId || 'local-device',
    source: opts.source || 'USER_TAP',
    planVersionId: opts.planVersionId || planVersionId(defaultPlan),
    supersedesEventId: opts.supersedesEventId,
    metadata: opts.metadata || {}
  };
}

export function normalizeEvents(events = []) {
  const superseded = new Set();
  for (const e of events) if (e.supersedesEventId) superseded.add(e.supersedesEventId);
  return events
    .filter(e => !superseded.has(e.eventId) && e.eventType !== 'EVENT_DELETED')
    .slice()
    .sort((a, b) => new Date(a.occurredAt) - new Date(b.occurredAt) || new Date(a.recordedAt) - new Date(b.recordedAt));
}

export function reduceNight(events = []) {
  const normalized = normalizeEvents(events);
  const state = {
    session: SessionState.PREP,
    babyState: BabyState.UNKNOWN,
    caregiverAction: CaregiverAction.NONE,
    safetyState: SafetyState.CLEAR,
    nightStartedAt: null,
    nightCompletedAt: null,
    cribPlacedAt: null,
    currentStateStartedAt: null,
    currentCryStartedAt: null,
    currentCareStartedAt: null,
    firstAsleepAt: null,
    lastAsleepAt: null,
    lastWakeAt: null,
    lastFeedStartedAt: null,
    lastFeedEndedAt: null,
    currentWakingStartedAt: null,
    checkInsThisWaking: 0,
    interruptedCaregiverAction: CaregiverAction.NONE,
    latestEvent: normalized.at(-1) || null
  };

  for (const e of normalized) {
    const t = e.occurredAt;

    if (e.eventType === 'NIGHT_STARTED') {
      state.session = SessionState.ACTIVE;
      state.nightStartedAt = t;
      continue;
    }
    if (e.eventType === 'NIGHT_COMPLETED') {
      state.session = SessionState.COMPLETE;
      state.nightCompletedAt = t;
      state.currentCryStartedAt = null;
      state.currentCareStartedAt = null;
      state.caregiverAction = CaregiverAction.NONE;
      continue;
    }
    if (e.eventType === 'PLACED_IN_CRIB') {
      state.cribPlacedAt = t;
      continue;
    }
    if (e.eventType === 'CARE_CONCERN_STARTED') {
      state.interruptedCaregiverAction = state.caregiverAction;
      state.safetyState = SafetyState.CARE_CONCERN;
      state.session = SessionState.CARE_SUSPENDED;
      state.babyState = BabyState.UNKNOWN;
      state.currentStateStartedAt = t;
      state.currentCryStartedAt = null;
      state.caregiverAction = CaregiverAction.NONE;
      state.currentCareStartedAt = null;
      state.checkInsThisWaking = 0;
      continue;
    }
    if (e.eventType === 'CARE_CONCERN_RESOLVED') {
      state.safetyState = SafetyState.CLEAR;
      if (state.session !== SessionState.COMPLETE) state.session = SessionState.ACTIVE;
      state.babyState = BabyState.UNKNOWN;
      state.currentStateStartedAt = t;
      state.currentCryStartedAt = null;
      state.caregiverAction = CaregiverAction.NONE;
      state.currentCareStartedAt = null;
      state.checkInsThisWaking = 0;
      state.interruptedCaregiverAction = CaregiverAction.NONE;
      continue;
    }

    if (BEHAVIOR_EVENTS.has(e.eventType)) {
      const previous = state.babyState;
      state.babyState = e.eventType;
      state.currentStateStartedAt = t;
      state.currentCryStartedAt = e.eventType === BabyState.CRYING ? t : null;

      if (e.eventType !== BabyState.ASLEEP && state.firstAsleepAt && !state.currentWakingStartedAt) {
        state.lastWakeAt = t;
        state.currentWakingStartedAt = t;
        state.checkInsThisWaking = 0;
      } else if (previous === BabyState.ASLEEP && e.eventType !== BabyState.ASLEEP) {
        state.lastWakeAt = t;
        state.currentWakingStartedAt = t;
        state.checkInsThisWaking = 0;
      }

      if (e.eventType === BabyState.ASLEEP) {
        if (!state.firstAsleepAt) state.firstAsleepAt = t;
        state.lastAsleepAt = t;
        state.currentWakingStartedAt = null;
        state.checkInsThisWaking = 0;
      }
      continue;
    }

    if (CARE_START[e.eventType]) {
      // Caregiver interventions suspend observation timers. The baby state becomes
      // unknown until the caregiver explicitly marks what is happening after care.
      state.caregiverAction = CARE_START[e.eventType];
      state.currentCareStartedAt = t;
      state.babyState = BabyState.UNKNOWN;
      state.currentStateStartedAt = t;
      state.currentCryStartedAt = null;
      if (e.eventType === 'CHECKIN_STARTED') state.checkInsThisWaking += 1;
      if (e.eventType === 'FEED_STARTED') state.lastFeedStartedAt = t;
      continue;
    }
    if (CARE_END_FOR[e.eventType]) {
      state.caregiverAction = CaregiverAction.NONE;
      state.currentCareStartedAt = null;
      if (e.eventType === 'FEED_ENDED') state.lastFeedEndedAt = t;
    }
  }
  return state;
}

export function minutesSince(ts, now = new Date()) {
  if (!ts) return null;
  return Math.max(0, (new Date(now) - new Date(ts)) / 60000);
}
export function msSince(ts, now = new Date()) {
  if (!ts) return 0;
  return Math.max(0, new Date(now) - new Date(ts));
}
export function parseClockMinutes(clock = '00:00') {
  const [h, m] = String(clock).split(':').map(Number);
  return (Number.isFinite(h) ? h : 0) * 60 + (Number.isFinite(m) ? m : 0);
}
export function clockMinutes(date) {
  const d = new Date(date);
  return d.getHours() * 60 + d.getMinutes();
}
export function minutesUntilClock(clock, now = new Date()) {
  const target = new Date(now);
  const [h, m] = clock.split(':').map(Number);
  target.setHours(h, m, 0, 0);
  if (target < now) target.setDate(target.getDate() + 1);
  return (target - now) / 60000;
}
export function isInClockWindow(now, start, end) {
  const n = clockMinutes(now), s = parseClockMinutes(start), e = parseClockMinutes(end);
  if (s === e) return true;
  return s < e ? n >= s && n < e : n >= s || n < e;
}

function alreadyFedInWindow(events, window, now) {
  const feeds = normalizeEvents(events).filter(e => e.eventType === 'FEED_STARTED');
  if (!feeds.length) return false;
  const current = new Date(now);
  const anchor = new Date(current);
  const [sh, sm] = window.start.split(':').map(Number);
  anchor.setHours(sh, sm, 0, 0);
  if (parseClockMinutes(window.start) > parseClockMinutes(window.end) && clockMinutes(current) < parseClockMinutes(window.end)) {
    anchor.setDate(anchor.getDate() - 1);
  }
  const end = new Date(anchor);
  const [eh, em] = window.end.split(':').map(Number);
  end.setHours(eh, em, 0, 0);
  if (end <= anchor) end.setDate(end.getDate() + 1);
  return feeds.some(f => new Date(f.occurredAt) >= anchor && new Date(f.occurredAt) < end);
}

export function getEligibleFeedWindow(plan, events, now) {
  if (!plan?.feeding || plan.feeding.mode === 'NONE') return null;
  const windows = plan.feeding.windows || [];
  return windows.find(w => w.feedOnWaking && isInClockWindow(now, w.start, w.end) && !alreadyFedInWindow(events, w, now)) || null;
}
export function getWakeToFeedWindow(plan, events, now) {
  if (!plan?.feeding || plan.feeding.mode === 'NONE') return null;
  const windows = plan.feeding.windows || [];
  return windows.find(w => w.wakeToFeed && isInClockWindow(now, w.start, w.end) && !alreadyFedInWindow(events, w, now)) || null;
}

export function morningBoundary(plan, now, nightStartedAt = null) {
  const ref = nightStartedAt ? new Date(nightStartedAt) : new Date(now);
  const boundary = new Date(ref);
  const [h, m] = plan.morningStart.split(':').map(Number);
  boundary.setHours(h, m, 0, 0);
  if (nightStartedAt && boundary <= ref) boundary.setDate(boundary.getDate() + 1);
  return boundary;
}
export function hasMorningStarted(plan, now, nightStartedAt = null) {
  if (!nightStartedAt) return clockMinutes(now) >= parseClockMinutes(plan.morningStart);
  return new Date(now) >= morningBoundary(plan, now, nightStartedAt);
}
export function isEarlyMorning(plan, now, nightStartedAt = null) {
  if (!nightStartedAt) {
    if (hasMorningStarted(plan, now)) return false;
    const morning = parseClockMinutes(plan.morningStart);
    const nowMin = clockMinutes(now);
    const delta = morning >= nowMin ? morning - nowMin : morning + 1440 - nowMin;
    return delta <= (plan.earlyMorningWindowMinutes ?? 120);
  }
  const boundary = morningBoundary(plan, now, nightStartedAt);
  if (new Date(now) >= boundary) return false;
  const delta = (boundary - new Date(now)) / 60000;
  return delta <= (plan.earlyMorningWindowMinutes ?? 120);
}

export function classifyContext({ state, plan, events, now }) {
  if (!state.firstAsleepAt) return NightContext.BEDTIME_ONSET;
  if ([BabyState.AWAKE_CALM, BabyState.FUSSING, BabyState.CRYING].includes(state.babyState)) {
    if (getEligibleFeedWindow(plan, events, now)) return NightContext.FEED_ELIGIBLE;
    if (hasMorningStarted(plan, now, state.nightStartedAt)) return NightContext.MORNING;
    if (isEarlyMorning(plan, now, state.nightStartedAt)) return NightContext.EARLY_MORNING;
  }
  return NightContext.ROUTINE_WAKING;
}

export function nextCheckInAt({ state, plan }) {
  if (!state.currentCryStartedAt || plan?.checkIn?.strategy !== 'TIMED_CHECKINS') return null;
  const intervals = plan.checkIn.intervalsMinutes || [];
  if (!intervals.length) return null;
  const i = Math.max(0, state.checkInsThisWaking);
  const interval = i < intervals.length ? intervals[i] : (plan.checkIn.afterFinalInterval === 'REPEAT_FINAL' ? intervals.at(-1) : null);
  if (interval == null) return null;
  return new Date(new Date(state.currentCryStartedAt).getTime() + interval * 60000).toISOString();
}

function output(primaryAction, headline, instruction, extras = {}) {
  return {
    primaryAction, headline, instruction,
    timerType: null, timerStartedAt: null, nextActionAt: null,
    reasonCodes: [], allowedActions: [], trainingTimersActive: true, safetyOverrideActive: false,
    ...extras
  };
}

export function decide({ now = new Date(), events = [], plan = defaultPlan }) {
  const state = reduceNight(events);
  const context = classifyContext({ state, plan, events, now });
  const trackingOnly = plan.mode === 'TRACK_ONLY';

  if (state.safetyState === SafetyState.CARE_CONCERN) {
    return output('GO_IN', 'GO IN', 'Check baby whenever something seems wrong.', {
      context, state, trainingTimersActive: false, safetyOverrideActive: true,
      reasonCodes: ['CARE_CONCERN'], allowedActions: ['CARE_CONCERN_RESOLVED']
    });
  }

  if (state.session === SessionState.PREP) {
    return output('BEGIN_NIGHT', 'READY WHEN YOU ARE', 'Complete tonight’s plan, then place baby in the crib and begin.', {
      context, state, trainingTimersActive: false, reasonCodes: ['SESSION_NOT_STARTED'], allowedActions: ['NIGHT_STARTED']
    });
  }
  if (state.session === SessionState.COMPLETE) {
    return output('REVIEW', 'NIGHT COMPLETE', 'Review the night and record how it felt.', {
      context, state, trainingTimersActive: false, reasonCodes: ['SESSION_COMPLETE'], allowedActions: []
    });
  }

  if (state.caregiverAction === CaregiverAction.CHECK_IN) {
    return output('LEAVE_ROOM', 'CHECK-IN', 'Keep it brief and calm. Leave while baby is awake when you are ready.', {
      context, state, timerType: 'CHECKIN', timerStartedAt: state.currentCareStartedAt,
      reasonCodes: ['CHECKIN_ACTIVE'], allowedActions: ['CHECKIN_ENDED', 'CARE_CONCERN_STARTED']
    });
  }
  if (state.caregiverAction === CaregiverAction.FEEDING) {
    return output('RETURN_TO_CRIB', 'NIGHT FEED', 'Keep the room dark and interaction quiet. Return to the crib when the feed is complete.', {
      context, state, timerType: 'FEED', timerStartedAt: state.currentCareStartedAt,
      reasonCodes: ['FEED_ACTIVE'], allowedActions: ['FEED_ENDED', 'CARE_CONCERN_STARTED']
    });
  }
  if (state.caregiverAction !== CaregiverAction.NONE) {
    const endAction = {
      [CaregiverAction.DIAPER]: 'DIAPER_ENDED',
      [CaregiverAction.COMFORTING]: 'COMFORT_ENDED',
      [CaregiverAction.OTHER_CARE]: 'OTHER_CARE_ENDED'
    }[state.caregiverAction];
    return output('COMPLETE_CARE', 'CARE IN PROGRESS', 'Finish the care you chose. Resume the night plan when ready.', {
      context, state, timerType: 'CARE', timerStartedAt: state.currentCareStartedAt,
      reasonCodes: ['CAREGIVING_ACTIVE'], allowedActions: [endAction, 'CARE_CONCERN_STARTED'].filter(Boolean)
    });
  }

  if (trackingOnly) {
    if (hasMorningStarted(plan, now, state.nightStartedAt) && state.babyState !== BabyState.ASLEEP && state.babyState !== BabyState.UNKNOWN) {
      return output('START_DAY', 'GOOD MORNING', 'The morning boundary has arrived. End the night record when you are ready.', {
        context: NightContext.MORNING, state, timerType: 'STATE', timerStartedAt: state.currentStateStartedAt,
        reasonCodes: ['TRACK_ONLY_MODE','MORNING_BOUNDARY_REACHED'], allowedActions: ['NIGHT_COMPLETED','CARE_CONCERN_STARTED']
      });
    }
    return output('OBSERVE', state.babyState === BabyState.ASLEEP ? 'ASLEEP' : 'TRACK ONLY', 'Record what happens. Protocol guidance is paused for tonight.', {
      context, state, timerType: 'STATE', timerStartedAt: state.currentStateStartedAt,
      reasonCodes: ['TRACK_ONLY_MODE'], allowedActions: ['ASLEEP','AWAKE_CALM','FUSSING','CRYING','FEED_STARTED','CARE_CONCERN_STARTED','NIGHT_COMPLETED']
    });
  }

  const wakeToFeedWindow = state.babyState === BabyState.ASLEEP ? getWakeToFeedWindow(plan, events, now) : null;
  if (wakeToFeedWindow) {
    return output('FEED', 'SCHEDULED FEED', 'Tonight’s saved plan says to wake for a feed in this window.', {
      context: NightContext.FEED_ELIGIBLE, state, timerType: 'STATE', timerStartedAt: state.currentStateStartedAt,
      reasonCodes: ['WAKE_TO_FEED_WINDOW','PARENT_PLAN_WAKE_TO_FEED'], allowedActions: ['FEED_STARTED','CARE_CONCERN_STARTED']
    });
  }

  const feedWindow = [BabyState.AWAKE_CALM, BabyState.FUSSING, BabyState.CRYING].includes(state.babyState) ? getEligibleFeedWindow(plan, events, now) : null;
  if (feedWindow) {
    return output('FEED', 'FEED', 'This waking falls within tonight’s planned feeding window.', {
      context: NightContext.FEED_ELIGIBLE, state, timerType: 'STATE', timerStartedAt: state.currentStateStartedAt,
      reasonCodes: ['FEED_WINDOW_ELIGIBLE','PARENT_PLAN_FEED_ON_WAKING'], allowedActions: ['FEED_STARTED','ASLEEP','CARE_CONCERN_STARTED']
    });
  }

  if (hasMorningStarted(plan, now, state.nightStartedAt) && state.babyState !== BabyState.ASLEEP && state.babyState !== BabyState.UNKNOWN) {
    return output('START_DAY', 'GOOD MORNING', 'Tonight’s morning boundary has arrived. Start the day when you are ready.', {
      context: NightContext.MORNING, state, timerType: 'STATE', timerStartedAt: state.currentStateStartedAt,
      reasonCodes: ['MORNING_BOUNDARY_REACHED','BABY_AWAKE'], allowedActions: ['NIGHT_COMPLETED','CARE_CONCERN_STARTED']
    });
  }

  if (state.babyState === BabyState.ASLEEP) {
    return output('NONE', 'ASLEEP', 'No action needed.', {
      context, state, timerType: 'STATE', timerStartedAt: state.currentStateStartedAt,
      reasonCodes: ['BABY_ASLEEP'], allowedActions: ['AWAKE_CALM','FUSSING','CRYING','CARE_CONCERN_STARTED']
    });
  }

  if (state.babyState === BabyState.CRYING) {
    const next = nextCheckInAt({ state, plan });
    if (plan.checkIn.strategy === 'TIMED_CHECKINS' && next && new Date(now) >= new Date(next)) {
      return output('CHECK_IN', 'CHECK IN', 'A brief check-in is available now. Keep it calm, brief, and low stimulation.', {
        context, state, timerType: 'STATE', timerStartedAt: state.currentCryStartedAt, nextActionAt: next,
        reasonCodes: ['BABY_CRYING','CHECKIN_INTERVAL_REACHED'], allowedActions: ['CHECKIN_STARTED','AWAKE_CALM','FUSSING','ASLEEP','FEED_STARTED','CARE_CONCERN_STARTED']
      });
    }
    return output('WAIT', 'WAIT + OBSERVE', isEarlyMorning(plan, now, state.nightStartedAt) ? 'It is still nighttime. Give baby an opportunity to resettle.' : 'Give baby an opportunity to resettle.', {
      context, state, timerType: 'STATE', timerStartedAt: state.currentCryStartedAt, nextActionAt: next,
      reasonCodes: ['BABY_CRYING', next ? 'CHECKIN_NOT_YET_AVAILABLE' : 'NO_TIMED_CHECKIN'], allowedActions: ['CHECKIN_STARTED','AWAKE_CALM','FUSSING','ASLEEP','FEED_STARTED','CARE_CONCERN_STARTED']
    });
  }

  if (state.babyState === BabyState.FUSSING) {
    return output('OBSERVE', 'PAUSE + OBSERVE', 'Give baby an opportunity to settle before intervening.', {
      context, state, timerType: 'STATE', timerStartedAt: state.currentStateStartedAt,
      reasonCodes: ['BABY_FUSSING'], allowedActions: ['CRYING','AWAKE_CALM','ASLEEP','FEED_STARTED','CARE_CONCERN_STARTED']
    });
  }

  if (state.babyState === BabyState.AWAKE_CALM) {
    const early = isEarlyMorning(plan, now, state.nightStartedAt);
    return output('OBSERVE', early ? 'STILL NIGHTTIME' : 'PAUSE + OBSERVE', early ? 'Keep the room dark and use tonight’s response plan.' : 'Baby is awake and calm. Give space to resettle.', {
      context, state, timerType: 'STATE', timerStartedAt: state.currentStateStartedAt,
      reasonCodes: ['BABY_AWAKE_CALM'], allowedActions: ['FUSSING','CRYING','ASLEEP','FEED_STARTED','CARE_CONCERN_STARTED']
    });
  }

  return output('MARK_STATE', 'WHAT IS HAPPENING?', 'Mark the current state so the guide can apply tonight’s plan.', {
    context, state, reasonCodes: ['BABY_STATE_UNKNOWN'], allowedActions: ['ASLEEP','AWAKE_CALM','FUSSING','CRYING','CARE_CONCERN_STARTED']
  });
}

function behaviorIntervals(events, targetStates, endAt = new Date()) {
  const normalized = normalizeEvents(events);
  const intervals = [];
  let sessionActive = false;
  let currentBehavior = null;
  let startedAt = null;
  const close = at => {
    if (startedAt && targetStates.has(currentBehavior)) intervals.push([startedAt, new Date(at)]);
    startedAt = null;
    currentBehavior = null;
  };

  for (const ev of normalized) {
    if (ev.eventType === 'NIGHT_STARTED') { sessionActive = true; continue; }
    if (!sessionActive) continue;
    if (ev.eventType === 'NIGHT_COMPLETED') { close(ev.occurredAt); sessionActive = false; continue; }
    if (ev.eventType === 'CARE_CONCERN_STARTED') { close(ev.occurredAt); continue; }
    if (CARE_START[ev.eventType]) { close(ev.occurredAt); continue; }
    if (BEHAVIOR_EVENTS.has(ev.eventType)) {
      close(ev.occurredAt);
      currentBehavior = ev.eventType;
      startedAt = new Date(ev.occurredAt);
    }
  }
  if (sessionActive && startedAt && targetStates.has(currentBehavior)) intervals.push([startedAt, new Date(endAt)]);
  return intervals;
}

export function deriveMetrics(events = [], endAt = new Date()) {
  const e = normalizeEvents(events);
  const state = reduceNight(e);
  const sleepIntervals = behaviorIntervals(e, new Set(['ASLEEP']), endAt);
  const cryIntervals = behaviorIntervals(e, new Set(['CRYING']), endAt);
  const fussIntervals = behaviorIntervals(e, new Set(['FUSSING']), endAt);
  const awakeIntervals = behaviorIntervals(e, new Set(['AWAKE_CALM','FUSSING','CRYING']), endAt);
  const duration = xs => xs.reduce((s,[a,b]) => s + Math.max(0,b-a),0);
  const maxDuration = xs => xs.length ? Math.max(...xs.map(([a,b]) => Math.max(0,b-a))) : 0;

  let lastBehavior = null;
  let wakingCount = 0;
  for (const ev of e) {
    if (!BEHAVIOR_EVENTS.has(ev.eventType)) continue;
    if (lastBehavior === 'ASLEEP' && ev.eventType !== 'ASLEEP') wakingCount += 1;
    lastBehavior = ev.eventType;
  }

  const crib = e.find(x => x.eventType === 'PLACED_IN_CRIB')?.occurredAt;
  const firstSleep = e.find(x => x.eventType === 'ASLEEP')?.occurredAt;
  return {
    timeToSleepMs: crib && firstSleep ? Math.max(0, new Date(firstSleep)-new Date(crib)) : null,
    totalNightSleepMs: duration(sleepIntervals),
    longestSleepStretchMs: maxDuration(sleepIntervals),
    wakingCount,
    checkInCount: e.filter(x => x.eventType === 'CHECKIN_STARTED').length,
    feedCount: e.filter(x => x.eventType === 'FEED_STARTED').length,
    cryDurationMs: duration(cryIntervals),
    fussDurationMs: duration(fussIntervals),
    protestDurationMs: duration(cryIntervals) + duration(fussIntervals),
    longestCryDurationMs: maxDuration(cryIntervals),
    awakeOvernightMs: firstSleep ? duration(awakeIntervals.filter(([a]) => a >= new Date(firstSleep))) : 0,
    finalWakeAt: state.lastWakeAt,
    nightStartedAt: state.nightStartedAt,
    nightCompletedAt: state.nightCompletedAt
  };
}

export function validateTransition(events, eventType) {
  const state = reduceNight(events);

  if (CORRECTION_EVENTS.has(eventType)) return { ok: true };
  if (state.session === SessionState.COMPLETE) return { ok: false, reason: 'Night is complete. Correct the timeline instead.' };

  if (state.safetyState === SafetyState.CARE_CONCERN) {
    if (eventType === 'CARE_CONCERN_RESOLVED') return { ok: true };
    return { ok: false, reason: 'Resolve the care concern before resuming the night.' };
  }

  if (eventType === 'CARE_CONCERN_RESOLVED') return { ok: false, reason: 'No active care concern to resolve.' };
  if (eventType === 'NIGHT_STARTED') return state.session === SessionState.PREP ? { ok: true } : { ok: false, reason: 'Night is already active.' };
  if (eventType === 'PLAN_VERSION_CHANGED') return state.session === SessionState.PREP ? { ok: true } : { ok: false, reason: 'Tonight’s plan is locked after the night begins.' };

  if (state.session === SessionState.PREP) return { ok: false, reason: 'Begin the night before recording night events.' };
  if (eventType === 'PLACED_IN_CRIB') return state.cribPlacedAt ? { ok: false, reason: 'Crib placement is already recorded.' } : { ok: true };

  if (BEHAVIOR_EVENTS.has(eventType)) {
    if (state.caregiverAction !== CaregiverAction.NONE) return { ok: false, reason: 'Finish the active care action before marking a new baby state.' };
    if (state.babyState === eventType) return { ok: false, reason: `Already marked ${eventType.replaceAll('_',' ').toLowerCase()}.` };
    return { ok: true };
  }

  if (eventType === 'CHECKIN_STARTED') {
    if (state.caregiverAction !== CaregiverAction.NONE) return { ok: false, reason: 'Another care action is already active.' };
    if (state.babyState !== BabyState.CRYING) return { ok: false, reason: 'Check-ins are available while crying is marked. Use care options for another need.' };
    return { ok: true };
  }
  if (eventType === 'FEED_STARTED' || eventType === 'DIAPER_STARTED' || eventType === 'COMFORT_STARTED' || eventType === 'OTHER_CARE_STARTED') {
    return state.caregiverAction === CaregiverAction.NONE ? { ok: true } : { ok: false, reason: 'Another care action is already active.' };
  }

  if (CARE_END_FOR[eventType]) {
    return state.caregiverAction === CARE_END_FOR[eventType]
      ? { ok: true }
      : { ok: false, reason: 'That care action is not currently active.' };
  }

  if (eventType === 'CARE_CONCERN_STARTED') return { ok: true };
  if (eventType === 'NIGHT_COMPLETED') {
    if (state.caregiverAction !== CaregiverAction.NONE) return { ok: false, reason: 'Finish the active care action before starting the day.' };
    if (state.babyState === BabyState.ASLEEP) return { ok: false, reason: 'Baby is marked asleep. Mark the waking before starting the day.' };
    if (state.babyState === BabyState.UNKNOWN) return { ok: false, reason: 'Mark the current baby state before starting the day.' };
    return { ok: true };
  }

  return { ok: true };
}