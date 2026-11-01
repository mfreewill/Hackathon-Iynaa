// Setup/source increment client. Same-origin fetch only; the
// server snapshot is the authority and no proposal is ever
// generated or inferred here.

const UNAVAILABLE_MESSAGE =
  "خدمة تحليل المصدر غير متصلة في هذا التشغيل. لم يُقترح أيّ مقطع أو سؤال، وتبقى صفحتك وإعداداتك كما هي.";
const INVALID_FILE_MESSAGE =
  "تعذّرت قراءة ملف الصفحة المختار. لم يتغيّر المصدر الحالي؛ اختر ملف صفحة صالحًا ثم حاول مجددًا.";
const RECEIVED_MESSAGE =
  "وصلت مقترحات من خدمة التحليل وحُفظت في مساحة العمل؛ راجع المقاطع أدناه قبل توليد الأسئلة.";
const SESSION_MESSAGE =
  "انتهت جلسة هذا المتصفح أو تعذّر التحقق منها. أعد تحميل الصفحة لبدء جلسة جديدة؛ قد تُفقد مساحة العمل غير المحفوظة.";
const UNKNOWN_STATUS =
  "نتيجة الطلب غير معروفة: لا يمكن التحقق من المصدر المعروض في هذه الزيارة. أوقف العمل هنا؛ لإعادة البدء حدّث المتصفح عمدًا وابدأ من جديد، مع العلم أن هذا العمل غير المحفوظ قد يُفقد.";
const UNKNOWN_SOURCE_NOTE =
  "لا يمكن التحقق من المصدر المعروض بعد طلب نتيجته غير معروفة، لذا أُخفيت معاينته وروابطه في هذه الزيارة.";

const statusLine = document.querySelector(
  "[data-testid='workspace-status']");
const form = document.querySelector("[data-testid='setup-form']");
const fileInput = document.getElementById("page-file");
const gradeInput = document.getElementById("grade");
const objectiveInput = document.getElementById("objective");
const pageNumberInput = document.getElementById("page-number");
const setupError = document.querySelector("[data-testid='setup-error']");
const replacementWarning = document.querySelector(
  "[data-testid='replacement-warning']");
const primaryButton = form.querySelector(".primary-action");
const workPane = document.querySelector("[data-testid='work-pane']");
const sourcePane = document.querySelector("[data-testid='source-pane']");
const sourceEmpty = document.querySelector("[data-testid='source-empty']");
const sourceReference = document.querySelector(
  "[data-testid='source-reference']");
const previewImage = document.querySelector(
  "[data-testid='source-preview']");
const originalLink = document.querySelector(
  "[data-testid='source-original-link']");
const sourceIdValue = document.querySelector("[data-testid='source-id']");
const sourceShaValue = document.querySelector(
  "[data-testid='source-sha256']");
const renderShaValue = document.querySelector(
  "[data-testid='render-sha256']");
const selectedPageValue = document.querySelector(
  "[data-testid='selected-page-number']");
const sourceDetails = document.querySelector(
  "[data-testid='source-details']");
const paneSwitch = document.querySelector(
  "[data-testid='pane-switch']");
const sourceTab = document.querySelector("[data-testid='pane-tab-source']");
const workTab = document.querySelector("[data-testid='pane-tab-work']");
const sourceHeading = document.getElementById("source-heading");
const operationGroup = document.querySelector(
  "[data-testid='provider-operation']");
const operationStatus = document.querySelector(
  "[data-testid='provider-operation-status']");
const operationCancelButton = document.querySelector(
  "[data-testid='provider-operation-cancel']");
const operationCheckButton = document.querySelector(
  "[data-testid='provider-operation-check']");
const operationRetryButton = document.querySelector(
  "[data-testid='provider-operation-retry']");
const operationRetryWarning = document.querySelector(
  "[data-testid='provider-retry-warning']");
const settingsApplyButton = document.querySelector(
  "[data-testid='settings-apply']");
const generationRetryWarning = document.querySelector(
  "[data-testid='generation-retry-warning']");

let csrfToken = null;
let sessionPromise = null;
let pending = false;
let hasCommittedSource = false;
let committedSourceId = null;
let outcomeUncertain = false;
const uncertainCallbacks = [];
const sourceStartedCallbacks = [];
const providerSnapshotCallbacks = [];
const providerPermissionCallbacks = [];
const identityRefusalCallbacks = [];
const settingsCallbacks = [];
const standaloneSettingsPermissionCallbacks = [];
const busyCallbacks = [];
let mutationChain = Promise.resolve();
let queuedMutations = 0;
let startQueued = false;
// Visual-ownership generation: bumped whenever a new serialized
// mutation starts executing and when a new source is adopted, so
// late callbacks of an older provider operation become inert.
let uiGeneration = 0;
let operationSequence = 0;
let activeProviderOperation = null;
let lastSettledOperation = null;

// Standalone settings permission is owned by the review
// coordinator's one currentness authority and only queried here
// (its sticky-identity reason); this module keeps no second
// currentness flag. A settings-reason withdrawal still permits
// the deliberate correction send, and Start never asks this.
function standaloneSettingsPermitted() {
  return standaloneSettingsPermissionCallbacks.every(
    (callback) => callback() === true);
}

function renderStandaloneSettingsControl() {
  if (!settingsApplyButton) {
    return;
  }
  const busy = queuedMutations > 0 || pending;
  settingsApplyButton.disabled = outcomeUncertain || busy
    || !standaloneSettingsPermitted();
}

function notifyBusy() {
  const busy = queuedMutations > 0 || pending;
  primaryButton.disabled = outcomeUncertain || busy;
  renderStandaloneSettingsControl();
  renderOperationControls();
  for (const callback of busyCallbacks) {
    callback(busy);
  }
}

function runSerialized(task) {
  queuedMutations += 1;
  notifyBusy();
  const begin = () => {
    uiGeneration += 1;
    return task();
  };
  const run = mutationChain.then(begin, begin);
  mutationChain = run.catch(() => {});
  return run.finally(() => {
    queuedMutations -= 1;
    notifyBusy();
  });
}

class StageError extends Error {
  constructor(stage, status, detail, network = false,
      unusableBody = false) {
    super("request failed");
    this.stage = stage;
    this.status = status;
    this.detail = detail;
    this.network = network;
    this.unusableBody = unusableBody;
  }
}

// Responses that prove the server refused before committing; any
// other outcome of a source-affecting stage is uncertain.
const DEFINITE_REFUSALS = {
  session: new Set([400, 401, 403, 413, 415, 429]),
  settings: new Set([400, 401, 403, 413, 415]),
  start: new Set([400, 401, 403, 409, 413, 415, 429]),
  "region-review": new Set([400, 401, 403, 404, 413, 415]),
  "region-move": new Set([400, 401, 403, 404, 413, 415]),
  "question-edit": new Set([400, 401, 403, 404, 413, 415]),
  "question-decision": new Set([400, 401, 403, 404, 409, 413, 415]),
  // Generation 502/503/504 need envelope/lifecycle evidence in
  // review.js; status alone never proves a precommit refusal.
  generation: new Set([400, 401, 403, 404, 409, 413, 415, 429]),
};

function isUncertainOutcome(error) {
  if (!error || error.stage === "analysis") {
    return false;
  }
  if (!(error instanceof StageError)) {
    return true;
  }
  if (error.network || error.unusableBody) {
    return true;
  }
  // The one concrete session-capacity response proves the server
  // inserted no session. No other 503 gains this exception.
  if (error.stage === "session" && error.status === 503
      && error.detail === "session limit reached") {
    return false;
  }
  const definite = DEFINITE_REFUSALS[error.stage];
  return !(definite && definite.has(error.status));
}

function enterUncertainOutcome() {
  if (outcomeUncertain) {
    return;
  }
  outcomeUncertain = true;
  uiGeneration += 1;
  primaryButton.disabled = true;
  if (settingsApplyButton) {
    settingsApplyButton.disabled = true;
  }
  renderOperationControls();
  sourceReference.hidden = true;
  previewImage.removeAttribute("src");
  originalLink.removeAttribute("href");
  sourceIdValue.textContent = "";
  sourceShaValue.textContent = "";
  renderShaValue.textContent = "";
  selectedPageValue.textContent = "";
  sourceDetails.removeAttribute("open");
  sourceEmpty.textContent = UNKNOWN_SOURCE_NOTE;
  sourceEmpty.hidden = false;
  setupError.textContent = UNKNOWN_STATUS;
  setupError.hidden = false;
  statusLine.textContent = UNKNOWN_STATUS;
  for (const callback of uncertainCallbacks) {
    callback();
  }
}

function detailCode(error) {
  if (error && error.detail && typeof error.detail === "object"
      && typeof error.detail.code === "string") {
    return error.detail.code;
  }
  return "";
}

async function readDetail(response) {
  try {
    const data = await response.clone().json();
    return data && typeof data === "object" ? data.detail ?? null : null;
  } catch {
    return null;
  }
}

async function readJsonBody(stage, response) {
  try {
    return await response.json();
  } catch {
    throw new StageError(stage, response.status, null, false, true);
  }
}

async function apiFetch(stage, url, options = {}) {
  const sourceIdAtSend = committedSourceId;
  const generationAtSend = uiGeneration;
  let response;
  try {
    response = await fetch(url, { credentials: "same-origin", ...options });
  } catch {
    throw new StageError(stage, 0, null, true);
  }
  if (!response.ok) {
    const detail = await readDetail(response);
    notifyOrdinaryIdentityRefusal(
      stage, response.status, detail, sourceIdAtSend,
      generationAtSend);
    throw new StageError(stage, response.status, detail);
  }
  return response;
}

async function fetchResponse(stage, url, options = {}) {
  try {
    return await fetch(url, { credentials: "same-origin", ...options });
  } catch {
    throw new StageError(stage, 0, null, true);
  }
}

async function getJson(path) {
  const response = await fetch(path, { credentials: "same-origin" });
  if (!response.ok) {
    throw new Error("readback failed");
  }
  return response.json();
}

function ensureSession() {
  if (csrfToken) {
    return Promise.resolve(csrfToken);
  }
  if (!sessionPromise) {
    sessionPromise = (async () => {
      const response = await apiFetch("session", "/api/sessions", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: "{}",
      });
      const data = await readJsonBody("session", response);
      if (!data || typeof data.csrf_token !== "string"
          || !data.csrf_token) {
        throw new StageError(
          "session", response.status, null, false, true);
      }
      csrfToken = data.csrf_token;
      return csrfToken;
    })().catch((error) => {
      sessionPromise = null;
      throw error;
    });
  }
  return sessionPromise;
}

function mutationHeaders(extra = {}) {
  return { "X-CSRF-Token": csrfToken, ...extra };
}

function showSetupError(message, focusTarget = null) {
  setupError.textContent = message;
  setupError.hidden = false;
  if (focusTarget) {
    focusTarget.focus();
  }
}

function clearSetupError() {
  setupError.textContent = "";
  setupError.hidden = true;
}

function setupStageMessage(error) {
  if (error.status === 401 || error.status === 403) {
    return SESSION_MESSAGE;
  }
  if (error.stage === "settings") {
    return error.status === 413
      ? "الإعدادات كبيرة جدًا ولم تُقبل. قصّر النصوص ثم حاول مجددًا بضغطة جديدة."
      : "تعذّر قبول الإعدادات. راجع الصف الدراسي والهدف التعليمي ثم حاول مجددًا بضغطة جديدة.";
  }
  if (error.stage === "start") {
    if (error.status === 400 || error.status === 415) {
      return INVALID_FILE_MESSAGE;
    }
    if (error.status === 413) {
      return "ملف الصفحة كبير جدًا ولم يُقبل. لم يتغيّر المصدر الحالي؛ اختر ملفًا أصغر ثم حاول مجددًا.";
    }
    if (error.status === 429) {
      return "توجد عمليات رفع كثيرة الآن. لم يتغيّر المصدر الحالي؛ انتظر قليلًا ثم حاول مجددًا بضغطة جديدة.";
    }
    if (error.status === 409) {
      return "تغيّرت الإعدادات أثناء تجهيز الصفحة ولم يُعتمد هذا البدء. لم يتغيّر المصدر الحالي؛ راجع الإعدادات ثم اضغط «بدء تحليل الصفحة» مجددًا.";
    }
  }
  return "تعذّر إكمال هذه الخطوة. لم نُعد المحاولة تلقائيًا؛ راجع ما هو ظاهر ثم حاول مجددًا بضغطة جديدة.";
}

function analysisMessage(error) {
  if (error.network) {
    return "تعذّر الاتصال بالخادم المحلي أثناء طلب التحليل. لم نُعد المحاولة تلقائيًا؛ تبقى الصفحة المعلنة كما هي، وحالة طلب التحليل نفسه غير مؤكدة.";
  }
  if (error.status === 401 || error.status === 403) {
    return SESSION_MESSAGE;
  }
  const code = detailCode(error);
  if (error.status === 503 && code === "provider_unavailable") {
    return UNAVAILABLE_MESSAGE;
  }
  const phrase = outcomePhrase(code);
  if (phrase) {
    return `لم يكتمل طلب التحليل: ${phrase}. لم يُقترح أيّ مقطع أو سؤال، وتبقى صفحتك وإعداداتك كما هي.`;
  }
  const byCode = {
    retained_budget_exceeded:
      "مساحة العمل المحفوظة ممتلئة ولم يكتمل طلب التحليل. لم يُقترح أيّ مقطع أو سؤال في هذه الخطوة.",
    request_closed:
      "أُغلق طلب التحليل قبل اكتماله ولم تُطبَّق نتيجته. تبقى صفحتك وإعداداتك كما هي.",
  };
  if (byCode[code]) {
    return byCode[code];
  }
  if (error.status === 503) {
    return "خدمة تحليل المصدر غير متاحة في هذا التشغيل. لم يُقترح أيّ مقطع أو سؤال، وتبقى صفحتك وإعداداتك كما هي.";
  }
  if (error.status === 409) {
    return byCode.request_closed;
  }
  if (error.status === 413) {
    return byCode.retained_budget_exceeded;
  }
  return "لم يكتمل طلب التحليل ولم يُقترح أيّ مقطع أو سؤال. تبقى صفحتك وإعداداتك كما هي.";
}

function updateReplacementWarning() {
  // Every Start destructively replaces the workspace, so the
  // notice follows the existence of a committed source, never a
  // comparison of file name, size, time or chosen page.
  replacementWarning.hidden = !hasCommittedSource;
}

function arabicIndic(number) {
  return String(number).replace(/\d/g, (digit) => "٠١٢٣٤٥٦٧٨٩"[digit]);
}

function isUsableMetadata(metadata) {
  if (!metadata || typeof metadata !== "object") {
    return false;
  }
  const strings = [
    "source_id", "source_sha256", "render_sha256", "original_url",
    "preview_url"];
  const numbers = ["page_index", "page_count", "width", "height"];
  return strings.every(
      (name) => typeof metadata[name] === "string" && metadata[name])
    && numbers.every((name) => Number.isFinite(metadata[name]));
}

function isUsableSnapshot(snapshot) {
  return Boolean(snapshot && typeof snapshot === "object"
    && typeof snapshot.source_id === "string" && snapshot.source_id
    && Array.isArray(snapshot.regions)
    && Array.isArray(snapshot.questions)
    && Array.isArray(snapshot.requests)
    && Array.isArray(snapshot.slot_outcomes)
    && Array.isArray(snapshot.preview_items)
    && typeof snapshot.can_generate === "boolean"
    && Number.isFinite(snapshot.included_count)
    && Number.isFinite(snapshot.withheld_count));
}

function isUsableProviderEnvelope(body) {
  return Boolean(body && typeof body === "object"
    && typeof body.request_id === "string" && body.request_id
    && typeof body.raw_retention === "string" && body.raw_retention
    && isUsableSnapshot(body.snapshot));
}

// -- Exact provider operations (source analysis / generation) ----
// One operation object owns kind, request ID, acknowledged source
// and UI generation. The serialized caller awaits the operation's
// logical wait, which the original response or the narrow
// Cancel/Check lane resolves once exact closure and snapshot
// reconciliation are established. Late completions of the original
// POST stay handled but inert.
const PROVIDER_PATHS = {
  source: "/api/session/source-analysis",
  questions: "/api/session/questions/generate",
};
const CLOSED_REQUEST_STATES = new Set(["terminal", "superseded"]);
const OPERATION_UNCONFIRMED =
  "حالة هذا الطلب غير مؤكدة بعد؛ لم نُعد إرساله ولم نبدأ طلبًا آخر. استخدم «التحقق من حالة الطلب» أو «إلغاء الطلب» لهذا الطلب نفسه.";
const OPERATION_STILL_WORKING =
  "الطلب ما زال قيد التنفيذ لدى المزوّد؛ لم يُغلق بعد.";
const OPERATION_UNRECONCILED =
  "أُغلق الطلب، لكن تعذّرت مطابقة حالة مساحة العمل الحالية معه بعد؛ استخدم «التحقق من حالة الطلب» لإكمال المطابقة.";
const OPERATION_REFUSED =
  "رُفض هذا الإرسال قبل قبوله؛ لم يبدأ طلب لدى المزوّد في هذه المحاولة.";
const OPERATION_SUCCEEDED = "اكتمل الطلب وطُبّقت نتيجته.";
const OPERATION_CLOSED_NON_OK =
  "أُغلق الطلب دون اكتمال ناجح؛ الحالة المعروضة هي آخر حالة مؤكدة من الخادم.";
const OPERATION_CANCELLED =
  "أُغلق الطلب ملغيًا؛ لن تُطبَّق أي نتيجة متأخرة منه في مساحة العمل.";
// One shared outcome phrase owner for every known non-success
// terminal outcome: exact operation closures here, source
// analysis in analysisMessage, and question generation in
// review.js through the exported workspace interface. Each
// consumer wraps the phrase in its own context; known outcomes
// stay distinct and never read as success, and an unknown
// outcome keeps each consumer's generic fallback. Success and
// cancellation strings stay pinned above, outside this map.
const OUTCOME_PHRASES = {
  incomplete: "وصل ناتج غير مكتمل",
  invalid_schema: "الناتج لا يطابق البنية المطلوبة",
  empty: "لم يصل أيّ ناتج",
  error: "حدث خطأ لدى المزوّد",
  refusal: "رفض المزوّد الطلب",
  timeout: "انتهى وقت الطلب قبل اكتماله",
  rate_limited: "الخدمة مشغولة الآن",
};

function outcomePhrase(outcome) {
  return OUTCOME_PHRASES[outcome] || null;
}

function closedOperationStatus(outcome) {
  if (outcome === "cancelled") {
    return OPERATION_CANCELLED;
  }
  const phrase = outcomePhrase(outcome);
  return phrase
    ? `أُغلق الطلب: ${phrase}؛ الحالة المعروضة هي آخر حالة مؤكدة من الخادم.`
    : OPERATION_CLOSED_NON_OK;
}

function newRequestId() {
  const bytes = new Uint8Array(16);
  crypto.getRandomValues(bytes);
  return [...bytes]
    .map((byte) => byte.toString(16).padStart(2, "0")).join("");
}

function exactRequestEntry(snapshot, operation) {
  if (!isUsableSnapshot(snapshot)) {
    return null;
  }
  return snapshot.requests.find((entry) => entry
    && entry.request_id === operation.requestId
    && entry.kind === operation.kind) || null;
}

function isClosedEntry(entry) {
  return Boolean(entry && CLOSED_REQUEST_STATES.has(entry.state));
}

function detailOfBody(body) {
  return body && typeof body === "object" ? body.detail ?? null : null;
}

function hasOperationMetadata(body) {
  return Boolean(body && typeof body === "object"
    && ("snapshot" in body || "request_id" in body
      || "raw_retention" in body));
}

// Only complete refusal bodies that the accepted HTTP contract
// sends before admission, matched by body plus status. Duplicate
// (409) and plain 413 are deliberately absent: they stay
// unresolved until the exact status read decides.
const PRE_ADMISSION_REFUSALS = {
  400: new Set(["invalid request", "invalid host"]),
  401: new Set(["session required"]),
  403: new Set(["forbidden origin", "forbidden request"]),
  404: new Set(["page not found"]),
  409: new Set(["generation not ready", "source changed"]),
  415: new Set(["unsupported media type"]),
  429: new Set(["too many provider requests"]),
};

function isKnownPreAdmissionRefusal(status, body) {
  if (!body || typeof body !== "object"
      || Object.keys(body).length !== 1 || !("detail" in body)) {
    return false;
  }
  const detail = body.detail;
  if (status === 503) {
    return Boolean(detail && typeof detail === "object"
      && detail.code === "provider_unavailable");
  }
  const allowed = PRE_ADMISSION_REFUSALS[status];
  return Boolean(allowed && typeof detail === "string"
    && allowed.has(detail));
}

// Sends this module drives itself (analysis retry in
// particular) consult the review coordinator's one currentness
// permission, registered below; there is no second currency
// value here.
function providerSendPermitted(kind, sourceId) {
  return providerPermissionCallbacks.every(
    (callback) => callback({ kind, sourceId }) === true);
}

// Refusals that invalidate this page's session/source identity:
// the send was not admitted, and the old displayed view can no
// longer be certified current. Invalid-origin 403 is not one:
// it is an ordinary refusal that grants nothing either.
function isIdentityInvalidatingRefusal(status, detail) {
  return (status === 401 && detail === "session required")
    || (status === 403 && detail === "forbidden request")
    || (status === 409 && detail === "source changed");
}

// Ordinary (non-provider) sends reach the same identity-refusal
// callbacks the provider lane uses, through this one fenced path:
// exact classifier only, 409 only where the stage already treats
// it as a definite refusal, and the source/UI generation captured
// before the send must still be the displayed context after the
// awaits. Ordinary validation, origin, capacity and approval
// refusals never satisfy the exact classifier.
const ORDINARY_IDENTITY_REFUSAL_STAGES = new Set([
  "settings", "start", "region-review", "region-move",
  "question-edit", "question-decision"]);

function notifyOrdinaryIdentityRefusal(
    stage, status, detail, sourceId, generation) {
  if (!ORDINARY_IDENTITY_REFUSAL_STAGES.has(stage)
      || outcomeUncertain) {
    return;
  }
  if (status === 409 && !DEFINITE_REFUSALS[stage]?.has(409)) {
    return;
  }
  if (!isIdentityInvalidatingRefusal(status, detail)) {
    return;
  }
  if (committedSourceId !== sourceId || uiGeneration !== generation) {
    return;
  }
  for (const callback of identityRefusalCallbacks) {
    callback({
      kind: stage,
      sourceId,
      generation,
      httpStatus: status,
      detail,
    });
  }
}

function operationOwnsUi(operation) {
  return Boolean(operation
    && operation === activeProviderOperation
    && !operation.settled
    && !outcomeUncertain
    && operation.sourceId === committedSourceId
    && operation.generation === uiGeneration);
}

function setOperationStatus(operation, text) {
  operation.statusText = text;
  renderOperationControls();
}

function markOperationUnresolved(operation, text) {
  if (!operationOwnsUi(operation)) {
    return;
  }
  setOperationStatus(operation, text);
  statusLine.textContent = text;
}

function renderOperationControls() {
  if (!operationGroup || !operationStatus) {
    return;
  }
  const display = activeProviderOperation || lastSettledOperation;
  if (!display || outcomeUncertain) {
    operationGroup.hidden = true;
    return;
  }
  operationGroup.hidden = false;
  operationStatus.textContent = display.statusText || "";
  const active = display === activeProviderOperation && !display.settled;
  const controlsDisabled = !active || !display.sent
    || display.controlInFlight;
  if (operationCancelButton) {
    operationCancelButton.hidden = !active;
    operationCancelButton.disabled = controlsDisabled;
  }
  if (operationCheckButton) {
    operationCheckButton.hidden = !active;
    operationCheckButton.disabled = controlsDisabled;
  }
  const canRetry = Boolean(!activeProviderOperation
    && display.kind === "source" && display.settled
    && !display.discarded && display.result
    && display.result.type !== "success"
    && hasCommittedSource
    && display.sourceId === committedSourceId
    && providerSendPermitted("source", display.sourceId));
  if (operationRetryButton) {
    operationRetryButton.hidden = !canRetry;
    operationRetryButton.disabled = !canRetry
      || queuedMutations > 0 || pending;
  }
  if (operationRetryWarning) {
    operationRetryWarning.hidden = !canRetry;
  }
  if (generationRetryWarning) {
    // Explicit new generation after an exact non-success closure
    // is a new provider request; warn beside that action only.
    // There is no automatic resend and no source-retry reuse.
    const generationClosedNonOk = Boolean(
      !activeProviderOperation
      && display.kind === "questions" && display.settled
      && !display.discarded && display.result
      && display.result.type === "terminal"
      && display.sourceId === committedSourceId);
    generationRetryWarning.hidden = !generationClosedNonOk;
  }
}

// Synchronous claim: a rapid second activation finds the first
// operation already owning the provider lane and sends nothing.
function claimProviderOperation(kind, sourceId) {
  if ((kind !== "source" && kind !== "questions") || !sourceId
      || outcomeUncertain || activeProviderOperation
      || sourceId !== committedSourceId) {
    return null;
  }
  let resolveLogical;
  const logicalPromise = new Promise((resolve) => {
    resolveLogical = resolve;
  });
  const operation = {
    token: ++operationSequence,
    kind,
    requestId: newRequestId(),
    sourceId,
    generation: uiGeneration,
    sent: false,
    settled: false,
    discarded: false,
    closureKnown: false,
    closureOutcome: null,
    controlInFlight: false,
    reconciling: false,
    result: null,
    statusText: kind === "source"
      ? "سيُرسل طلب تحليل المصدر عند بدء تنفيذه."
      : "سيُرسل طلب توليد الأسئلة عند بدء تنفيذه.",
    logicalPromise,
    resolveLogical,
  };
  activeProviderOperation = operation;
  renderOperationControls();
  return operation;
}

function settleOperation(operation, result, statusText) {
  operation.settled = true;
  operation.result = result;
  lastSettledOperation = operation;
  setOperationStatus(operation, statusText);
  operation.resolveLogical(result);
}

// Reconcile one exact closed operation with one actual snapshot
// through the review coordinator's existing adoption and
// settings/currentness checks, then release the logical wait.
// A failed adoption keeps the known closure for a later Check.
async function reconcileAndSettle(operation, evidence) {
  if (operation.settled || operation.reconciling
      || !operationOwnsUi(operation)) {
    return false;
  }
  operation.reconciling = true;
  try {
    let adopted = false;
    for (const callback of providerSnapshotCallbacks) {
      adopted = await callback({
        kind: operation.kind,
        requestId: operation.requestId,
        sourceId: operation.sourceId,
        snapshot: evidence.snapshot,
        rawRetention: evidence.rawRetention ?? null,
        outcome: evidence.outcome,
      }) === true || adopted;
    }
    if (!operationOwnsUi(operation)) {
      return false;
    }
    if (!adopted) {
      markOperationUnresolved(operation, OPERATION_UNRECONCILED);
      return false;
    }
    const succeeded = evidence.outcome === "ok";
    settleOperation(operation, {
      type: succeeded ? "success" : "terminal",
      outcome: evidence.outcome,
      snapshot: evidence.snapshot,
      httpStatus: evidence.httpStatus ?? 0,
      detail: evidence.detail ?? null,
    }, succeeded ? OPERATION_SUCCEEDED
      : closedOperationStatus(evidence.outcome));
    return true;
  } finally {
    operation.reconciling = false;
  }
}

function classifyOriginalResponse(operation, status, body, readable) {
  if (!operationOwnsUi(operation)) {
    return;
  }
  if (readable && body && typeof body === "object") {
    if (isUsableProviderEnvelope(body)
        && body.request_id === operation.requestId
        && body.snapshot.source_id === operation.sourceId) {
      const entry = exactRequestEntry(body.snapshot, operation);
      if (entry && isClosedEntry(entry)) {
        // An exact terminal "ok" wins even on a non-2xx response;
        // any other closed outcome stays truthful non-success.
        operation.closureKnown = true;
        operation.closureOutcome = entry.outcome;
        void reconcileAndSettle(operation, {
          snapshot: body.snapshot,
          rawRetention: body.raw_retention,
          outcome: entry.outcome,
          httpStatus: status,
          detail: detailOfBody(body),
        });
        return;
      }
      if (entry && entry.state === "working") {
        markOperationUnresolved(operation, OPERATION_STILL_WORKING);
        return;
      }
      markOperationUnresolved(operation, OPERATION_UNCONFIRMED);
      return;
    }
    if (hasOperationMetadata(body)) {
      markOperationUnresolved(operation, OPERATION_UNCONFIRMED);
      return;
    }
    if (isKnownPreAdmissionRefusal(status, body)) {
      if (isIdentityInvalidatingRefusal(
          status, detailOfBody(body))) {
        for (const callback of identityRefusalCallbacks) {
          callback({
            kind: operation.kind,
            sourceId: operation.sourceId,
            httpStatus: status,
            detail: detailOfBody(body),
          });
        }
      }
      settleOperation(operation, {
        type: "refusal",
        outcome: null,
        snapshot: null,
        httpStatus: status,
        detail: detailOfBody(body),
      }, OPERATION_REFUSED);
      return;
    }
  }
  markOperationUnresolved(operation, OPERATION_UNCONFIRMED);
}

// Runs inside the caller's serialized task. Rechecks the queued
// claim immediately before its single exact POST, then returns
// the operation's logical wait: the serialized task completes on
// exact settlement even while the original POST is still pending.
// A pre-send session failure still throws to the caller's
// existing staged-error handling; nothing was sent for it.
async function executeProviderOperation(operation) {
  if (!operation || operation !== activeProviderOperation
      || operation.settled || operation.sent || outcomeUncertain
      || operation.sourceId !== committedSourceId) {
    return { type: "aborted" };
  }
  await ensureSession();
  if (operation !== activeProviderOperation || operation.settled
      || operation.sent || outcomeUncertain
      || operation.sourceId !== committedSourceId
      || !providerSendPermitted(
        operation.kind, operation.sourceId)) {
    return { type: "aborted" };
  }
  operation.generation = uiGeneration;
  operation.sent = true;
  setOperationStatus(operation, operation.kind === "source"
    ? "طلب تحليل المصدر قيد التنفيذ لدى المزوّد."
    : "طلب توليد الأسئلة قيد التنفيذ لدى المزوّد.");
  const stage = operation.kind === "source" ? "analysis" : "generation";
  const original = (async () => {
    let response;
    try {
      response = await fetchResponse(
        stage, PROVIDER_PATHS[operation.kind], {
          method: "POST",
          headers: mutationHeaders(
            { "Content-Type": "application/json" }),
          body: JSON.stringify({
            request_id: operation.requestId,
            source_id: operation.sourceId,
          }),
        });
    } catch {
      markOperationUnresolved(operation, OPERATION_UNCONFIRMED);
      return;
    }
    let body = null;
    let readable = true;
    try {
      body = await response.json();
    } catch {
      readable = false;
    }
    classifyOriginalResponse(
      operation, response.status, body, readable);
  })();
  original.catch(() => {});
  return operation.logicalPromise;
}

function finishProviderOperation(operation) {
  if (!operation) {
    return;
  }
  if (!operation.settled) {
    operation.settled = true;
    operation.discarded = true;
    operation.resolveLogical({ type: "aborted" });
  }
  if (activeProviderOperation === operation) {
    activeProviderOperation = null;
  }
  renderOperationControls();
}

// -- Narrow Cancel/Check lane --------------------------------------
// Outside the serialized mutation queue (which this operation's
// POST may still be blocking), but under the same session/CSRF
// authority and bound to the one active exact operation.
async function reconcileFromSnapshotRead(operation) {
  let snapshot = null;
  try {
    const response = await fetchResponse(
      "provider-snapshot", "/api/session/snapshot");
    if (response.ok) {
      snapshot = await response.json();
    }
  } catch {
    snapshot = null;
  }
  if (!operationOwnsUi(operation)) {
    return;
  }
  const entry = snapshot && snapshot.source_id === operation.sourceId
    ? exactRequestEntry(snapshot, operation) : null;
  if (!entry || !isClosedEntry(entry)) {
    markOperationUnresolved(operation, OPERATION_UNRECONCILED);
    return;
  }
  operation.closureOutcome = entry.outcome;
  await reconcileAndSettle(operation, {
    snapshot,
    rawRetention: null,
    outcome: entry.outcome,
    httpStatus: 200,
    detail: null,
  });
}

async function checkActiveOperation() {
  const operation = activeProviderOperation;
  if (!operation || operation.settled || !operation.sent
      || operation.controlInFlight || outcomeUncertain) {
    return;
  }
  operation.controlInFlight = true;
  renderOperationControls();
  try {
    if (operation.closureKnown) {
      await reconcileFromSnapshotRead(operation);
      return;
    }
    await ensureSession();
    if (!operationOwnsUi(operation)) {
      return;
    }
    let response;
    try {
      response = await fetchResponse("provider-status",
        `/api/session/requests/${encodeURIComponent(operation.requestId)}/status?source_id=${encodeURIComponent(operation.sourceId)}`);
    } catch {
      markOperationUnresolved(operation, OPERATION_UNCONFIRMED);
      return;
    }
    let body = null;
    try {
      body = await response.json();
    } catch {
      body = null;
    }
    if (!operationOwnsUi(operation)) {
      return;
    }
    const entry = response.status === 200 && body
      && body.request_id === operation.requestId
      && body.source_id === operation.sourceId
      && body.request
      && body.request.request_id === operation.requestId
      && body.request.kind === operation.kind
      ? body.request : null;
    if (!entry) {
      markOperationUnresolved(operation, OPERATION_UNCONFIRMED);
      return;
    }
    if (entry.state === "working") {
      markOperationUnresolved(operation, OPERATION_STILL_WORKING);
      return;
    }
    if (!isClosedEntry(entry)) {
      markOperationUnresolved(operation, OPERATION_UNCONFIRMED);
      return;
    }
    operation.closureKnown = true;
    operation.closureOutcome = entry.outcome;
    await reconcileFromSnapshotRead(operation);
  } catch {
    if (operationOwnsUi(operation)) {
      markOperationUnresolved(operation, OPERATION_UNCONFIRMED);
    }
  } finally {
    operation.controlInFlight = false;
    renderOperationControls();
  }
}

async function cancelActiveOperation() {
  const operation = activeProviderOperation;
  if (!operation || operation.settled || !operation.sent
      || operation.controlInFlight || outcomeUncertain) {
    return;
  }
  operation.controlInFlight = true;
  renderOperationControls();
  try {
    await ensureSession();
    if (!operationOwnsUi(operation)) {
      return;
    }
    let response;
    try {
      response = await fetchResponse("provider-cancel",
        `/api/session/requests/${encodeURIComponent(operation.requestId)}/cancel`, {
          method: "POST",
          headers: mutationHeaders(
            { "Content-Type": "application/json" }),
          body: JSON.stringify({ source_id: operation.sourceId }),
        });
    } catch {
      markOperationUnresolved(operation, OPERATION_UNCONFIRMED);
      return;
    }
    let body = null;
    try {
      body = await response.json();
    } catch {
      body = null;
    }
    if (!operationOwnsUi(operation)) {
      return;
    }
    const entry = response.status === 200 && body
      && body.request_id === operation.requestId
      && body.source_id === operation.sourceId
      && isUsableSnapshot(body.snapshot)
      && body.snapshot.source_id === operation.sourceId
      ? exactRequestEntry(body.snapshot, operation) : null;
    if (entry && entry.state === "terminal"
        && entry.outcome === "cancelled") {
      operation.closureKnown = true;
      operation.closureOutcome = entry.outcome;
      await reconcileAndSettle(operation, {
        snapshot: body.snapshot,
        rawRetention: null,
        outcome: entry.outcome,
        httpStatus: response.status,
        detail: null,
      });
      return;
    }
    // 404/409/other: cancellation is unconfirmed. Completion may
    // have won, or admission may not have happened yet; only an
    // exact Check can decide. Nothing here certifies closure.
    markOperationUnresolved(operation, OPERATION_UNCONFIRMED);
  } catch {
    if (operationOwnsUi(operation)) {
      markOperationUnresolved(operation, OPERATION_UNCONFIRMED);
    }
  } finally {
    operation.controlInFlight = false;
    renderOperationControls();
  }
}

function adoptSource(metadata) {
  uiGeneration += 1;
  hasCommittedSource = true;
  committedSourceId = metadata.source_id;
  sourceEmpty.hidden = true;
  sourceReference.hidden = false;
  previewImage.src =
    `${metadata.preview_url}?v=${encodeURIComponent(metadata.render_sha256)}`;
  originalLink.href =
    `${metadata.original_url}?source=${encodeURIComponent(metadata.source_sha256)}`;
  sourceIdValue.textContent = metadata.source_id;
  sourceShaValue.textContent = metadata.source_sha256;
  renderShaValue.textContent = metadata.render_sha256;
  selectedPageValue.textContent =
    `${arabicIndic(metadata.page_index + 1)} من ${arabicIndic(metadata.page_count)}`;
  updateReplacementWarning();
}

function analysisResultMessage(result) {
  if (result.type === "success") {
    return RECEIVED_MESSAGE;
  }
  if (result.type === "refusal") {
    return analysisMessage(new StageError(
      "analysis", result.httpStatus, result.detail));
  }
  if (result.outcome === "cancelled") {
    return "أُلغي طلب تحليل المصدر قبل اكتماله ولم تُطبَّق نتيجته. تبقى صفحتك وإعداداتك كما هي.";
  }
  return analysisMessage(new StageError(
    "analysis", result.httpStatus,
    result.detail ?? { code: result.outcome }));
}

// The one settings PUT/acknowledgment path, shared by Start and
// the standalone settings save. Exact request body and exact
// echo validation stay as they were; a completed request whose
// echo does not match is never treated as acknowledged. Only the
// standalone save passes a before-send guard, rechecked after the
// session await so a sticky identity refusal learned in between
// blocks the final send; Start keeps its separate explicit
// semantics and passes none. Returns false when the guard kept
// the send from happening at all.
async function putSettingsAcknowledged(
    grade, objective, beforeSend = null) {
  await ensureSession();
  if (beforeSend && beforeSend() !== true) {
    return false;
  }
  const settingsResponse = await apiFetch(
    "settings", "/api/session/settings", {
      method: "PUT",
      headers: mutationHeaders({ "Content-Type": "application/json" }),
      body: JSON.stringify({ grade, objective, rights_ref: "" }),
    });
  const settingsData = await readJsonBody(
    "settings", settingsResponse);
  if (!settingsData || settingsData.grade !== grade
      || settingsData.objective !== objective
      || settingsData.rights_ref !== "") {
    throw new StageError(
      "settings", settingsResponse.status, null, false, true);
  }
  // An acknowledged settings change can outdate existing
  // approvals even when a following Start is definitely refused,
  // so let the review view refresh that effect on acknowledgment.
  for (const callback of settingsCallbacks) {
    await callback({ grade, objective });
  }
  return true;
}

async function startAnalysis() {
  if (pending || outcomeUncertain) {
    return;
  }
  clearSetupError();
  const file = fileInput.files && fileInput.files[0];
  const grade = gradeInput.value;
  const objective = objectiveInput.value;
  const pageNumberText = pageNumberInput.value;
  if (!file) {
    showSetupError("اختر ملف صفحة المصدر أولًا.", fileInput);
    return;
  }
  if (!grade.trim()) {
    showSetupError("أدخل الصف الدراسي.", gradeInput);
    return;
  }
  if (!objective.trim()) {
    showSetupError("أدخل الهدف التعليمي.", objectiveInput);
    return;
  }
  if (!/^\d+$/.test(pageNumberText)
      || Number(pageNumberText) < 1
      || Number(pageNumberText) - 1 > 999999999) {
    showSetupError(
      "أدخل رقم صفحة صحيحًا يبدأ من ١.", pageNumberInput);
    return;
  }
  const pageIndex = Number(pageNumberText) - 1;
  const previousStatus = statusLine.textContent;
  let analysisOperation = null;
  pending = true;
  notifyBusy();
  statusLine.textContent = "جارٍ تجهيز الصفحة وبدء التحليل…";
  try {
    await putSettingsAcknowledged(grade, objective);
    const startResponse = await apiFetch(
      "start", `/api/session/start?page_index=${pageIndex}`, {
        method: "POST",
        headers: mutationHeaders(
          { "Content-Type": "application/octet-stream" }),
        body: file,
      });
    const metadata = await readJsonBody("start", startResponse);
    if (!isUsableMetadata(metadata)) {
      throw new StageError(
        "start", startResponse.status, null, false, true);
    }
    adoptSource(metadata);
    for (const callback of sourceStartedCallbacks) {
      await callback(metadata);
    }
    statusLine.textContent = "جارٍ طلب تحليل المصدر…";
    // The acknowledged Start above is the only destructive step.
    // From here the exact provider operation owns the send, its
    // complete response evidence and logical settlement; snapshot
    // adoption happens inside settlement via the review callbacks.
    analysisOperation = claimProviderOperation(
      "source", metadata.source_id);
    if (!analysisOperation) {
      throw new StageError("analysis", 0, null, false, true);
    }
    const analysisResult = await executeProviderOperation(
      analysisOperation);
    if (analysisResult.type !== "aborted"
        && analysisOperation.generation === uiGeneration
        && !outcomeUncertain
        && committedSourceId === metadata.source_id) {
      statusLine.textContent = analysisResultMessage(analysisResult);
    }
  } catch (error) {
    if (error instanceof StageError && error.stage === "analysis") {
      statusLine.textContent = analysisMessage(error);
    } else if (isUncertainOutcome(error)) {
      enterUncertainOutcome();
    } else if (error instanceof StageError) {
      statusLine.textContent = previousStatus;
      showSetupError(
        setupStageMessage(error),
        error.stage === "start" ? fileInput : null);
    } else {
      enterUncertainOutcome();
    }
  } finally {
    pending = false;
    finishProviderOperation(analysisOperation);
    notifyBusy();
  }
}

// Standalone grade/objective save: the same acknowledged
// settings PUT, without a file, page or destructive Start. It
// never sends Start, source analysis or generation, runs in the
// same serialized lane, and an uncertain write is never healed
// by a read here.
let settingsSaveQueued = false;

async function saveSettingsOnly() {
  // Queued-action checkpoint: sticky identity may have been
  // withdrawn while this save waited in the serialized lane.
  if (pending || outcomeUncertain || !standaloneSettingsPermitted()) {
    return;
  }
  clearSetupError();
  const grade = gradeInput.value;
  const objective = objectiveInput.value;
  if (!grade.trim()) {
    showSetupError("أدخل الصف الدراسي.", gradeInput);
    return;
  }
  if (!objective.trim()) {
    showSetupError("أدخل الهدف التعليمي.", objectiveInput);
    return;
  }
  const previousStatus = statusLine.textContent;
  pending = true;
  notifyBusy();
  statusLine.textContent = "جارٍ حفظ الصف والهدف…";
  try {
    const acknowledged = await putSettingsAcknowledged(
      grade, objective, () => standaloneSettingsPermitted());
    if (!acknowledged) {
      statusLine.textContent = previousStatus;
      return;
    }
    if (!outcomeUncertain) {
      statusLine.textContent = "حُفظ الصف والهدف.";
    }
  } catch (error) {
    if (isUncertainOutcome(error)) {
      enterUncertainOutcome();
    } else if (error instanceof StageError) {
      statusLine.textContent = previousStatus;
      showSetupError(setupStageMessage(error));
    } else {
      enterUncertainOutcome();
    }
  } finally {
    pending = false;
    notifyBusy();
  }
}

if (settingsApplyButton) {
  settingsApplyButton.addEventListener("click", () => {
    if (settingsSaveQueued || outcomeUncertain
        || !standaloneSettingsPermitted()) {
      return;
    }
    settingsSaveQueued = true;
    void runSerialized(() => saveSettingsOnly()).finally(() => {
      settingsSaveQueued = false;
    });
  });
}

form.addEventListener("submit", (event) => {
  event.preventDefault();
  if (startQueued || outcomeUncertain) {
    return;
  }
  startQueued = true;
  void runSerialized(() => startAnalysis()).finally(() => {
    startQueued = false;
  });
});
fileInput.addEventListener("change", updateReplacementWarning);

// Explicit same-source analysis retry: a fresh exact source
// operation only. No settings, no Start, no review reset; the
// settled snapshot adoption appends any new proposals to the
// retained work. Reachable only after the previous source
// operation settled as a refusal or an exact non-success closure.
function handleAnalysisRetry() {
  if (outcomeUncertain || !hasCommittedSource || !committedSourceId
      || !lastSettledOperation
      || lastSettledOperation.kind !== "source"
      || lastSettledOperation.sourceId !== committedSourceId
      || !lastSettledOperation.result
      || lastSettledOperation.result.type === "success"
      || !providerSendPermitted("source", committedSourceId)) {
    return;
  }
  const sourceId = committedSourceId;
  const operation = claimProviderOperation("source", sourceId);
  if (!operation) {
    return;
  }
  void runSerialized(async () => {
    try {
      if (committedSourceId !== sourceId || outcomeUncertain
          || !providerSendPermitted("source", sourceId)) {
        return;
      }
      statusLine.textContent = "جارٍ طلب تحليل المصدر…";
      const result = await executeProviderOperation(operation);
      if (result.type !== "aborted"
          && operation.generation === uiGeneration
          && !outcomeUncertain
          && committedSourceId === sourceId) {
        statusLine.textContent = analysisResultMessage(result);
      }
    } catch (error) {
      if (error instanceof StageError && error.stage === "analysis") {
        statusLine.textContent = analysisMessage(error);
      } else if (isUncertainOutcome(error)) {
        enterUncertainOutcome();
      } else if (error instanceof StageError) {
        statusLine.textContent = setupStageMessage(error);
      } else {
        enterUncertainOutcome();
      }
    } finally {
      finishProviderOperation(operation);
    }
  });
}

operationCancelButton?.addEventListener(
  "click", () => { void cancelActiveOperation(); });
operationCheckButton?.addEventListener(
  "click", () => { void checkActiveOperation(); });
operationRetryButton?.addEventListener(
  "click", handleAnalysisRetry);
renderOperationControls();

// -- Source/Work panes: same nodes at every width ----------------
const narrowQuery = window.matchMedia("(max-width: 1023.98px)");
const panes = { work: workPane, source: sourcePane };
const tabs = { work: workTab, source: sourceTab };
const lastFocus = { work: null, source: null };
const scrollByPane = { work: 0, source: 0 };
let activePane = "work";

for (const name of Object.keys(panes)) {
  panes[name].addEventListener("focusin", (event) => {
    lastFocus[name] = event.target;
  });
}

function syncTabs() {
  sourceTab.setAttribute(
    "aria-selected", String(activePane === "source"));
  workTab.setAttribute(
    "aria-selected", String(activePane === "work"));
}

function showActivePane() {
  if (narrowQuery.matches) {
    workPane.hidden = activePane !== "work";
    sourcePane.hidden = activePane !== "source";
  } else {
    workPane.hidden = false;
    sourcePane.hidden = false;
  }
  syncTabs();
}

// Restoring a pane or crossing a layout boundary keeps logical
// focus, value and selection, but the restored scroll can leave
// the focused control below the viewport or underneath the
// sticky pane switch. This one owner scrolls the window the
// minimum amount that puts the latest focused control back
// inside the usable viewport — below the visible sticky switch
// (measured, never a hardcoded height) and above the viewport
// bottom — without moving focus or touching text/selection. It
// runs only at those boundaries, never on ordinary user scroll,
// and re-reads the active element and pane visibility when the
// post-layout callback runs, so a stale callback cannot chase
// an older focus or scroll a hidden pane.
let focusedVisibilityFrame = 0;

function ensureFocusedControlVisible() {
  const active = document.activeElement;
  if (!active || active === document.body || !active.isConnected
      || (paneSwitch && paneSwitch.contains(active))
      || (workPane.contains(active) && workPane.hidden)
      || (sourcePane.contains(active) && sourcePane.hidden)) {
    return;
  }
  const rect = active.getBoundingClientRect();
  const viewportHeight = window.innerHeight;
  if (!rect.height || !viewportHeight) {
    return;
  }
  let topObstruction = 0;
  if (paneSwitch) {
    const switchRect = paneSwitch.getBoundingClientRect();
    if (switchRect.height > 0 && switchRect.bottom > 0
        && switchRect.top < viewportHeight) {
      topObstruction = Math.min(
        switchRect.bottom, viewportHeight);
    }
  }
  // The painted focus ring is part of what must fit: its outward
  // extent derives from the computed outline width plus offset,
  // clamped to nonnegative (an unpainted outline contributes
  // zero), never a fixed allowance.
  const outlineStyle = window.getComputedStyle(active);
  const outlineWidth = outlineStyle.outlineStyle === "none"
    ? 0 : Number.parseFloat(outlineStyle.outlineWidth) || 0;
  const ringExtent = Math.max(0, outlineWidth > 0
    ? outlineWidth
      + (Number.parseFloat(outlineStyle.outlineOffset) || 0)
    : 0);
  const ringTop = rect.top - ringExtent;
  const ringBottom = rect.bottom + ringExtent;
  const available = viewportHeight - topObstruction;
  if (available <= 0) {
    return;
  }
  // Rounding follows the bound being satisfied: a top alignment
  // floors its delta so the control never overshoots above the
  // usable top, and a bottom correction ceils its delta so the
  // ring never undershoots below the viewport bottom.
  let offset = 0;
  if (ringBottom - ringTop > available) {
    // Too tall to fit whole, ring included: an already
    // meaningful visible start (top below the switch, a portion
    // in view) stays exactly as it is. Otherwise align the top
    // edge just below the switch, whether the target lies above
    // or entirely below; never chase impossible full visibility.
    if (ringTop < topObstruction || ringTop >= viewportHeight) {
      offset = Math.floor(ringTop - topObstruction);
    }
  } else if (ringTop < topObstruction) {
    offset = Math.floor(ringTop - topObstruction);
  } else if (ringBottom > viewportHeight) {
    offset = Math.ceil(ringBottom - viewportHeight);
  }
  if (offset) {
    window.scrollBy(0, offset);
  }
}

function scheduleFocusedControlVisibility() {
  if (focusedVisibilityFrame) {
    return;
  }
  focusedVisibilityFrame = window.requestAnimationFrame(() => {
    focusedVisibilityFrame = 0;
    ensureFocusedControlVisible();
  });
}

function activatePane(name) {
  if (name === activePane) {
    showActivePane();
    return;
  }
  scrollByPane[activePane] = window.scrollY;
  activePane = name;
  showActivePane();
  if (narrowQuery.matches) {
    window.scrollTo(0, scrollByPane[name] || 0);
    const target = lastFocus[name];
    if (target && target.isConnected) {
      target.focus({ preventScroll: true });
    }
  }
  scheduleFocusedControlVisibility();
}

function showSourceEvidence(invokingElement) {
  if (invokingElement && invokingElement.isConnected) {
    lastFocus.work = invokingElement;
  }
  activatePane("source");
  if (sourceHeading) {
    sourceHeading.focus();
  }
}

function showWork() {
  activatePane("work");
}

sourceTab.addEventListener("click", () => activatePane("source"));
workTab.addEventListener("click", () => activatePane("work"));

narrowQuery.addEventListener("change", () => {
  const active = document.activeElement;
  if (narrowQuery.matches) {
    const focusedPane = workPane.contains(active) ? "work"
      : sourcePane.contains(active) ? "source" : null;
    showActivePane();
    if (focusedPane && focusedPane !== activePane) {
      tabs[activePane].focus({ preventScroll: true });
    }
  } else {
    const onTab = active === sourceTab || active === workTab;
    showActivePane();
    if (onTab) {
      const target = lastFocus[activePane];
      if (target && target.isConnected) {
        target.focus({ preventScroll: true });
      }
    }
  }
  scheduleFocusedControlVisibility();
});

// Same-side-of-1024 resizes reflow without any matchMedia change;
// the scheduled check above is frame-throttled and inert unless
// the focused control actually left the usable viewport.
window.addEventListener("resize", scheduleFocusedControlVisibility);

showActivePane();
updateReplacementWarning();
// The one internal interface shared with the review modules.
export const workspace = {
  apiFetch,
  fetchResponse,
  getJson,
  readJsonBody,
  ensureSession,
  mutationHeaders,
  runSerialized,
  isUncertainOutcome,
  enterUncertainOutcome,
  isOutcomeUncertain: () => outcomeUncertain,
  setStatus: (text) => { statusLine.textContent = text; },
  onUncertain: (callback) => { uncertainCallbacks.push(callback); },
  onSourceStarted: (callback) => {
    sourceStartedCallbacks.push(callback);
  },
  onProviderSnapshot: (callback) => {
    providerSnapshotCallbacks.push(callback);
  },
  onProviderSendPermission: (callback) => {
    providerPermissionCallbacks.push(callback);
  },
  onIdentityInvalidatingRefusal: (callback) => {
    identityRefusalCallbacks.push(callback);
  },
  onStandaloneSettingsPermission: (callback) => {
    standaloneSettingsPermissionCallbacks.push(callback);
  },
  refreshStandaloneSettingsControl: () => {
    renderStandaloneSettingsControl();
  },
  outcomePhrase,
  refreshProviderOperationControls: () => {
    renderOperationControls();
  },
  claimProviderOperation,
  executeProviderOperation,
  finishProviderOperation,
  currentGeneration: () => uiGeneration,
  hasActiveProviderOperation: () => activeProviderOperation !== null,
  onSettingsAcknowledged: (callback) => {
    settingsCallbacks.push(callback);
  },
  onBusy: (callback) => { busyCallbacks.push(callback); },
  hasCommittedSource: () => hasCommittedSource,
  currentSourceId: () => committedSourceId,
  isUsableSnapshot,
  isUsableProviderEnvelope,
  showSourceEvidence,
  showWork,
};

ensureSession().catch((error) => {
  if (outcomeUncertain) {
    return;
  }
  if (isUncertainOutcome(error)) {
    enterUncertainOutcome();
    return;
  }
  statusLine.textContent =
    "تعذّر بدء جلسة هذا المتصفح. تحقّق من الخادم المحلي ثم اضغط «بدء تحليل الصفحة» للمحاولة بضغطة جديدة.";
});

