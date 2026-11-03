import {
  initStudioView,
  reflectStudioStage,
  syncStudioSetupView,
} from "./studio.js";
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
const primaryButton = document.querySelector(
  "[data-testid='prepare-page']");
const confirmButton = document.querySelector(
  "[data-testid='confirm-page']");
const preparedPagePanel = document.querySelector(
  "[data-testid='prepared-page']");
const selectedFilePageNote = document.querySelector(
  "[data-testid='selected-file-page-note']");
const setupActionStatus = document.querySelector(
  "[data-testid='setup-action-status']");
const generationActionStatus = document.querySelector(
  "[data-testid='generation-action-status']");
const bootError = document.querySelector(
  "[data-testid='review-boot-error']");
const previewReturnButton = document.querySelector(
  "[data-testid='preview-return']");
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
const workflowStepNames = ["setup", "source", "questions", "preview"];
const workflowBodies = new Map(workflowStepNames.map((step) => [
  step,
  document.querySelector(`[data-workflow-step='${step}']`),
]));
const workflowNavButtons = new Map(workflowStepNames.map((step) => [
  step,
  document.querySelector(`[data-testid='workflow-nav-${step}']`),
]));
const workflowHeadingIds = {
  setup: "setup-heading",
  source: "source-review-heading",
  questions: "question-review-heading",
  preview: "teacher-preview-heading",
};
let activeWorkflowStep = "setup";

let csrfToken = null;
let sessionPromise = null;
// Hosted-session lease disclosure. No lease exists until a real
// create response or a same-origin /api/session response header
// supplies a validated positive idle limit; local runs without
// either keep the pre-hosting behavior exactly. Typing, local
// timers, unsent work and still-pending fetches never renew or
// extend the lease: only receipt of a matching response bearing
// a valid idle header (whatever its HTTP status) does, plus the
// create body's policy anchored to its response receipt.
let sessionExpired = false;
let hostedLeaseSeconds = null;
let hostedLeaseTimer = null;
let hostedLeaseLastWallMs = 0;
let hostedLeaseLastMonoMs = 0;
const sessionExpiredCallbacks = [];
const hostedLeaseReceipts = new WeakMap();

function isSessionExpired() {
  return sessionExpired;
}

function onSessionExpired(callback) {
  sessionExpiredCallbacks.push(callback);
  if (sessionExpired) {
    callback();
  }
}

function isSessionExpiredError(error) {
  return error instanceof StageError
    && error.status === 0
    && error.detail === "session_expired"
    && error.network === false
    && error.unusableBody === false;
}

function sessionExpiredMessage() {
  return "انقضت نافذة تأكيد جلسة الاستضافة، فلم يعد بالإمكان التحقق من حداثة العمل المعروض. يبقى ما هو ظاهر كما هو؛ وإعادة تحميل الصفحة صراحةً تبدأ من جديد وقد تُفقد الأعمال غير المحفوظة.";
}

function usageMessage(detail) {
  const code = typeof detail === "string"
    ? detail
    : detail && typeof detail === "object"
      ? (typeof detail.code === "string" ? detail.code
        : typeof detail.detail === "string" ? detail.detail : "")
      : "";
  const messages = {
    usage_exhausted:
      "بُلغ حدّ الاستخدام المسموح لهذا التشغيل، فلم يُنشأ محتوى جديد من النموذج.",
    usage_window_closed:
      "أُغلقت نافذة الاستخدام المسموحة لهذا التشغيل، فلم يُنشأ محتوى جديد من النموذج.",
    usage_rate_limited:
      "توقّف مؤقت بسبب تقارب الطلبات أكثر من التباعد المسموح حاليًا، فلم يُنشأ محتوى جديد من النموذج الآن؛ لا يعني هذا وحده بلوغ حدّ الاستخدام الكلي.",
    usage_accounting_unavailable:
      "تعذّر التحقق من محاسبة الاستخدام لهذا التشغيل الآن، فلم يُنشأ محتوى جديد من النموذج؛ لا تؤكد هذه الرسالة بلوغ حدّ الاستخدام أو عدم بلوغه.",
  };
  return Object.prototype.hasOwnProperty.call(messages, code)
    ? messages[code] : null;
}

function validHostedLeaseSeconds(value) {
  const seconds = typeof value === "number"
    ? value
    : typeof value === "string" && value.trim() !== ""
      ? Number(value) : NaN;
  return Number.isFinite(seconds) && seconds > 0 ? seconds : null;
}

function isHostedSessionPath(url) {
  try {
    const parsed = new URL(String(url), window.location.href);
    return parsed.origin === window.location.origin
      && (parsed.pathname === "/api/sessions"
        || parsed.pathname === "/api/session"
        || parsed.pathname.startsWith("/api/session/"));
  } catch {
    return false;
  }
}

function formatHostedLeaseDuration(seconds) {
  if (seconds >= 60 && seconds % 60 === 0) {
    const minutes = seconds / 60;
    if (minutes === 1) {
      return "دقيقة واحدة";
    }
    if (minutes === 2) {
      return "دقيقتين";
    }
    return minutes <= 10
      ? `${minutes} دقائق` : `${minutes} دقيقة`;
  }
  if (seconds === 1) {
    return "ثانية واحدة";
  }
  if (seconds === 2) {
    return "ثانيتين";
  }
  return seconds > 2 && seconds <= 10
    ? `${seconds} ثوانٍ` : `${seconds} ثانية`;
}

function hostedLeaseElapsedMs() {
  // Conservative for sleep/resume and wall-clock jumps: whichever
  // clock has advanced further is the elapsed idle time.
  return Math.max(
    Date.now() - hostedLeaseLastWallMs,
    performance.now() - hostedLeaseLastMonoMs);
}

function clearHostedLeaseTimer() {
  if (hostedLeaseTimer !== null) {
    clearTimeout(hostedLeaseTimer);
    hostedLeaseTimer = null;
  }
}

function scheduleHostedLeaseTimer() {
  clearHostedLeaseTimer();
  if (sessionExpired || hostedLeaseSeconds === null) {
    return;
  }
  const remainingMs = hostedLeaseSeconds * 1000
    - hostedLeaseElapsedMs();
  hostedLeaseTimer = setTimeout(
    checkHostedLeaseExpiry, Math.max(0, remainingMs));
}

function checkHostedLeaseExpiry() {
  if (sessionExpired) {
    return true;
  }
  if (hostedLeaseSeconds === null) {
    return false;
  }
  if (hostedLeaseElapsedMs() < hostedLeaseSeconds * 1000) {
    scheduleHostedLeaseTimer();
    return false;
  }
  // A still-pending fetch proves nothing about server ownership,
  // so it never defers withdrawal past the confirmed deadline.
  expireHostedSession();
  return true;
}

function renderHostedSessionNotice() {
  const notice = document.querySelector(
    '[data-testid="hosted-session-notice"]');
  if (!notice || hostedLeaseSeconds === null) {
    return;
  }
  notice.textContent =
    `عمل مؤقت في جلسة مستضافة: تنتهي الجلسة بعد ${formatHostedLeaseDuration(hostedLeaseSeconds)} دون وصول أي طلب إلى الخادم؛ الكتابة دون إرسال طلب لا تجدّد الجلسة.`;
  notice.hidden = false;
}

function hostedLeaseReceipt(response) {
  return response
    && (typeof response === "object"
      || typeof response === "function")
    ? hostedLeaseReceipts.get(response) : undefined;
}

function renewHostedLease(receipt) {
  if (sessionExpired || hostedLeaseSeconds === null) {
    return;
  }
  hostedLeaseLastWallMs = receipt ? receipt.wallMs : Date.now();
  hostedLeaseLastMonoMs = receipt
    ? receipt.monoMs : performance.now();
  renderHostedSessionNotice();
  scheduleHostedLeaseTimer();
}

function adoptHostedLeaseFromSessionCreate(data, response) {
  if (!data || typeof data !== "object" || sessionExpired) {
    return;
  }
  const seconds = validHostedLeaseSeconds(data.session_idle_seconds);
  if (seconds === null) {
    return;
  }
  if (checkHostedLeaseExpiry()) {
    return;
  }
  // Anchor to the create Response receipt recorded by
  // sessionFetch, never to this later body-parsing moment. The
  // closing check withdraws at once if that receipt's window
  // already elapsed while the body was being read.
  hostedLeaseSeconds = seconds;
  renewHostedLease(hostedLeaseReceipt(response));
  checkHostedLeaseExpiry();
}

function noteHostedLeaseResponse(response) {
  // The previous confirmed deadline is checked first, so a late
  // response can never renew over an already elapsed window or
  // heal sticky expiry. A valid header then renews whatever the
  // HTTP status; a missing or invalid header renews nothing.
  if (!response || checkHostedLeaseExpiry()) {
    return;
  }
  const headerSeconds = validHostedLeaseSeconds(
    response.headers && typeof response.headers.get === "function"
      ? response.headers.get("X-Session-Idle-Seconds") : null);
  if (headerSeconds === null) {
    return;
  }
  hostedLeaseSeconds = headerSeconds;
  renewHostedLease(hostedLeaseReceipt(response));
}

function expireHostedSession() {
  if (sessionExpired || hostedLeaseSeconds === null) {
    return;
  }
  // Sticky until reload: no new session is created here, the CSRF
  // token and all displayed work stay exactly as they are, and no
  // uncertain-outcome state is entered (that would hide source).
  sessionExpired = true;
  clearHostedLeaseTimer();
  const expiredNotice = document.querySelector(
    '[data-testid="hosted-session-expired"]');
  if (expiredNotice) {
    expiredNotice.textContent = sessionExpiredMessage();
    expiredNotice.hidden = false;
  }
  notifyBusy();
  for (const callback of [...sessionExpiredCallbacks]) {
    callback();
  }
}

async function sessionFetch(url, options = {}, stage = "session") {
  const method = String(options.method || "GET").toUpperCase();
  if (method !== "GET" && method !== "HEAD"
      && checkHostedLeaseExpiry()) {
    throw new StageError(stage, 0, "session_expired", false);
  }
  const matching = isHostedSessionPath(url);
  try {
    const response = await fetch(url, options);
    if (matching) {
      hostedLeaseReceipts.set(response, {
        wallMs: Date.now(),
        monoMs: performance.now(),
      });
      noteHostedLeaseResponse(response);
    }
    return response;
  } finally {
    if (matching) {
      checkHostedLeaseExpiry();
    }
  }
}

document.addEventListener("visibilitychange", () => {
  if (!document.hidden) {
    checkHostedLeaseExpiry();
  }
});
window.addEventListener("pageshow", () => {
  checkHostedLeaseExpiry();
});

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
let reviewReady = false;
let reviewBootFailed = false;
let setupInputRevision = 0;
let preparationSequence = 0;
let pendingConfirmation = null;
let initialAnalysisAttempted = false;
let workflowAvailability = {
  source: false,
  questions: false,
  preview: false,
};

function setWorkflowAvailability(next) {
  workflowAvailability = {
    source: Boolean(next?.source),
    questions: Boolean(next?.questions),
    preview: Boolean(next?.preview),
  };
  renderWorkflowStep();
}

function handleConfirmPage() {
  const candidate = pendingConfirmation;
  if (!candidate || candidate.state !== "ready"
      || !pendingConfirmationCurrent(candidate)
      || !pendingPreviewMatches(candidate)
      || !previewImage.complete || previewImage.naturalWidth <= 0
      || !hasCommittedSource
      || candidate.sourceId !== committedSourceId
      || outcomeUncertain || checkHostedLeaseExpiry()
      || !providerSendPermitted("source", candidate.sourceId)) {
    return;
  }
  const sourceId = candidate.sourceId;
  const operation = claimProviderOperation("source", sourceId);
  if (!operation) {
    return;
  }
  initialAnalysisAttempted = true;
  operation.beforeSend = () => Boolean(
    candidate === pendingConfirmation
    && candidate.state === "ready"
    && candidate.sourceId === committedSourceId
    && candidate.sourceId === operation.sourceId
    && candidate.token === preparationSequence
    && setupInputsUnchanged(candidate)
    && pendingPreviewMatches(candidate)
    && previewImage.complete && previewImage.naturalWidth > 0
    && !outcomeUncertain
    && !checkHostedLeaseExpiry()
    && providerSendPermitted("source", candidate.sourceId));
  void runSerialized(async () => {
    try {
      if (candidate !== pendingConfirmation
          || candidate.state !== "ready"
          || candidate.sourceId !== committedSourceId
          || candidate.token !== preparationSequence
          || !setupInputsUnchanged(candidate)
          || !pendingPreviewMatches(candidate)
          || !previewImage.complete
          || previewImage.naturalWidth <= 0
          || outcomeUncertain || checkHostedLeaseExpiry()
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


function setSetupActionStatus(text) {
  if (setupActionStatus) {
    setupActionStatus.textContent = text;
  }
}

function reviewRegistrationsComplete() {
  return sourceStartedCallbacks.length > 0
    && providerSnapshotCallbacks.length > 0
    && providerPermissionCallbacks.length > 0
    && settingsCallbacks.length > 0
    && standaloneSettingsPermissionCallbacks.length > 0
    && identityRefusalCallbacks.length > 0
    && busyCallbacks.length > 0
    && uncertainCallbacks.length > 0
    && sessionExpiredCallbacks.length > 0;
}

function showBootReadyIfPossible() {
  if (!reviewReady || !csrfToken || outcomeUncertain
      || isSessionExpired()) {
    return;
  }
  statusLine.textContent =
    "أكمل إعداد الصفحة ثم اضغط «تهيئة الصفحة».";
  setSetupActionStatus(
    "أدخل البيانات واختر صفحة ملف واحدة، ثم اضغط «تهيئة الصفحة».");
}

function renderWorkflowStep() {
  const focused = document.activeElement;
  workPane.dataset.activeStep = activeWorkflowStep;
  workPane.setAttribute(
    "aria-labelledby", workflowHeadingIds[activeWorkflowStep]);
  for (const step of workflowStepNames) {
    const body = workflowBodies.get(step);
    if (body) {
      body.hidden = step !== activeWorkflowStep;
    }
    const button = workflowNavButtons.get(step);
    if (!button) {
      continue;
    }
    const current = step === activeWorkflowStep;
    button.disabled = step !== "setup"
      && !workflowAvailability[step];
    if (current) {
      button.setAttribute("aria-current", "step");
    } else {
      button.removeAttribute("aria-current");
    }
    button.closest(".stage")?.classList.toggle(
      "stage-current", current);
  }
  if (focused && focused !== document.body) {
    for (const step of workflowStepNames) {
      const body = workflowBodies.get(step);
      if (step !== activeWorkflowStep && body?.contains(focused)) {
        workflowNavButtons.get(activeWorkflowStep)
          ?.focus({ preventScroll: true });
        break;
      }
    }
  }
  reflectStudioStage(activeWorkflowStep);
}

function selectWorkflowStep(step) {
  if (!workflowBodies.has(step)) {
    return;
  }
  activeWorkflowStep = step;
  renderWorkflowStep();
  showWork();
}

function revealSourceReview() {
  selectWorkflowStep("source");
  showWork();
  const heading = document.querySelector(
      "[data-testid='region-list'] [data-testid='region-heading']")
    || document.getElementById("source-review-heading");
  if (!heading) {
    return;
  }
  heading.scrollIntoView({ block: "center", inline: "nearest" });
  heading.focus({ preventScroll: true });
  ensureFocusedControlVisible();
}

function captureSetupInputs() {
  return {
    revision: setupInputRevision,
    file: fileInput.files && fileInput.files[0]
      ? fileInput.files[0] : null,
    grade: gradeInput.value,
    objective: objectiveInput.value,
    pageNumberText: pageNumberInput.value,
  };
}

function setupInputsUnchanged(captured) {
  if (!captured) {
    return false;
  }
  const current = captureSetupInputs();
  return current.revision === captured.revision
    && current.file === captured.file
    && current.grade === captured.grade
    && current.objective === captured.objective
    && current.pageNumberText === captured.pageNumberText;
}

function pendingConfirmationCurrent(candidate = pendingConfirmation) {
  return Boolean(candidate
    && candidate === pendingConfirmation
    && !initialAnalysisAttempted
    && candidate.sourceId === committedSourceId
    && setupInputsUnchanged(candidate));
}

function renderPendingConfirmation() {
  const candidate = pendingConfirmation;
  const busy = queuedMutations > 0 || pending;
  if (preparedPagePanel) {
    preparedPagePanel.hidden = !candidate;
  }
  if (!confirmButton) {
    syncStudioSetupView();
    return;
  }
  confirmButton.hidden = !candidate;
  confirmButton.disabled = !candidate
    || candidate.state !== "ready"
    || !pendingConfirmationCurrent(candidate)
    || busy || outcomeUncertain || isSessionExpired()
    || !reviewReady
    || !providerSendPermitted("source", candidate.sourceId);
  syncStudioSetupView();
}

function invalidatePendingConfirmation(message) {
  if (!pendingConfirmation) {
    return;
  }
  pendingConfirmation = null;
  if (message) {
    setSetupActionStatus(message);
  }
  renderPendingConfirmation();
}

// Standalone settings permission is owned by the review
// coordinator's one currentness authority and only queried here
// (its sticky-identity reason); this module keeps no second
// currentness flag. A settings-reason withdrawal still permits
// the deliberate correction send, and Start never asks this.
function standaloneSettingsPermitted() {
  return reviewReady
    && standaloneSettingsPermissionCallbacks.length > 0
    && !isSessionExpired()
    && standaloneSettingsPermissionCallbacks.every(
      (callback) => callback() === true);
}


function renderStandaloneSettingsControl() {
  if (!settingsApplyButton) {
    return;
  }
  const busy = queuedMutations > 0 || pending;
  settingsApplyButton.disabled = outcomeUncertain || busy
    || isSessionExpired() || !standaloneSettingsPermitted();
}


function notifyBusy() {
  const busy = queuedMutations > 0 || pending;
  primaryButton.disabled = !reviewReady || outcomeUncertain || busy
    || isSessionExpired();
  if (previewReturnButton) {
    previewReturnButton.disabled = !reviewReady;
  }
  renderPendingConfirmation();
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
      unusableBody = false, exactTypedBodyRefusal = false) {
    super("request failed");
    this.stage = stage;
    this.status = status;
    this.detail = detail;
    this.network = network;
    this.unusableBody = unusableBody;
    // Exact-envelope certificate derived once from the actual
    // error response body (see readDetail). Manually built
    // errors default to false and never gain it from detail.
    this.exactTypedBodyRefusal = exactTypedBodyRefusal === true;
  }
}

// Exact before-body refusal certificates: the HTTP status, the
// detail code and admission:"not_started" must match as one pair.
// The detail may carry only code/admission plus the one fixed
// message for that code; any other field, code or admission
// proves nothing. The inner classifier below never certifies an
// ordinary no-write by itself: ordinary handling also requires
// the exact {detail} envelope certificate carried on StageError
// (isExactTypedBodyRefusalEnvelope). The provider lane verifies
// the same envelope in isKnownPreAdmissionRefusal first.
const TYPED_BODY_REFUSALS = {
  400: {
    code: "request_body_timeout",
    message: "request body timeout",
  },
  429: {
    code: "body_reader_capacity_exceeded",
    message: "body reader capacity exceeded",
  },
};

function typedBodyRefusalCode(status, detail) {
  const expected = TYPED_BODY_REFUSALS[status];
  if (!expected || !detail || typeof detail !== "object"
      || Array.isArray(detail)) {
    return null;
  }
  const onlyCertificateFields = Object.keys(detail).every(
    (key) => key === "code" || key === "admission"
      || key === "message");
  if (!onlyCertificateFields
      || detail.code !== expected.code
      || detail.admission !== "not_started"
      || ("message" in detail
        && detail.message !== expected.message)) {
    return null;
  }
  return expected.code;
}

function isTypedBodyRefusal(status, detail) {
  return typedBodyRefusalCode(status, detail) !== null;
}

// Exact root-envelope check over a successfully parsed body:
// only a non-array object whose sole key is "detail", with the
// exact inner certificate above. Any array, null, unrelated or
// operation field (snapshot/request_id/raw_retention) fails.
function isExactTypedBodyRefusalEnvelope(status, body) {
  if (!body || typeof body !== "object" || Array.isArray(body)
      || Object.keys(body).length !== 1 || !("detail" in body)) {
    return false;
  }
  return isTypedBodyRefusal(status, body.detail);
}

// Callers must already hold exact-envelope proof: ordinary
// callers gate on StageError.exactTypedBodyRefusal, the provider
// lane on isKnownPreAdmissionRefusal. Copy certifies only this
// attempt; it makes no currentness or preview promise.
function typedBodyRefusalMessage(status, detail) {
  const code = typedBodyRefusalCode(status, detail);
  if (code === "request_body_timeout") {
    return "لم يصل هذا الرفع أو الأمر كاملًا خلال الوقت المسموح، فلم يُطبَّق ولم يبدأ طلب في هذه المحاولة. حاول مجددًا بضغطة جديدة عندما تكون جاهزًا.";
  }
  if (code === "body_reader_capacity_exceeded") {
    return "سعة استقبال الطلبات مشغولة الآن، فلم يُطبَّق هذا الرفع أو الأمر ولم يبدأ طلب في هذه المحاولة. انتظر قليلًا ثم حاول مجددًا بضغطة جديدة.";
  }
  return null;
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
  // A locally refused no-send is definite: nothing was sent.
  if (isSessionExpiredError(error)) {
    return false;
  }
  if (error.network || error.unusableBody) {
    return true;
  }
  // Only the exact-envelope certificate derived from the actual
  // response proves this attempt started no request. Truncated
  // detail alone, a general 400 or an unknown/wrong-code 429
  // earns nothing here and keeps the legacy stage behavior below.
  if (error.exactTypedBodyRefusal === true
      && isTypedBodyRefusal(error.status, error.detail)) {
    return false;
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
  pendingConfirmation = null;
  renderPendingConfirmation();
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
  showSetupError(UNKNOWN_STATUS);
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

// Reads an error body once and derives the exact-envelope
// certificate from the complete parsed shape before discarding
// it; only detail plus the boolean leave this function. A parse
// failure yields no certificate and never infers one later.
async function readDetail(response) {
  let body;
  try {
    body = await response.clone().json();
  } catch {
    return { detail: null, exactTypedBodyRefusal: false };
  }
  const detail = body && typeof body === "object"
    ? body.detail ?? null : null;
  return {
    detail,
    exactTypedBodyRefusal: isExactTypedBodyRefusalEnvelope(
      response.status, body),
  };
}

async function readJsonBody(stage, response) {
  let result;
  try {
    result = await response.json();
  } catch {
    throw new StageError(stage, response.status, null, false, true);
  }
  // A body can complete after the confirmed deadline while the
  // expiry timer has not run yet. Evaluate the deadline now so
  // sticky withdrawal precedes any callback or snapshot
  // adoption. The successfully read body itself stays the known
  // outcome: expiry never turns it into a no-send/network error.
  checkHostedLeaseExpiry();
  return result;
}

async function apiFetch(stage, url, options = {}) {
  const method = String(options.method || "GET").toUpperCase();
  if (method !== "GET" && method !== "HEAD"
      && checkHostedLeaseExpiry()) {
    throw new StageError(stage, 0, "session_expired", false);
  }
  const sourceIdAtSend = committedSourceId;
  const generationAtSend = uiGeneration;
  let response;
  try {
    response = await sessionFetch(
      url, { credentials: "same-origin", ...options }, stage);
  } catch (error) {
    if (isSessionExpiredError(error)) {
      throw error;
    }
    throw new StageError(stage, 0, null, true);
  }
  if (!response.ok) {
    const { detail, exactTypedBodyRefusal } =
      await readDetail(response);
    notifyOrdinaryIdentityRefusal(
      stage, response.status, detail, sourceIdAtSend,
      generationAtSend);
    // network/unusableBody stay explicitly false for a received
    // error response: parse readability lives only in the
    // certificate, so legacy definite refusals keep deciding
    // every non-certified error exactly as before.
    throw new StageError(
      stage, response.status, detail, false, false,
      exactTypedBodyRefusal);
  }
  return response;
}


async function fetchResponse(stage, url, options = {}) {
  const method = String(options.method || "GET").toUpperCase();
  if (method !== "GET" && method !== "HEAD"
      && checkHostedLeaseExpiry()) {
    throw new StageError(stage, 0, "session_expired", false);
  }
  try {
    return await sessionFetch(
      url, { credentials: "same-origin", ...options }, stage);
  } catch (error) {
    if (isSessionExpiredError(error)) {
      throw error;
    }
    throw new StageError(stage, 0, null, true);
  }
}


async function getJson(path) {
  const response = await sessionFetch(path, { credentials: "same-origin" });
  if (!response.ok) {
    throw new Error("readback failed");
  }
  const result = await response.json();
  // Same body boundary as readJsonBody: evaluate the confirmed
  // deadline after the successful parse so sticky withdrawal
  // precedes caller adoption, while the body itself is returned
  // unchanged as the known readback.
  checkHostedLeaseExpiry();
  return result;
}


function ensureSession() {
  if (sessionExpired) {
    return Promise.reject(
      new StageError("session", 0, "session_expired", false));
  }
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
      adoptHostedLeaseFromSessionCreate(data, response);
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

const SETUP_ERROR_ID = "setup-error";
let setupErrorHomeParent = null;
let setupErrorHomeNext = null;
let setupErrorCulprit = null;
let setupErrorCulpritInvalid = null;

function ensureSetupErrorId() {
  if (!setupError.id) {
    setupError.id = SETUP_ERROR_ID;
  }
  return setupError.id;
}

function captureSetupErrorHome() {
  if (!setupErrorHomeParent && setupError.parentNode) {
    setupErrorHomeParent = setupError.parentNode;
    setupErrorHomeNext = setupError.nextSibling;
  }
}

function placeSetupErrorHome() {
  captureSetupErrorHome();
  if (!setupErrorHomeParent
      || (setupError.parentNode === setupErrorHomeParent
        && setupError.nextSibling === setupErrorHomeNext)) {
    return;
  }
  const next = setupErrorHomeNext
      && setupErrorHomeNext.parentNode === setupErrorHomeParent
    ? setupErrorHomeNext : null;
  setupErrorHomeParent.insertBefore(setupError, next);
}

function placeSetupErrorForField(target) {
  const wrapper = typeof target.closest === "function"
    ? target.closest(".field") || target.parentElement
    : target.parentElement;
  if (!wrapper) {
    placeSetupErrorHome();
    return;
  }
  let anchor = target;
  while (anchor.parentElement && anchor.parentElement !== wrapper) {
    anchor = anchor.parentElement;
  }
  wrapper.insertBefore(setupError, anchor.nextSibling);
}

function detachSetupErrorCulprit() {
  const culprit = setupErrorCulprit;
  if (!culprit) {
    return;
  }
  setupErrorCulprit = null;
  const describedBy = (culprit.getAttribute("aria-describedby") || "")
    .split(/\s+/)
    .filter((token) => token && token !== setupError.id);
  if (describedBy.length) {
    culprit.setAttribute("aria-describedby", describedBy.join(" "));
  } else {
    culprit.removeAttribute("aria-describedby");
  }
  if (culprit.getAttribute("aria-invalid") === "true") {
    if (setupErrorCulpritInvalid === null) {
      culprit.removeAttribute("aria-invalid");
    } else {
      culprit.setAttribute("aria-invalid", setupErrorCulpritInvalid);
    }
  }
  setupErrorCulpritInvalid = null;
}

function attachSetupErrorCulprit(target) {
  const errorId = ensureSetupErrorId();
  setupErrorCulprit = target;
  setupErrorCulpritInvalid = target.getAttribute("aria-invalid");
  target.setAttribute("aria-invalid", "true");
  const describedBy = (target.getAttribute("aria-describedby") || "")
    .split(/\s+/).filter(Boolean);
  if (!describedBy.includes(errorId)) {
    describedBy.push(errorId);
  }
  target.setAttribute("aria-describedby", describedBy.join(" "));
}

function showSetupError(message, focusTarget = null) {
  if (!setupError) {
    return;
  }
  ensureSetupErrorId();
  captureSetupErrorHome();
  detachSetupErrorCulprit();
  if (focusTarget) {
    placeSetupErrorForField(focusTarget);
    attachSetupErrorCulprit(focusTarget);
  } else {
    placeSetupErrorHome();
  }
  setupError.textContent = message;
  setupError.hidden = false;
  if (focusTarget) {
    focusTarget.focus();
  }
}

function clearSetupError() {
  if (!setupError) {
    return;
  }
  detachSetupErrorCulprit();
  setupError.textContent = "";
  setupError.hidden = true;
  placeSetupErrorHome();
}

function setupCulpritCorrected(target) {
  if (target === fileInput) {
    return Boolean(target.files && target.files.length);
  }
  if (target === pageNumberInput) {
    return /^\d+$/.test(target.value)
      && Number(target.value) >= 1
      && Number(target.value) - 1 <= 999999999;
  }
  return typeof target.value === "string" && target.value.trim() !== "";
}

if (setupError) {
  ensureSetupErrorId();
}

for (const setupField of [fileInput, gradeInput, objectiveInput,
    pageNumberInput]) {
  if (!setupField) {
    continue;
  }
  const clearCorrectedSetupError = () => {
    if (setupErrorCulprit === setupField
        && setupCulpritCorrected(setupField)) {
      clearSetupError();
    }
  };
  setupField.addEventListener("input", clearCorrectedSetupError);
  setupField.addEventListener("change", clearCorrectedSetupError);
}

function setupStageMessage(error) {
  if (isSessionExpiredError(error)) {
    return sessionExpiredMessage();
  }
  if (error.exactTypedBodyRefusal === true
      && !error.network && !error.unusableBody) {
    const bodyRefusal = typedBodyRefusalMessage(
      error.status, error.detail);
    if (bodyRefusal) {
      return bodyRefusal;
    }
  }
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
      return "تغيّرت الإعدادات أثناء تجهيز الصفحة ولم يُعتمد هذا البدء. لم يتغيّر المصدر الحالي؛ راجع الإعدادات ثم اضغط «تهيئة الصفحة» مجددًا.";
    }
  }
  return "تعذّر إكمال هذه الخطوة. لم نُعد المحاولة تلقائيًا؛ راجع ما هو ظاهر ثم حاول مجددًا بضغطة جديدة.";
}

function analysisMessage(error) {
  if (isSessionExpiredError(error)) {
    return sessionExpiredMessage();
  }
  if (error.network) {
    return "تعذّر الاتصال بالخادم المحلي أثناء طلب التحليل. لم نُعد المحاولة تلقائيًا؛ تبقى الصفحة المعلنة كما هي، وحالة طلب التحليل نفسه غير مؤكدة.";
  }
  if (error.exactTypedBodyRefusal === true
      && !error.unusableBody) {
    const bodyRefusal = typedBodyRefusalMessage(
      error.status, error.detail);
    if (bodyRefusal) {
      return bodyRefusal;
    }
  }
  if (error.status === 401 || error.status === 403) {
    return SESSION_MESSAGE;
  }
  const code = detailCode(error);
  if (error.status === 503 && code === "provider_unavailable") {
    return UNAVAILABLE_MESSAGE;
  }
  const usage = error.unusableBody ? null : usageMessage(error.detail);
  if (usage) {
    return usage;
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

function closedOperationStatus(outcome, detail = null) {
  if (outcome === "cancelled") {
    return OPERATION_CANCELLED;
  }
  // Detail reaches here only from the validated exact terminal
  // envelope, so a closed usage code names the budget cause
  // before the generic outcome phrase. Never inferred from the
  // HTTP status or the outcome alone.
  const usage = usageMessage(detail);
  if (usage) {
    return `أُغلق الطلب. ${usage} الحالة المعروضة هي آخر حالة مؤكدة من الخادم.`;
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
  if (status === 413) {
    return Boolean(detail && typeof detail === "object"
      && detail.code === "retained_capacity_exceeded"
      && detail.admission === "not_started");
  }
  // Typed before-body certificates, through the one shared exact
  // status+code+admission classifier. The body above already had
  // to be only {detail}, so a body carrying snapshot, request_id
  // or raw_retention never reaches this proof; a general 400, an
  // unknown 429 or a generic timeout detail matches nothing.
  if (isTypedBodyRefusal(status, detail)) {
    return true;
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
  return reviewReady
    && providerSnapshotCallbacks.length > 0
    && providerPermissionCallbacks.length > 0
    && providerPermissionCallbacks.every(
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
  if (operation.kind === "source") {
    setSetupActionStatus(text);
  } else if (operation.kind === "questions"
      && generationActionStatus) {
    generationActionStatus.textContent = text;
  }
  renderOperationControls();
}

function markOperationUnresolved(operation, text) {
  if (!operationOwnsUi(operation)) {
    return;
  }
  const effectiveText = operation.closureKnown
    ? OPERATION_UNRECONCILED : text;
  setOperationStatus(operation, effectiveText);
  statusLine.textContent = effectiveText;
}

function renderOperationControls() {
  if (!operationGroup || !operationStatus) {
    return;
  }
  const display = activeProviderOperation || lastSettledOperation;
  if (!display || outcomeUncertain) {
    operationGroup.hidden = true;
    delete operationGroup.dataset.displayState;
    return;
  }
  operationGroup.hidden = false;
  operationStatus.textContent = display.statusText || "";
  const expired = isSessionExpired();
  const active = display === activeProviderOperation && !display.settled;
  operationGroup.dataset.displayState = active ? "active"
    : display.settled && display.result
      && display.result.type === "success" && !expired
      ? "settled-success" : "attention";
  const commonDisabled = !active || !display.sent || expired;
  if (operationCancelButton) {
    operationCancelButton.hidden = !active;
    operationCancelButton.disabled = commonDisabled
      || display.cancelInFlight;
  }
  if (operationCheckButton) {
    operationCheckButton.hidden = !active;
    operationCheckButton.disabled = commonDisabled
      || display.checkInFlight;
  }
  const canRetry = Boolean(!expired
    && !activeProviderOperation
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
  if (!reviewReady || providerSnapshotCallbacks.length === 0
      || (kind !== "source" && kind !== "questions") || !sourceId
      || outcomeUncertain || activeProviderOperation
      || sourceId !== committedSourceId
      || checkHostedLeaseExpiry()) {
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
    checkInFlight: false,
    cancelInFlight: false,
    reconciling: false,
    beforeSend: null,
    progressRevealed: false,
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
    if (checkHostedLeaseExpiry()) {
      const succeeded = evidence.outcome === "ok";
      let lateStatus;
      if (succeeded) {
        lateStatus =
          "اكتمل الطلب، لكن لم تُطبَّق نتيجته على هذا العرض.";
      } else if (evidence.outcome === "cancelled") {
        lateStatus = OPERATION_CANCELLED;
      } else {
        const usage = usageMessage(evidence.detail ?? null);
        const phrase = outcomePhrase(evidence.outcome);
        lateStatus = usage
          ? `أُغلق الطلب. ${usage} لم تُطبَّق نتيجته على هذا العرض.`
          : phrase
            ? `أُغلق الطلب: ${phrase}؛ لم تُطبَّق نتيجته على هذا العرض.`
            : "أُغلق الطلب دون اكتمال ناجح؛ لم تُطبَّق نتيجته على هذا العرض.";
      }
      settleOperation(operation, {
        type: succeeded ? "success" : "terminal",
        outcome: evidence.outcome,
        snapshot: evidence.snapshot,
        httpStatus: evidence.httpStatus ?? 0,
        detail: evidence.detail ?? null,
      }, lateStatus);
      return true;
    }
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
      : closedOperationStatus(
          evidence.outcome, evidence.detail ?? null));
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
      const refusalDetail = detailOfBody(body);
      const refusalStatus = typedBodyRefusalMessage(
          status, refusalDetail)
        ?? (status === 413
          && refusalDetail && typeof refusalDetail === "object"
          && refusalDetail.code === "retained_capacity_exceeded"
          && refusalDetail.admission === "not_started"
          ? "تعذّر قبول هذا الإرسال ضمن حدود مساحة العمل؛ لم يبدأ طلب لدى المزوّد في هذه المحاولة."
          : OPERATION_REFUSED);
      settleOperation(operation, {
        type: "refusal",
        outcome: null,
        snapshot: null,
        httpStatus: status,
        detail: detailOfBody(body),
        // Derived here from the complete body so later message
        // reconstruction never re-infers it from detail alone.
        exactTypedBodyRefusal: isExactTypedBodyRefusalEnvelope(
          status, body),
      }, refusalStatus);
      return;
    }
  }
  markOperationUnresolved(operation, OPERATION_UNCONFIRMED);
}

function revealProviderOperation(operation) {
  if (!operationGroup || !operation
      || operation !== activeProviderOperation
      || operation.progressRevealed || operationGroup.hidden) {
    return;
  }
  operation.progressRevealed = true;
  operationGroup.scrollIntoView({
    block: "center", inline: "nearest" });
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
      || operation.sourceId !== committedSourceId
      || checkHostedLeaseExpiry()) {
    return { type: "aborted" };
  }
  try {
    await ensureSession();
  } catch (error) {
    if (isSessionExpiredError(error)) {
      return { type: "aborted" };
    }
    throw error;
  }
  if (operation !== activeProviderOperation || operation.settled
      || operation.sent || outcomeUncertain
      || operation.sourceId !== committedSourceId
      || checkHostedLeaseExpiry()
      || !providerSendPermitted(
        operation.kind, operation.sourceId)
      || (operation.beforeSend
        && operation.beforeSend() !== true)) {
    return { type: "aborted" };
  }
  operation.generation = uiGeneration;
  operation.sent = true;
  setOperationStatus(operation, operation.kind === "source"
    ? "جارٍ طلب تحليل المصدر وانتظار رد المزوّد…"
    : "جارٍ طلب توليد الأسئلة وانتظار رد المزوّد…");
  revealProviderOperation(operation);
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
    } catch (error) {
      if (isSessionExpiredError(error)) {
        if (!operation.settled) {
          operation.sent = false;
          setOperationStatus(operation, sessionExpiredMessage());
          operation.resolveLogical({ type: "aborted" });
        }
        return;
      }
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
      || operation.checkInFlight || outcomeUncertain
      || checkHostedLeaseExpiry()
      || operation.sourceId !== committedSourceId
      || operation.generation !== uiGeneration) {
    return;
  }
  operation.checkInFlight = true;
  setOperationStatus(operation, "جارٍ التحقق من حالة الطلب…");
  try {
    if (operation.closureKnown) {
      await reconcileFromSnapshotRead(operation);
      return;
    }
    await ensureSession();
    if (checkHostedLeaseExpiry() || !operationOwnsUi(operation)) {
      return;
    }
    let response;
    try {
      response = await fetchResponse("provider-status",
        `/api/session/requests/${encodeURIComponent(operation.requestId)}/status?source_id=${encodeURIComponent(operation.sourceId)}`);
    } catch (error) {
      if (!isSessionExpiredError(error)) {
        markOperationUnresolved(operation, OPERATION_UNCONFIRMED);
      }
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
  } catch (error) {
    if (!isSessionExpiredError(error) && operationOwnsUi(operation)) {
      markOperationUnresolved(operation, OPERATION_UNCONFIRMED);
    }
  } finally {
    operation.checkInFlight = false;
    renderOperationControls();
  }
}


async function cancelActiveOperation() {
  const operation = activeProviderOperation;
  if (!operation || operation.settled || !operation.sent
      || operation.cancelInFlight || outcomeUncertain
      || checkHostedLeaseExpiry()
      || operation.sourceId !== committedSourceId
      || operation.generation !== uiGeneration) {
    return;
  }
  operation.cancelInFlight = true;
  setOperationStatus(operation, "جارٍ طلب إلغاء الطلب…");
  try {
    await ensureSession();
    if (checkHostedLeaseExpiry() || !operationOwnsUi(operation)) {
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
    } catch (error) {
      if (!isSessionExpiredError(error)) {
        markOperationUnresolved(operation, OPERATION_UNCONFIRMED);
      }
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
  } catch (error) {
    if (!isSessionExpiredError(error) && operationOwnsUi(operation)) {
      markOperationUnresolved(operation, OPERATION_UNCONFIRMED);
    }
  } finally {
    operation.cancelInFlight = false;
    renderOperationControls();
  }
}


function adoptSource(metadata) {
  uiGeneration += 1;
  initialAnalysisAttempted = false;
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

function pendingPreviewMatches(candidate) {
  return Boolean(candidate
    && candidate === pendingConfirmation
    && candidate.sourceId === committedSourceId
    && candidate.token === preparationSequence
    && setupInputsUnchanged(candidate)
    && previewImage.getAttribute("src") === candidate.previewUrl);
}

previewImage.addEventListener("load", () => {
  const candidate = pendingConfirmation;
  if (!pendingPreviewMatches(candidate)
      || !previewImage.complete || previewImage.naturalWidth <= 0) {
    return;
  }
  if (checkHostedLeaseExpiry() || isSessionExpired()
      || outcomeUncertain) {
    return;
  }
  candidate.state = "ready";
  const message =
    "راجع صورة صفحة الملف في لوحة المصدر، ثم اضغط «تحليل هذه الصفحة» لبدء التحليل.";
  setSetupActionStatus(message);
  if (!checkHostedLeaseExpiry() && !outcomeUncertain) {
    statusLine.textContent = message;
  }
  renderPendingConfirmation();
});

previewImage.addEventListener("error", () => {
  const candidate = pendingConfirmation;
  if (!pendingPreviewMatches(candidate)) {
    return;
  }
  if (checkHostedLeaseExpiry() || isSessionExpired()
      || outcomeUncertain) {
    return;
  }
  candidate.state = "image-failed";
  const message =
    "تعذّر تحميل صورة صفحة الملف المهيأة، لذا لا يمكن تأكيد تحليلها. هيّئ الصفحة مجددًا بعد التحقق من الخادم والملف.";
  setSetupActionStatus(message);
  if (!outcomeUncertain) {
    statusLine.textContent = message;
  }
  renderPendingConfirmation();
});

function analysisResultMessage(result) {
  if (checkHostedLeaseExpiry()) {
    return sessionExpiredMessage();
  }
  if (result.type === "success") {
    return RECEIVED_MESSAGE;
  }
  if (result.type === "refusal") {
    return analysisMessage(new StageError(
      "analysis", result.httpStatus, result.detail, false, false,
      result.exactTypedBodyRefusal === true));
  }
  if (result.outcome === "cancelled") {
    return "أُلغي طلب تحليل المصدر قبل اكتماله ولم تُطبَّق نتيجته. تبقى صفحتك وإعداداتك كما هي.";
  }
  return analysisMessage(new StageError(
    "analysis", result.httpStatus,
    result.detail ?? { code: result.outcome }));
}

// The one settings PUT/acknowledgment path, shared by page
// preparation and the standalone settings save. Exact request
// body and exact echo validation stay as they were; a completed
// request whose echo does not match is never treated as
// acknowledged. Each caller passes its own before-send guard,
// rechecked after the session await. Returns false when the
// guard kept the send from happening at all.
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

async function preparePage() {
  if (pending || outcomeUncertain || checkHostedLeaseExpiry()) {
    return;
  }
  if (!reviewReady) {
    setSetupActionStatus(reviewBootFailed
      ? "وحدة المراجعة غير محملة؛ التهيئة متوقفة في هذه الصفحة."
      : "جارٍ تحميل وحدة المراجعة؛ لا يمكن التهيئة بعد.");
    return;
  }
  clearSetupError();
  const captured = captureSetupInputs();
  const { file, grade, objective, pageNumberText } = captured;
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
  const token = ++preparationSequence;
  let settingsAcknowledged = false;
  let candidate = null;
  pendingConfirmation = null;
  renderPendingConfirmation();
  pending = true;
  notifyBusy();
  setSetupActionStatus("جارٍ تجهيز جلسة المتصفح…");
  statusLine.textContent = "جارٍ تجهيز جلسة المتصفح…";
  try {
    await ensureSession();
    if (token !== preparationSequence
        || !setupInputsUnchanged(captured)) {
      const message =
        "تغيّرت مدخلات الإعداد أثناء تجهيز الجلسة، فلم تُهيَّأ صفحة. راجع المدخلات ثم هيّئ الصفحة مجددًا.";
      setSetupActionStatus(message);
      statusLine.textContent = message;
      return;
    }
    if (checkHostedLeaseExpiry()) {
      statusLine.textContent = sessionExpiredMessage();
      setSetupActionStatus(sessionExpiredMessage());
      return;
    }
    setSetupActionStatus("جارٍ حفظ الصف والهدف…");
    statusLine.textContent = "جارٍ حفظ الصف والهدف…";
    const acknowledged = await putSettingsAcknowledged(
      grade, objective,
      () => reviewReady && token === preparationSequence
        && setupInputsUnchanged(captured)
        && !checkHostedLeaseExpiry());
    if (!acknowledged) {
      const message = isSessionExpired()
        ? sessionExpiredMessage()
        : "تغيّرت مدخلات الإعداد قبل إرسال حفظ الصف والهدف، فلم تُهيَّأ صفحة. راجع المدخلات ثم هيّئ الصفحة مجددًا.";
      setSetupActionStatus(message);
      statusLine.textContent = message;
      return;
    }
    settingsAcknowledged = true;
    if (checkHostedLeaseExpiry()) {
      statusLine.textContent = sessionExpiredMessage();
      setSetupActionStatus(sessionExpiredMessage());
      return;
    }
    if (token !== preparationSequence
        || !setupInputsUnchanged(captured)) {
      const message =
        "حُفظ الصف والهدف، لكن تغيّرت المدخلات قبل تهيئة الصفحة فلم تُهيَّأ صفحة جديدة. راجع المدخلات ثم هيّئ الصفحة مجددًا.";
      setSetupActionStatus(message);
      statusLine.textContent = message;
      return;
    }
    setSetupActionStatus("جارٍ تهيئة صفحة الملف المختارة…");
    statusLine.textContent = "جارٍ تهيئة صفحة الملف المختارة…";
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
    if (checkHostedLeaseExpiry()) {
      statusLine.textContent =
        "وصل إقرار بدء جديد بعد انتهاء نافذة التأكيد، فلم يُعتمد مصدره في هذا العرض ولم تُمسّ الصفحة والمسودات المعروضة. "
        + sessionExpiredMessage();
      setSetupActionStatus(sessionExpiredMessage());
      return;
    }
    const inputsStillCurrent = token === preparationSequence
      && setupInputsUnchanged(captured);
    if (inputsStillCurrent) {
      candidate = {
        ...captured,
        token,
        pageIndex,
        sourceId: metadata.source_id,
        previewUrl:
          `${metadata.preview_url}?v=${encodeURIComponent(metadata.render_sha256)}`,
        state: "loading",
      };
      pendingConfirmation = candidate;
      if (selectedFilePageNote) {
        selectedFilePageNote.textContent =
          `الصورة المعروضة في المصدر هي صفحة الملف ${arabicIndic(metadata.page_index + 1)} من ${arabicIndic(metadata.page_count)}. ترتيبها داخل الملف قد يختلف عن الرقم المطبوع على الصفحة، ولن يُحلَّل الملف أو الكتاب كاملًا.`;
      }
      // Force this preparation's own image load rather than
      // trusting a previous complete image at the same URL.
      previewImage.removeAttribute("src");
    }
    // The acknowledged Start remains the only destructive step.
    // Preparation stops here: source analysis waits for the
    // separate explicit confirmation of this exact page.
    adoptSource(metadata);
    renderPendingConfirmation();
    for (const callback of sourceStartedCallbacks) {
      await callback(metadata);
    }
    if (checkHostedLeaseExpiry()) {
      statusLine.textContent = sessionExpiredMessage();
      setSetupActionStatus(sessionExpiredMessage());
      return;
    }
    if (!inputsStillCurrent || candidate !== pendingConfirmation
        || !setupInputsUnchanged(captured)) {
      if (pendingConfirmation === candidate) {
        pendingConfirmation = null;
      }
      const message =
        "تغيّرت مدخلات الإعداد أثناء تهيئة الصفحة؛ الصفحة المعروضة هي آخر صفحة هيّأها الخادم، لكنها ليست جاهزة للتحليل بالمدخلات الحالية. هيّئ الصفحة مجددًا.";
      setSetupActionStatus(message);
      statusLine.textContent = message;
      renderPendingConfirmation();
      return;
    }
    if (candidate.state === "loading") {
      const message =
        "وصلت بيانات الصفحة المهيأة؛ جارٍ تحميل صورة صفحة الملف للمراجعة قبل التحليل.";
      setSetupActionStatus(message);
      statusLine.textContent = message;
    }
  } catch (error) {
    if (isSessionExpiredError(error)) {
      statusLine.textContent = sessionExpiredMessage();
      setSetupActionStatus(sessionExpiredMessage());
    } else if (isUncertainOutcome(error)) {
      enterUncertainOutcome();
    } else if (error instanceof StageError) {
      const message = setupStageMessage(error);
      statusLine.textContent = settingsAcknowledged
        ? "حُفظ الصف والهدف، لكن لم تُهيَّأ صفحة جديدة."
        : previousStatus;
      setSetupActionStatus(message);
      showSetupError(
        message, error.stage === "start" ? fileInput : null);
    } else {
      enterUncertainOutcome();
    }
  } finally {
    pending = false;
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
  if (pending || outcomeUncertain || checkHostedLeaseExpiry()
      || !standaloneSettingsPermitted()) {
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
  setSetupActionStatus("جارٍ تجهيز الجلسة وحفظ الصف والهدف…");
  statusLine.textContent = "جارٍ حفظ الصف والهدف…";
  try {
    const acknowledged = await putSettingsAcknowledged(
      grade, objective,
      () => !checkHostedLeaseExpiry() && standaloneSettingsPermitted());
    if (!acknowledged) {
      statusLine.textContent = isSessionExpired()
        ? sessionExpiredMessage() : previousStatus;
      setSetupActionStatus(statusLine.textContent);
      return;
    }
    if (checkHostedLeaseExpiry()) {
      statusLine.textContent = sessionExpiredMessage();
      setSetupActionStatus(sessionExpiredMessage());
    } else if (!outcomeUncertain) {
      statusLine.textContent = "حُفظ الصف والهدف.";
      setSetupActionStatus("حُفظ الصف والهدف.");
    }
  } catch (error) {
    if (isSessionExpiredError(error)) {
      statusLine.textContent = sessionExpiredMessage();
    } else if (isUncertainOutcome(error)) {
      enterUncertainOutcome();
    } else if (error instanceof StageError) {
      statusLine.textContent = previousStatus;
      setSetupActionStatus(setupStageMessage(error));
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
  if (!reviewReady || pending || startQueued || queuedMutations > 0
      || outcomeUncertain || isSessionExpired()) {
    return;
  }
  startQueued = true;
  void runSerialized(() => preparePage()).finally(() => {
    startQueued = false;
  });
});
fileInput.addEventListener("change", updateReplacementWarning);
function updateSelectedUploadName() {
  const selectedUploadName = document.querySelector('[data-testid="selected-upload-name"]');
  if (!selectedUploadName) {
    return;
  }
  const selectedFile = fileInput.files && fileInput.files[0];
  selectedUploadName.textContent = selectedFile ? selectedFile.name : "PDF أو صورة PNG/JPG";
}
fileInput.addEventListener("change", updateSelectedUploadName);
updateSelectedUploadName();

for (const setupInput of [fileInput, gradeInput, objectiveInput,
    pageNumberInput]) {
  if (!setupInput) {
    continue;
  }
  const invalidateSetupInput = () => {
    setupInputRevision += 1;
    invalidatePendingConfirmation(
      "تغيّرت بيانات الإعداد، لذا لم تعد الصفحة المهيأة بانتظار التأكيد صالحة. اضغط «تهيئة الصفحة» لتهيئة البيانات الحالية وتأكيدها.");
  };
  setupInput.addEventListener("input", invalidateSetupInput);
  setupInput.addEventListener("change", invalidateSetupInput);
}
confirmButton?.addEventListener("click", handleConfirmPage);
for (const [step, button] of workflowNavButtons) {
  if (!button) {
    continue;
  }
  button.addEventListener("click", () => {
    if (button.disabled) {
      return;
    }
    selectWorkflowStep(step);
    showWork();
    const heading = document.getElementById(workflowHeadingIds[step]);
    if (!heading) {
      return;
    }
    heading.scrollIntoView({ block: "center", inline: "nearest" });
    heading.focus({ preventScroll: true });
    ensureFocusedControlVisible();
  });
}


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
  sourceTab.tabIndex = activePane === "source" ? 0 : -1;
  workTab.setAttribute(
    "aria-selected", String(activePane === "work"));
  workTab.tabIndex = activePane === "work" ? 0 : -1;
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

const paneTabOrder = ["source", "work"];

function handlePaneTabKeydown(event) {
  if (event.altKey || event.ctrlKey || event.metaKey || event.shiftKey) {
    return;
  }
  const focusedName = event.currentTarget === sourceTab ? "source"
    : event.currentTarget === workTab ? "work" : activePane;
  const index = paneTabOrder.indexOf(focusedName);
  let next = null;
  if (event.key === "ArrowRight") {
    next = paneTabOrder[(index + 1) % paneTabOrder.length];
  } else if (event.key === "ArrowLeft") {
    next = paneTabOrder[
      (index - 1 + paneTabOrder.length) % paneTabOrder.length];
  } else if (event.key === "Home") {
    next = paneTabOrder[0];
  } else if (event.key === "End") {
    next = paneTabOrder[paneTabOrder.length - 1];
  } else {
    return;
  }
  event.preventDefault();
  activatePane(next);
  tabs[next].focus({ preventScroll: true });
}

sourceTab.addEventListener("keydown", handlePaneTabKeydown);
workTab.addEventListener("keydown", handlePaneTabKeydown);

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
initStudioView(() => ({
  stage: activeWorkflowStep,
  hasFile: Boolean(fileInput.files && fileInput.files.length),
  candidate: pendingConfirmation,
  candidateState: pendingConfirmation?.state ?? null,
  candidateCurrent: pendingConfirmationCurrent(),
  hasCommittedSource,
  initialAnalysisAttempted,
  outcomeUncertain,
  sessionExpired: isSessionExpired(),
  setupErrorVisible: Boolean(setupError && !setupError.hidden),
}));
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
  isSessionExpired,
  onSessionExpired,
  isSessionExpiredError,
  sessionExpiredMessage,
  usageMessage,
  setStatus: (text) => {
    statusLine.textContent = text;
    if (activeWorkflowStep === "setup") {
      setSetupActionStatus(text);
    } else if ((activeWorkflowStep === "source"
        || activeWorkflowStep === "questions")
        && generationActionStatus) {
      generationActionStatus.textContent = text;
    }
  },
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
  selectWorkflowStep,
  revealSourceReview,
  setWorkflowAvailability,
};

let bootSessionFailed = false;

ensureSession().then(() => {
  if (!bootSessionFailed && !pending && !startQueued
      && queuedMutations === 0 && !pendingConfirmation
      && !hasCommittedSource) {
    showBootReadyIfPossible();
  }
}).catch((error) => {
  bootSessionFailed = true;
  if (outcomeUncertain) {
    return;
  }
  if (isUncertainOutcome(error)) {
    enterUncertainOutcome();
    return;
  }
  statusLine.textContent =
    "تعذّر بدء جلسة هذا المتصفح. تحقّق من الخادم المحلي ثم اضغط «تهيئة الصفحة» للمحاولة بضغطة جديدة.";
});

import("./review.js").then(() => {
  if (!reviewRegistrationsComplete()) {
    throw new Error("Incomplete review registrations.");
  }
  reviewReady = true;
  notifyBusy();
  renderWorkflowStep();
  if (!bootSessionFailed && !pending && !startQueued
      && queuedMutations === 0 && !pendingConfirmation
      && !hasCommittedSource) {
    showBootReadyIfPossible();
  }
}).catch(() => {
  reviewBootFailed = true;
  reviewReady = false;
  if (bootError) {
    bootError.textContent =
      "تعذّر تحميل مكوّن المراجعة ولم يُرسَل أي تحليل جديد للصفحة؛ يمكنك تحديث الصفحة بنفسك لإعادة المحاولة، لكن التحديث قد يُفقد أي عمل غير محفوظ.";
    bootError.hidden = false;
  }
  notifyBusy();
});

