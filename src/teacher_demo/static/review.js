// Review coordinator: acknowledged snapshots, keyed local drafts
// and the authoritative teacher preview. All transport, session
// and queue ownership stay in workspace.js.

import { workspace } from "./workspace.js";
import {
  BLOOM_ARABIC,
  INCLUSION_ARABIC,
  LEGAL_INCLUSIONS,
  createPreviewItem,
  createQuestionCard,
  createRegionItem,
  orderChildren,
} from "./review-render.js";

const MODEL_UNAVAILABLE = "غير متاح";
const GATE_UNAVAILABLE =
  "لا يمكن التوليد بعد: راجع مقطعًا واحدًا على الأقل واجعله مشمولًا بنص غير فارغ.";
const GATE_READY = "المصدر جاهز لتوليد الأسئلة.";
const SESSION_MESSAGE =
  "انتهت جلسة هذا المتصفح أو تعذّر التحقق منها. أعد تحميل الصفحة لبدء جلسة جديدة؛ قد تُفقد مساحة العمل غير المحفوظة.";
const STALE_PREVIEW_TEXT =
  "المعاينة الحالية غير متاحة لأن حالة الأسئلة المعتمدة الحالية غير مؤكدة.";
const CURRENCY_STATUS_TEXT =
  "حالة المراجعة المعروضة غير مؤكدة بعد إقرار الإعدادات: "
  + "المحتوى والمسودات المحفوظة أدناه آخر حالة معروفة للاطلاع فقط، "
  + "وليست حالة حالية للمعاينة أو الاعتماد. يصف هذا حالة عرض "
  + "المراجعة بعد الإعدادات المقرّة، وليس نتيجة غير معروفة لطلب تغيير.";
const IDENTITY_CURRENCY_STATUS_TEXT =
  "حالة المراجعة المعروضة غير مؤكدة بعد رفض الجلسة أو تغيّر المصدر: "
  + "المحتوى والمسودات المحفوظة أدناه آخر حالة معروفة للاطلاع فقط، "
  + "وليست حالة حالية للمعاينة أو الاعتماد. لإعادة إنشاء حالة حالية "
  + "أعد تحميل الصفحة وابدأ من جديد؛ قد يُفقد هذا العمل غير المحفوظ.";
const GENERATION_SUCCESS =
  "اكتمل توليد الأسئلة؛ راجع المسودات أدناه قبل أي اعتماد.";
const GENERATION_TERMINAL =
  "لم يكتمل توليد الأسئلة ولم تُعتمد أي أسئلة جديدة في هذه الخطوة. بقيت مراجعة المصدر كما هي.";
const GENERATION_UNAVAILABLE =
  "خدمة توليد الأسئلة غير متاحة في هذا التشغيل. لم تُنشر أسئلة جديدة، وبقيت مراجعة المصدر كما هي.";
const GENERATION_CANCELLED =
  "أُلغي طلب توليد الأسئلة ولم تُنشر أسئلة جديدة في هذه الخطوة. بقيت مراجعة المصدر كما هي.";
// Generation context wrapper around the one shared outcome
// phrase owner in workspace.js; cancelled keeps its pinned text
// above, and an unknown outcome falls back to GENERATION_TERMINAL.
function generationTerminalText(outcome, detail) {
  const phrase = workspace.usageMessage(detail)
    || workspace.outcomePhrase(outcome);
  return phrase
    ? `لم يكتمل توليد الأسئلة: ${phrase}، ولم تُعتمد أي أسئلة جديدة. بقيت مراجعة المصدر كما هي.`
    : GENERATION_TERMINAL;
}
const REGION_SAVED = "حُفظت مراجعة المقطع.";
const QUESTION_SAVED = "حُفظ تعديل السؤال.";
const QUESTION_APPROVED = "اعتُمد السؤال.";
const QUESTION_REJECTED = "رُفض السؤال.";
const OUTDATED_PREVIEW_TEXT =
  "لم تعد هناك أسئلة معتمدة حاليًا؛ الاعتمادات السابقة تحتاج إلى تحديث.";
const REJECTED_PREVIEW_TEXT =
  "لا أسئلة معتمدة حاليًا؛ توجد أسئلة مرفوضة بقرار المعلم.";

const QUESTION_EDIT_FIELDS = [
  ["question", "current_question"],
  ["answer_guidance", "current_answer_guidance"],
  ["cognitive_explanation", "current_cognitive_explanation"],
  ["grade", "current_grade"],
  ["objective", "current_objective"],
  ["bloom_level", "current_level"],
];
const ABORTED = Symbol("queued action no longer sendable");

function byTestId(testId) {
  return document.querySelector(`[data-testid='${testId}']`);
}

const sourceReviewEmpty = byTestId("source-review-empty");
const sourceModelName = byTestId("source-model-name");
const regionList = byTestId("region-list");
const sourceCounts = byTestId("source-counts");
const generateGateStatus = byTestId("generate-gate-status");
const generateButton = document.getElementById("generate-questions");
const questionReviewEmpty = byTestId("question-review-empty");
const slotOutcomes = byTestId("slot-outcomes");
const questionList = byTestId("question-list");
const previewEmpty = byTestId("preview-empty");
const previewReturn = byTestId("preview-return");
const previewList = byTestId("preview-list");
const sourceSelectedText = byTestId("source-selected-text");
const sourceSelectedContext = byTestId("source-selected-context");
const stageItems = document.querySelectorAll(".stages .stage");
const sourceReviewHeading =
  document.getElementById("source-review-heading");
const questionReviewHeading =
  document.getElementById("question-review-heading");
const defaultPreviewEmptyText = previewEmpty.textContent;
// Persistent currentness disclosure, owned by this coordinator and
// placed outside both switchable panes so pane switching and the
// workspace status line can never hide or overwrite it.
const currencyStatus = document.createElement("p");
currencyStatus.dataset.testid = "review-currency-status";
currencyStatus.textContent = CURRENCY_STATUS_TEXT;
currencyStatus.hidden = true;
const workPane = byTestId("work-pane");
if (workPane && workPane.parentElement) {
  workPane.parentElement.insertBefore(currencyStatus, workPane);
} else if (document.body) {
  document.body.append(currencyStatus);
}

let currentSnapshot = null;
let activeSourceId = null;
let isBusy = false;
let previewSuppressed = false;
// The one currentness authority. acknowledgedSettings is the latest
// acknowledged grade/objective that a restoring snapshot must match.
// reason is null while current, "settings" for the acknowledged-
// settings withdrawal (healed only by a matching snapshot), or
// "identity" for a session/source identity refusal (sticky until
// the explicit acknowledged source-start reinitialization in
// clearReviewForSource; no snapshot or metadata read heals it).
const reviewCurrency = {
  current: true, acknowledgedSettings: null, reason: null };
// Read-only per-request provenance owner, separate from the
// mutation queue and from reviewCurrency. One raw read per
// (kind, request_id) per source generation; completions paint
// only their own provenance text and never adopt a snapshot,
// change a decision, clear a lock or touch the status line.
let metadataEpoch = 0;
let sourceSummaryRequestId = null;
// The exact source region an evidence link last selected. It is
// re-derived from the current snapshot on every repaint and
// clears itself when that region no longer exists.
let selectedRegionId = null;
const modelByRequest = new Map();
const regionRecords = new Map();
const regionDrafts = new Map();
const questionRecords = new Map();
const questionDrafts = new Map();
const openEditors = new Set();
const previewRecords = new Map();
// Final-UI review support: workflow availability, adopted-step
// handoffs, raw source notes and exact request summaries. This
// block declares no snapshot, currency or dirty state of its own;
// every helper reads the one coordinator state above and paints
// text without adopting data or enabling any write.
const sourceReviewNotes = byTestId("source-review-notes");
const sourcePageContinuation = byTestId("source-page-continuation");
const questionRequestSummaries = byTestId("question-request-summaries");

// Availability follows the current adopted snapshot only. The
// workspace setter paints step buttons and never calls back into
// this coordinator, so updating it from renderView cannot recurse
// and never moves the selected step.
function workflowAvailabilityFor(snapshot, regions, questions) {
  const requests = requestEntries(snapshot);
  const closed = (entry) => entry && typeof entry === "object"
    && (entry.state === "terminal" || entry.state === "superseded");
  const source = regions.length > 0 || requests.some(
    (entry) => closed(entry) && entry.kind === "source");
  const slots = Array.isArray(snapshot?.slot_outcomes)
    ? snapshot.slot_outcomes : [];
  const questionsAvailable = questions.length > 0 || slots.length > 0
    || requests.some((entry) => entry
      && typeof entry === "object" && entry.kind === "questions");
  const preview = Boolean(reviewCurrency.current && !previewSuppressed
    && snapshot && Array.isArray(snapshot.preview_items)
    && snapshot.preview_items.length);
  return { source, questions: questionsAvailable, preview };
}

function updateWorkflowAvailability(snapshot, regions, questions) {
  workspace.setWorkflowAvailability?.(
    workflowAvailabilityFor(snapshot, regions, questions));
}

// Step handoff after provider adoption, shared by every source
// branch: only an actually adopted, still-current snapshot for
// this exact source may select a step. A late lease return has
// adopted=false and keeps the previous snapshot, so it can never
// pretend adoption here. Nothing in this path sends or resets.
function handoffAdoptedProviderStep(info, adopted) {
  if (!adopted || !info || !currentSnapshot
      || currentSnapshot !== info.snapshot
      || !reviewCurrency.current
      || workspace.isOutcomeUncertain()
      || workspace.isSessionExpired()
      || activeSourceId !== info.sourceId
      || currentSnapshot.source_id !== info.sourceId) {
    return;
  }
  if (info.kind === "source" && info.outcome === "ok") {
    if (typeof workspace.revealSourceReview === "function") {
      workspace.revealSourceReview();
    } else {
      workspace.selectWorkflowStep?.("source");
      workspace.showWork();
    }
    return;
  }
  if (info.kind === "questions") {
    workspace.selectWorkflowStep?.("questions");
    workspace.showWork();
  }
}

// The same adopted-current proof for one exact closed request,
// used when this coordinator's own generation action settles.
function hasAdoptedCurrentRequest(kind, requestId, sourceId) {
  return Boolean(currentSnapshot && reviewCurrency.current
    && !workspace.isOutcomeUncertain()
    && !workspace.isSessionExpired()
    && activeSourceId === sourceId
    && currentSnapshot.source_id === sourceId
    && requestEntries(currentSnapshot).some((entry) => entry
      && typeof entry === "object"
      && entry.request_id === requestId && entry.kind === kind
      && (entry.state === "terminal"
        || entry.state === "superseded")));
}

// Source notes from the same retained raw read as the model
// name. Envelope mismatch trusts neither field; each raw field
// is then known only when it has its exact expected type.
function validatedSourceNotes(raw, requestId, kind) {
  if (kind !== "source" || !raw || typeof raw !== "object"
      || raw.request_id !== requestId || raw.kind !== kind
      || raw.raw_retention !== "retained") {
    return null;
  }
  const result = raw.provider_result;
  const payload = result && typeof result === "object"
    ? result.raw : null;
  if (!payload || typeof payload !== "object") {
    return null;
  }
  const notes = Array.isArray(payload.review_notes)
    && payload.review_notes.every(
      (value) => typeof value === "string")
    ? [...payload.review_notes] : null;
  const pageContinues = payload.page_continues === true
      || payload.page_continues === false
    ? payload.page_continues : null;
  return { notes, pageContinues };
}

function sourceNotesRequestId() {
  if (!currentSnapshot) {
    return null;
  }
  if (sourceSummaryRequestId
      && requestStillPresent("source", sourceSummaryRequestId)) {
    return sourceSummaryRequestId;
  }
  for (const region of orderedRegions(currentSnapshot.regions)) {
    const requestId = requestIdOf(region);
    if (requestId) {
      return requestId;
    }
  }
  return null;
}

// Notes paint only from the matched entry already stored by the
// epoch/source/request-fenced read, and only while that exact
// request is still present in the current adopted snapshot.
// Anything else is the unavailable state; the continuation
// attribute exists only for a known boolean and its wording
// attributes the claim to the model, never to this page.
function paintSourceNotes() {
  const requestId = sourceNotesRequestId();
  const entry = requestId
    ? modelByRequest.get(metadataKey("source", requestId)) : null;
  const usable = Boolean(requestId && entry && currentSnapshot
    && reviewCurrency.current && !workspace.isOutcomeUncertain()
    && requestStillPresent("source", requestId));
  const notes = usable && Array.isArray(entry.reviewNotes)
    ? entry.reviewNotes : null;
  const pageContinues = usable
      && (entry.pageContinues === true
        || entry.pageContinues === false)
    ? entry.pageContinues : null;
  if (sourceReviewNotes) {
    sourceReviewNotes.hidden = false;
    if (notes && notes.length) {
      sourceReviewNotes.replaceChildren(...notes.map((note) => {
        const line = document.createElement("p");
        line.textContent = note;
        return line;
      }));
    } else if (notes) {
      sourceReviewNotes.textContent =
        "لم يورد النموذج ملاحظات مراجعة لهذا المصدر.";
    } else if (usable && entry.state === "pending") {
      sourceReviewNotes.textContent =
        "ملاحظات مراجعة النموذج غير متاحة بعد.";
    } else {
      sourceReviewNotes.textContent =
        "ملاحظات مراجعة النموذج غير متاحة لهذا المصدر.";
    }
  }
  if (sourcePageContinuation) {
    sourcePageContinuation.hidden = false;
    if (pageContinues === true || pageContinues === false) {
      sourcePageContinuation.setAttribute(
        "data-continues", pageContinues ? "true" : "false");
      sourcePageContinuation.textContent = pageContinues
        ? "أفاد النموذج بأن محتوى هذه الصفحة يمتد إلى صفحة تالية."
        : "أفاد النموذج بأن محتوى هذه الصفحة لا يمتد إلى صفحة تالية.";
    } else {
      sourcePageContinuation.removeAttribute("data-continues");
      sourcePageContinuation.textContent =
        "غير متاح بعد ما أفاد به النموذج عن امتداد محتوى هذه الصفحة إلى صفحة تالية.";
    }
  }
}

function paintSourceNotesForRequest(kind, requestId) {
  if (kind !== "source" || requestId !== sourceNotesRequestId()) {
    return;
  }
  paintSourceNotes();
}

function slotOutcomeCounts(requestId, slotOutcomes) {
  let draftCount = 0;
  let abstainCount = 0;
  for (const entry of Array.isArray(slotOutcomes)
      ? slotOutcomes : []) {
    if (!entry || typeof entry !== "object"
        || entry.request_id !== requestId) {
      continue;
    }
    const outcome = String(entry.outcome ?? entry.status ?? "");
    if (outcome === "draft") {
      draftCount += 1;
    } else if (outcome === "abstain" || outcome === "abstained") {
      abstainCount += 1;
    }
  }
  return { draftCount, abstainCount };
}

// One exact request's summary sentence, composed only from that
// request's outcome and its own slot counts: abstention wording
// appears only where abstentions actually occurred, and a zero
// draft count is stated as no draft arriving, never as drafts
// waiting below.
function questionRequestSummaryText(request, draftCount,
    abstainCount) {
  const outcome = typeof request?.outcome === "string"
    ? request.outcome : "";
  let lead;
  if (outcome === "ok") {
    lead = "اكتمل طلب توليد الأسئلة.";
  } else if (outcome === "cancelled") {
    lead = "أُلغي طلب توليد الأسئلة.";
  } else {
    const phrase = workspace.outcomePhrase(outcome) || outcome
      || "نتيجة غير مؤكدة";
    lead = `انتهى طلب توليد الأسئلة دون اكتمال ناجح: ${phrase}.`;
  }
  const draftClause = draftCount > 0
    ? `وصلت ${arabicIndic(draftCount)} من مسودات الأسئلة الجديدة.`
    : "لم تصل أي مسودة سؤال جديدة من هذا الطلب.";
  const abstainClause = abstainCount > 0
    ? `امتنع النموذج في ${arabicIndic(abstainCount)} من المستويات (امتناع عن التوليد).`
    : "";
  return [lead, draftClause, abstainClause].filter(Boolean).join(" ");
}

// Request order is the snapshot's own order; no latest request
// is picked by sorting IDs and previous question cards are left
// to the keyed card rendering, never erased here. Returns how
// many summaries were painted so the generic empty line can
// stand down when a real outcome is already readable.
function renderQuestionRequestSummaries(snapshot) {
  if (!questionRequestSummaries) {
    return 0;
  }
  const seen = new Set();
  const nodes = [];
  for (const request of requestEntries(snapshot)) {
    if (!request || typeof request !== "object"
        || request.kind !== "questions"
        || (request.state !== "terminal"
          && request.state !== "superseded")
        || typeof request.outcome !== "string"
        || !request.outcome) {
      continue;
    }
    const requestId = requestIdOf(request);
    if (!requestId || seen.has(requestId)) {
      continue;
    }
    seen.add(requestId);
    const { draftCount, abstainCount } = slotOutcomeCounts(
      requestId, snapshot?.slot_outcomes);
    const node = document.createElement("div");
    node.dataset.testid = "question-request-summary";
    node.dataset.requestId = requestId;
    node.dataset.outcome = request.outcome;
    node.dataset.draftCount = String(draftCount);
    node.dataset.abstainCount = String(abstainCount);
    const text = document.createElement("span");
    text.textContent = questionRequestSummaryText(
      request, draftCount, abstainCount);
    const identifier = document.createElement("bdi");
    identifier.textContent = requestId;
    node.append(text, " معرّف الطلب: ", identifier);
    nodes.push(node);
  }
  questionRequestSummaries.replaceChildren(...nodes);
  if (nodes.length) {
    questionRequestSummaries.hidden = false;
  }
  return nodes.length;
}

// The global generation result line for one exact operation,
// counted from that request's own slot outcomes in its result
// snapshot. Without any slot rows for the request the previous
// general success line remains the honest fallback.
function generationSuccessText(requestId, snapshot) {
  if (!requestId || !snapshot || typeof snapshot !== "object"
      || !Array.isArray(snapshot.slot_outcomes)) {
    return GENERATION_SUCCESS;
  }
  const ownSlots = snapshot.slot_outcomes.filter((entry) => entry
    && typeof entry === "object" && entry.request_id === requestId);
  if (!ownSlots.length) {
    return GENERATION_SUCCESS;
  }
  const { draftCount, abstainCount } = slotOutcomeCounts(
    requestId, snapshot.slot_outcomes);
  if (draftCount > 0) {
    const abstainClause = abstainCount > 0
      ? ` امتنع النموذج في ${arabicIndic(abstainCount)} من المستويات.`
      : "";
    return `اكتمل توليد الأسئلة: وصلت ${arabicIndic(draftCount)} من مسودات الأسئلة الجديدة؛ راجع المسودات أدناه قبل أي اعتماد.${abstainClause}`;
  }
  if (abstainCount > 0) {
    return `اكتمل طلب توليد الأسئلة دون مسودات جديدة: لم تصل أي مسودة سؤال جديدة، وامتنع النموذج في ${arabicIndic(abstainCount)} من المستويات.`;
  }
  return "اكتمل طلب توليد الأسئلة دون مسودات جديدة: لم تصل أي مسودة سؤال جديدة.";
}



const callbacks = {
  onRegionDraft: handleRegionDraft,
  onRegionCommit: handleRegionCommit,
  onRegionCancel: handleRegionCancel,
  onRegionMove: handleRegionMove,
  onRegionEvidence: handleRegionEvidence,
  onShowQuestion: handleShowQuestion,
  onQuestionDraft: handleQuestionDraft,
  onQuestionEditToggle: handleQuestionEditToggle,
  onQuestionApprove: handleQuestionApprove,
  onQuestionReject: handleQuestionReject,
  onQuestionEditCommit: handleQuestionEditCommit,
  onQuestionEditCancel: handleQuestionEditCancel,
};

function arabicIndic(number) {
  return String(number).replace(/\d/g, (digit) => "٠١٢٣٤٥٦٧٨٩"[digit]);
}

function locked() {
  return isBusy || workspace.isOutcomeUncertain()
    || workspace.hasActiveProviderOperation()
    || !reviewCurrency.current;
}

function snapshotMatchesSettings(snapshot, settings) {
  return Boolean(snapshot && settings
    && snapshot.grade === settings.grade
    && snapshot.objective === settings.objective);
}

function isNoncurrentOnly() {
  return !reviewCurrency.current && !isBusy
    && !workspace.isOutcomeUncertain();
}

function regionDraftFrom(region) {
  return {
    current_text: region.current_text,
    current_label: region.current_label,
    inclusion: region.inclusion,
  };
}

function questionDraftFrom(question) {
  const draft = {};
  for (const [key, field] of QUESTION_EDIT_FIELDS) {
    draft[key] = question[field];
  }
  return draft;
}

function findRegion(snapshot, regionId) {
  return (snapshot?.regions || []).find(
    (region) => region.region_id === regionId) || null;
}

function findQuestion(snapshot, questionId) {
  return (snapshot?.questions || []).find(
    (question) => question.question_id === questionId) || null;
}

function isRegionDirty(regionId, snapshot) {
  const draft = regionDrafts.get(regionId);
  const region = findRegion(snapshot, regionId);
  return Boolean(draft && region
    && (draft.current_text !== region.current_text
      || draft.current_label !== region.current_label
      || draft.inclusion !== region.inclusion));
}

function isQuestionDirty(questionId, snapshot) {
  const draft = questionDrafts.get(questionId);
  const question = findQuestion(snapshot, questionId);
  if (!draft || !question) {
    return false;
  }
  return QUESTION_EDIT_FIELDS.some(
    ([key, field]) => draft[key] !== question[field]);
}

function orderedRegions(regions) {
  const list = Array.isArray(regions) ? [...regions] : [];
  if (list.length
      && list.every((r) => Number.isFinite(r.display_position))) {
    list.sort((a, b) => a.display_position - b.display_position);
  }
  return list;
}

function requestIdOf(item) {
  return item && typeof item.request_id === "string" && item.request_id
    ? item.request_id : null;
}

function metadataKey(kind, requestId) {
  return `${kind}:${requestId}`;
}

function modelTextFor(kind, requestId) {
  if (!requestId) {
    return MODEL_UNAVAILABLE;
  }
  const entry = modelByRequest.get(metadataKey(kind, requestId));
  return entry && entry.state === "known" && entry.modelName
    ? entry.modelName : MODEL_UNAVAILABLE;
}

function isValidOriginalFact(fact) {
  return Boolean(fact && typeof fact === "object"
    && typeof fact.region_id === "string" && fact.region_id
    && typeof fact.quote === "string");
}

function isValidOriginalEntries(entries) {
  return Array.isArray(entries) && entries.every((entry) =>
    entry && typeof entry === "object"
    && typeof entry.text === "string"
    && (entry.origin === "source" || entry.origin === "objective")
    && Array.isArray(entry.supporting_facts)
    && entry.supporting_facts.every(isValidOriginalFact)
    // Objective origin can never masquerade as a source fact.
    && (entry.origin === "source"
      || entry.supporting_facts.length === 0));
}

function isValidOriginalOrderPairs(pairs) {
  return Array.isArray(pairs) && pairs.every((pair) =>
    Array.isArray(pair) && pair.length === 2
    && typeof pair[0] === "string" && pair[0]
    && typeof pair[1] === "string" && pair[1]
    && pair[0] !== pair[1]);
}

// A legitimate abstention carries no draft to disclose; it is
// skipped at this boundary so it can never invalidate the good
// drafts of the same request. Anything that is neither a valid
// draft item nor an explicit abstention still distrusts the join.
function isAbstentionOriginalItem(item) {
  return item.outcome === "abstain" || item.outcome === "abstained"
    || item.status === "abstain" || item.status === "abstained"
    || item.abstained === true || item.abstain === true;
}

// The retained raw items of one exact questions request, keyed
// by slot, from the same single read that yields the model name.
// Any malformed envelope/item distrusts the whole join.
function validatedOriginalItems(raw, requestId, kind) {
  if (kind !== "questions" || !raw || typeof raw !== "object"
      || raw.request_id !== requestId || raw.kind !== kind
      || raw.raw_retention !== "retained") {
    return null;
  }
  const result = raw.provider_result;
  const payload = result && typeof result === "object"
    ? result.raw : null;
  if (!payload || typeof payload !== "object"
      || !Array.isArray(payload.items)) {
    return null;
  }
  const items = new Map();
  const seenSlotIds = new Set();
  for (const item of payload.items) {
    if (!item || typeof item !== "object"
        || typeof item.slot_id !== "string" || !item.slot_id
        || seenSlotIds.has(item.slot_id)) {
      return null;
    }
    seenSlotIds.add(item.slot_id);
    if (isAbstentionOriginalItem(item)) {
      continue;
    }
    if (typeof item.question !== "string"
        || typeof item.answer_guidance !== "string"
        || typeof item.cognitive_explanation !== "string"
        || typeof item.level !== "string"
        || !Array.isArray(item.evidence)
        || !item.evidence.every(isValidOriginalFact)
        || !isValidOriginalOrderPairs(
          item.required_order_relationships)
        || !Array.isArray(item.hypothetical_assumptions)
        || !item.hypothetical_assumptions.every(
          (value) => typeof value === "string")
        || !isValidOriginalEntries(item.evaluate_criteria)
        || !isValidOriginalEntries(item.create_rubric)) {
      return null;
    }
    items.set(item.slot_id, item);
  }
  return items;
}

// The original material a question may honestly show: the exact
// same-request/slot raw item, cross-checked against the
// snapshot's immutable original scalars. Anything else is
// unavailable; mutable current fields are never substituted.
function originalViewFor(question) {
  const requestId = requestIdOf(question);
  if (!requestId) {
    return { state: "unavailable" };
  }
  const entry = modelByRequest.get(
    metadataKey("questions", requestId));
  if (!entry || entry.state === "pending") {
    return { state: "pending" };
  }
  if (entry.state !== "known" || !entry.originals) {
    return { state: "unavailable" };
  }
  const item = entry.originals.get(question.slot_id);
  if (!item
      || item.question !== question.original_question
      || item.answer_guidance !== question.original_answer_guidance
      || item.cognitive_explanation
        !== question.original_cognitive_explanation
      || item.level !== question.model_level) {
    return { state: "unavailable" };
  }
  return { state: "known", item };
}

function provenanceFor(kind, item) {
  const requestId = requestIdOf(item);
  return {
    requestId,
    requestIdText: requestId || MODEL_UNAVAILABLE,
    modelName: modelTextFor(kind, requestId),
    original: kind === "questions" && item
      ? originalViewFor(item) : null,
  };
}

function validatedModelName(raw, requestId, kind) {
  if (!raw || typeof raw !== "object"
      || raw.request_id !== requestId || raw.kind !== kind
      || raw.raw_retention !== "retained") {
    return null;
  }
  const result = raw.provider_result;
  if (!result || typeof result !== "object"
      || typeof result.model_name !== "string"
      || !result.model_name.trim()) {
    return null;
  }
  return result.model_name;
}

function requestStillPresent(kind, requestId) {
  if (!currentSnapshot) {
    return false;
  }
  if (kind === "source") {
    return sourceSummaryRequestId === requestId
      || (currentSnapshot.regions || []).some(
        (region) => requestIdOf(region) === requestId);
  }
  return (currentSnapshot.questions || []).some(
    (question) => requestIdOf(question) === requestId);
}

function paintModelForRequest(kind, requestId, modelName) {
  if (!currentSnapshot) {
    return;
  }
  const text = modelName || MODEL_UNAVAILABLE;
  if (kind === "source") {
    if (sourceSummaryRequestId === requestId) {
      sourceModelName.textContent = text;
    }
    for (const region of currentSnapshot.regions || []) {
      if (requestIdOf(region) === requestId) {
        regionRecords.get(region.region_id)?.setModelName?.(text);
      }
    }
    return;
  }
  for (const question of currentSnapshot.questions || []) {
    if (requestIdOf(question) === requestId) {
      questionRecords.get(question.question_id)?.setModelName?.(text);
    }
  }
  for (const entry of currentSnapshot.preview_items || []) {
    const joined = findQuestion(currentSnapshot, entry.question_id);
    if (joined && requestIdOf(joined) === requestId) {
      previewRecords.get(entry.question_id)?.setModelName?.(text);
    }
  }
}

// Metadata completion paints only the matching questions'
// original disclosures, under the same fences as model names:
// never a snapshot adoption, draft, lock or status change.
function paintOriginalsForRequest(kind, requestId) {
  if (kind !== "questions" || !currentSnapshot) {
    return;
  }
  for (const question of currentSnapshot.questions || []) {
    if (requestIdOf(question) === requestId) {
      questionRecords.get(question.question_id)
        ?.setOriginalMaterial?.(originalViewFor(question));
    }
  }
  for (const entry of currentSnapshot.preview_items || []) {
    const joined = findQuestion(currentSnapshot, entry.question_id);
    if (joined && requestIdOf(joined) === requestId) {
      previewRecords.get(entry.question_id)
        ?.setOriginalMaterial?.(originalViewFor(joined));
    }
  }
}

function ensureModel(kind, requestId) {
  if (!requestId || !currentSnapshot || !activeSourceId
      || workspace.isOutcomeUncertain()) {
    return Promise.resolve();
  }
  const key = metadataKey(kind, requestId);
  const existing = modelByRequest.get(key);
  if (existing) {
    return existing.promise || Promise.resolve();
  }
  const sourceId = activeSourceId;
  const epoch = metadataEpoch;
  const entry = {
    state: "pending", modelName: null, originals: null,
    reviewNotes: null, pageContinues: null, promise: null };
  modelByRequest.set(key, entry);
  entry.promise = (async () => {
    let modelName = null;
    let originals = null;
    let sourceNotes = null;
    try {
      const raw = await workspace.getJson(
        `/api/session/requests/${encodeURIComponent(requestId)}/raw`);
      modelName = validatedModelName(raw, requestId, kind);
      originals = validatedOriginalItems(raw, requestId, kind);
      // General source notes ride this same single raw read;
      // no second fetch or authority is created for them.
      sourceNotes = validatedSourceNotes(raw, requestId, kind);
    } catch {
      modelName = null;
      originals = null;
      sourceNotes = null;
    }
    // A late read for an old source, or after sticky uncertainty,
    // must not re-label what is now displayed.
    if (epoch !== metadataEpoch || activeSourceId !== sourceId
        || !currentSnapshot || currentSnapshot.source_id !== sourceId
        || workspace.isOutcomeUncertain()
        || modelByRequest.get(key) !== entry) {
      if (modelByRequest.get(key) === entry
          && entry.state === "pending") {
        entry.state = "unavailable";
      }
      return;
    }
    if (!requestStillPresent(kind, requestId)) {
      entry.state = "unavailable";
      entry.modelName = null;
      return;
    }
    entry.state = modelName ? "known" : "unavailable";
    entry.modelName = modelName;
    entry.originals = originals;
    entry.reviewNotes = sourceNotes ? sourceNotes.notes : null;
    entry.pageContinues = sourceNotes
      ? sourceNotes.pageContinues : null;
    paintModelForRequest(kind, requestId, modelName);
    paintOriginalsForRequest(kind, requestId);
    paintSourceNotesForRequest(kind, requestId);
  })();
  return entry.promise;
}


function ensureSnapshotProvenance(snapshot) {
  if (!snapshot) {
    return;
  }
  const seen = new Set();
  const queue = (kind, requestId) => {
    if (!requestId) {
      return;
    }
    const key = metadataKey(kind, requestId);
    if (!seen.has(key)) {
      seen.add(key);
      void ensureModel(kind, requestId);
    }
  };
  queue("source", sourceSummaryRequestId);
  for (const region of snapshot.regions || []) {
    queue("source", requestIdOf(region));
  }
  for (const question of snapshot.questions || []) {
    queue("questions", requestIdOf(question));
  }
}

function requestEntries(snapshot) {
  return Array.isArray(snapshot?.requests) ? snapshot.requests : [];
}

// The actual request projection: state is exactly "working",
// "terminal" or "superseded". No alias or timestamp inference.
function isWorkingEntry(entry) {
  return Boolean(entry && typeof entry === "object"
    && entry.state === "working");
}

function hasWorkingRequest(snapshot) {
  return requestEntries(snapshot).some(isWorkingEntry);
}

function detailCode(detail) {
  return detail && typeof detail === "object"
    && typeof detail.code === "string" ? detail.code : "";
}

function detailMessage(detail) {
  return detail && typeof detail === "object"
    && typeof detail.message === "string" && detail.message
    ? detail.message : "";
}

function focusIfUsable(element) {
  if (element && element.isConnected && !element.disabled) {
    element.focus();
  }
}

function itemButton(record, testId) {
  return record
    ? record.node.querySelector(`[data-testid='${testId}']`) : null;
}

function regionRecordFor(region) {
  let record = regionRecords.get(region.region_id);
  if (!record) {
    record = createRegionItem(region, callbacks);
    regionRecords.set(region.region_id, record);
  }
  return record;
}

function questionRecordFor(question) {
  let record = questionRecords.get(question.question_id);
  if (!record) {
    record = createQuestionCard(question, callbacks);
    questionRecords.set(question.question_id, record);
  }
  return record;
}

function previewRecordFor(entry) {
  let record = previewRecords.get(entry.question_id);
  if (!record) {
    record = createPreviewItem(entry, callbacks);
    previewRecords.set(entry.question_id, record);
  }
  return record;
}

function editorControls(record) {
  return [
    [record.fields.question, "question"],
    [record.fields.guidance, "answer_guidance"],
    [record.fields.cognitive, "cognitive_explanation"],
    [record.fields.grade, "grade"],
    [record.fields.objective, "objective"],
    [record.fields.level, "bloom_level"],
  ];
}

function syncEditorIfIdle(record, draft) {
  for (const [control, key] of editorControls(record)) {
    if (document.activeElement !== control && control.value !== draft[key]) {
      control.value = draft[key];
    }
  }
}

function slotText(entry, level, outcome) {
  const bloom = BLOOM_ARABIC[level] || level;
  if (outcome === "draft") {
    return `${bloom}: وصلت مسودة`;
  }
  if (outcome === "pending") {
    // request_state is the exact owning questions request's
    // state, joined by renderSlotOutcomes; anything else is
    // unconfirmed rather than an inferred closure.
    if (entry.request_state === "working") {
      return `${bloom}: بانتظار نتيجة طلب التوليد`;
    }
    if (entry.request_state === "terminal"
        || entry.request_state === "superseded") {
      return `${bloom}: أُغلق طلب التوليد دون نتيجة لهذا المستوى`;
    }
    return `${bloom}: حالة نتيجة هذا المستوى غير مؤكدة`;
  }
  const reason = [entry.abstain_reason, entry.reason, entry.message]
    .find((value) => typeof value === "string" && value) || "";
  if (outcome === "abstain" || outcome === "abstained") {
    return `${bloom}: امتنع النموذج — ${reason}`;
  }
  return reason ? `${bloom}: ${reason}` : `${bloom}: ${outcome}`;
}

function renderSlotOutcomes(entries) {
  const rows = (Array.isArray(entries) ? entries : []).map((raw) => {
    const entry = raw && typeof raw === "object" ? raw : {};
    const level = entry.level ?? entry.bloom_level
      ?? entry.current_level ?? "";
    const outcome = entry.outcome ?? entry.status ?? "";
    // A pending slot's text follows its exact owning questions
    // request in the current snapshot, joined by request_id and
    // kind only; the row's data-outcome stays "pending"
    // whatever that owner state is.
    let requestState = null;
    if (String(outcome) === "pending") {
      const requestId = requestIdOf(entry);
      const owner = requestId
        ? requestEntries(currentSnapshot).find((request) =>
          request && typeof request === "object"
          && request.request_id === requestId
          && request.kind === "questions")
        : null;
      requestState = owner && typeof owner.state === "string"
        ? owner.state : null;
    }
    const row = document.createElement("div");
    row.dataset.testid = "slot-outcome";
    row.dataset.slotId = String(entry.slot_id ?? "");
    row.dataset.level = String(level);
    row.dataset.outcome = String(outcome);
    const text = document.createElement("span");
    text.dataset.testid = "slot-outcome-text";
    text.textContent = slotText(
      { ...entry, request_state: requestState },
      String(level), String(outcome));
    row.append(text);
    return row;
  });
  slotOutcomes.replaceChildren(...rows);
}

function pruneRecords(records, drafts, keepIds) {
  for (const id of [...records.keys()]) {
    if (!keepIds.has(id)) {
      records.delete(id);
      if (drafts) {
        drafts.delete(id);
      }
    }
  }
  if (drafts) {
    for (const id of [...drafts.keys()]) {
      if (!keepIds.has(id)) {
        drafts.delete(id);
      }
    }
  }
}

function setNodeControlDisabled(node, testId, disabled) {
  const control = node.querySelector(`[data-testid='${testId}']`);
  if (control) {
    control.disabled = disabled;
  }
}

function setNodeControlsDisabled(node, testId, disabled) {
  for (const control of node.querySelectorAll(
      `[data-testid='${testId}']`)) {
    control.disabled = disabled;
  }
}

function setFieldHidden(element, hidden) {
  element.hidden = hidden;
  element.style.display = hidden ? "none" : "";
}

// While noncurrent (and not busy/uncertain), the renderer has locked
// the whole card; re-enable only local draft typing and read-only
// navigation. Server write controls stay disabled under locked().
function applyRegionCurrency(record) {
  if (!isNoncurrentOnly()) {
    return;
  }
  for (const testId of ["region-edit", "region-current-text-input",
      "region-current-label-input", "region-inclusion-input",
      "region-review-cancel", "region-evidence-link"]) {
    setNodeControlDisabled(record.node, testId, false);
  }
  setNodeControlsDisabled(
    record.node, "region-linked-question", false);
}


function applyQuestionCurrency(record) {
  const node = record.node;
  const noncurrent = !reviewCurrency.current;
  const status = node.querySelector("[data-testid='question-status']");
  const statusOwner = status ? (status.closest("p") || status) : null;
  if (status) {
    setFieldHidden(status, noncurrent);
  }
  if (statusOwner && statusOwner !== status) {
    setFieldHidden(statusOwner, noncurrent);
  }
  const decision = node.querySelector(
    "[data-testid='question-decision']");
  if (decision) {
    setFieldHidden(decision, noncurrent);
  }
  for (const block of node.querySelectorAll(
      "[data-testid='question-block']")) {
    setFieldHidden(block, noncurrent);
  }
  if (!isNoncurrentOnly()) {
    return;
  }
  for (const testId of ["question-edit", "question-edit-cancel",
      "question-edit-question", "question-edit-guidance",
      "question-edit-cognitive", "question-edit-grade",
      "question-edit-objective", "question-edit-level"]) {
    setNodeControlDisabled(node, testId, false);
  }
  setNodeControlsDisabled(node, "question-evidence-source", false);
  setNodeControlsDisabled(node, "question-order-source", false);
}

// The empty-preview reason is derived from current question
// states only: outdated prior approvals first, then rejections,
// else the exact initial guidance. A rejection never implies
// content was removed; the cards themselves stay readable.
function emptyPreviewText(questions) {
  if (questions.some((question) => question.status === "outdated")) {
    return OUTDATED_PREVIEW_TEXT;
  }
  if (questions.some((question) => question.status === "rejected")) {
    return REJECTED_PREVIEW_TEXT;
  }
  if (!questions.length) {
    return "لا أسئلة معتمدة حاليًا؛ لا توجد أسئلة مولّدة بعد في هذا المصدر.";
  }
  return defaultPreviewEmptyText;
}


function paintSourceSelection() {
  if (!sourceSelectedText) {
    return;
  }
  const region = selectedRegionId
    ? findRegion(currentSnapshot, selectedRegionId) : null;
  if (!region) {
    selectedRegionId = null;
    sourceSelectedText.textContent = "";
    if (sourceSelectedContext) {
      sourceSelectedContext.textContent = "";
    }
    return;
  }
  sourceSelectedText.textContent = region.current_text;
  if (sourceSelectedContext) {
    const flags = Array.isArray(region.model_flags)
      ? region.model_flags : [];
    const position = Number.isFinite(region.display_position)
      ? arabicIndic(region.display_position) : "";
    sourceSelectedContext.textContent =
      `النص الحالي للمقطع ${position} · ${region.current_label} · `
      + `${INCLUSION_ARABIC[region.inclusion] || region.inclusion}`
      + (flags.length ? ` · تنبيهات النموذج: ${flags.join("، ")}` : "");
  }
}

function renderView() {
  const snapshot = currentSnapshot;
  const isLocked = locked();
  const regions = snapshot ? orderedRegions(snapshot.regions) : [];
  const questions = Array.isArray(snapshot?.questions)
    ? snapshot.questions : [];
  const questionsById = new Map(
    questions.map((question) => [question.question_id, question]));
  currencyStatus.hidden = reviewCurrency.current || !snapshot
    || workspace.isOutcomeUncertain();
  currencyStatus.textContent = reviewCurrency.reason === "lease"
    ? workspace.sessionExpiredMessage()
    : reviewCurrency.reason === "identity"
      ? IDENTITY_CURRENCY_STATUS_TEXT : CURRENCY_STATUS_TEXT;
  // The workspace owns the active workflow step; this repaint
  // only reports availability derived from the authoritative
  // snapshot. It never selects a step (in particular it never
  // auto-switches to preview from an approved count), so a
  // first approval or a snapshot repaint cannot steal the step.
  updateWorkflowAvailability(snapshot, regions, questions);
  // Retry visibility in the workspace operation group follows
  // this same currentness authority; refresh it whenever the
  // review view repaints for a currency-relevant change.
  workspace.refreshProviderOperationControls();
  workspace.refreshStandaloneSettingsControl();
  ensureSnapshotProvenance(snapshot);
  const linkedStatusVisible = reviewCurrency.current
    && !workspace.isOutcomeUncertain();

  const regionNodes = regions.map((region) => {
    if (!regionDrafts.has(region.region_id)) {
      regionDrafts.set(region.region_id, regionDraftFrom(region));
    }
    const linked = (Array.isArray(region.linked_question_ids)
      ? region.linked_question_ids : [])
      .map((questionId) => questionsById.get(questionId))
      .filter(Boolean);
    const record = regionRecordFor(region);
    record.update(
      region, regionDrafts.get(region.region_id), isLocked, linked,
      provenanceFor("source", region), linkedStatusVisible);
    applyRegionCurrency(record);
    return record.node;
  });
  orderChildren(regionList, regionNodes);
  pruneRecords(regionRecords, regionDrafts,
    new Set(regions.map((region) => region.region_id)));
  paintSourceSelection();
  sourceReviewEmpty.hidden = regions.length > 0;
  sourceCounts.textContent =
    `المضمّنة: ${arabicIndic(snapshot?.included_count ?? 0)} · المحتجبة: ${arabicIndic(snapshot?.withheld_count ?? 0)}`;
  const canGenerate = Boolean(snapshot?.can_generate);
  generateGateStatus.textContent = canGenerate ? GATE_READY : GATE_UNAVAILABLE;
  generateButton.disabled = isLocked || !canGenerate
    || hasWorkingRequest(snapshot);
  // Stored raw notes repaint from their matched entries only;
  // a slow or unavailable read never blocks source review.
  paintSourceNotes();

  const summaryCount = renderQuestionRequestSummaries(snapshot);
  renderSlotOutcomes(snapshot?.slot_outcomes);
  const questionNodes = questions.map((question) => {
    const record = questionRecordFor(question);
    record.update(
      question, isLocked, provenanceFor("questions", question),
      isQuestionDirty(question.question_id, currentSnapshot));
    applyQuestionCurrency(record);
    return record.node;
  });
  orderChildren(questionList, questionNodes);
  const questionIds = new Set(
    questions.map((question) => question.question_id));
  pruneRecords(questionRecords, questionDrafts, questionIds);
  for (const id of [...openEditors]) {
    if (!questionIds.has(id)) {
      openEditors.delete(id);
    }
  }
  // A readable exact-request summary is the outcome state for a
  // zero-draft request; the generic empty line must not stand
  // as if a successful generation were still the only account.
  questionReviewEmpty.hidden = questions.length > 0 || summaryCount > 0;

  if (!reviewCurrency.current || previewSuppressed || !snapshot) {
    orderChildren(previewList, []);
    previewEmpty.hidden = false;
    previewEmpty.textContent =
      (!reviewCurrency.current || previewSuppressed)
        ? STALE_PREVIEW_TEXT : emptyPreviewText(questions);
    return;
  }
  const entries = Array.isArray(snapshot.preview_items)
    ? snapshot.preview_items : [];
  const previewNodes = entries.map((entry) => {
    const record = previewRecordFor(entry);
    const joined = questionsById.get(entry.question_id);
    record.update(
      entry, joined, provenanceFor("questions", joined));
    return record.node;
  });
  orderChildren(previewList, previewNodes);
  pruneRecords(previewRecords, null,
    new Set(entries.map((entry) => entry.question_id)));
  previewEmpty.hidden = previewNodes.length > 0;
  if (!previewNodes.length) {
    previewEmpty.textContent = emptyPreviewText(questions);
  }
}


function adoptSnapshot(snapshot, options = {}) {
  if (workspace.isOutcomeUncertain()
      || !workspace.isUsableSnapshot(snapshot)) {
    return false;
  }
  if (activeSourceId && snapshot.source_id !== activeSourceId) {
    return false;
  }
  if (workspace.isSessionExpired()
      || reviewCurrency.reason === "lease") {
    // A late same-source acknowledgment after sticky lease
    // expiry keeps the last-known snapshot, keyed drafts and
    // open editors exactly as displayed. Its owner may still
    // acknowledge exact closure, but this snapshot is not
    // applied here and currentness is not restored.
    reviewCurrency.current = false;
    reviewCurrency.reason = "lease";
    renderView();
    return false;
  }
  const old = currentSnapshot;
  const dirtyRegions = new Set();
  const dirtyQuestions = new Set();
  if (old) {
    for (const id of regionDrafts.keys()) {
      if (isRegionDirty(id, old)) {
        dirtyRegions.add(id);
      }
    }
    for (const id of questionDrafts.keys()) {
      if (isQuestionDirty(id, old)) {
        dirtyQuestions.add(id);
      }
    }
  }
  currentSnapshot = snapshot;
  activeSourceId = snapshot.source_id;
  previewSuppressed = false;
  const settingsMatch = !reviewCurrency.acknowledgedSettings
    || snapshotMatchesSettings(
      snapshot, reviewCurrency.acknowledgedSettings);
  if (settingsMatch) {
    reviewCurrency.acknowledgedSettings = null;
  }
  if (reviewCurrency.reason === "identity") {
    // Sticky: an accidental old matching-settings snapshot is
    // still only last-known content. Only the explicit
    // acknowledged source-start reinitialization clears this.
    reviewCurrency.current = false;
  } else if (settingsMatch) {
    reviewCurrency.current = true;
    reviewCurrency.reason = null;
  } else {
    reviewCurrency.current = false;
    reviewCurrency.reason = "settings";
  }
  for (const region of orderedRegions(snapshot.regions)) {
    if (region.region_id === options.resetRegionId
        || !dirtyRegions.has(region.region_id)
        || !regionDrafts.has(region.region_id)) {
      regionDrafts.set(region.region_id, regionDraftFrom(region));
    }
  }
  for (const question of snapshot.questions) {
    const id = question.question_id;
    if (id === options.resetQuestionId) {
      questionDrafts.delete(id);
      openEditors.delete(id);
      questionRecords.get(id)?.closeEditor();
    } else if (questionDrafts.has(id) && !dirtyQuestions.has(id)) {
      const draft = questionDraftFrom(question);
      questionDrafts.set(id, draft);
      const record = questionRecords.get(id);
      if (record && openEditors.has(id)) {
        syncEditorIfIdle(record, draft);
      }
    }
  }
  renderView();
  return true;
}

function clearReviewForSource(sourceId) {
  if (workspace.isSessionExpired()
      || reviewCurrency.reason === "lease") {
    // A late acknowledged Start cannot heal sticky lease expiry
    // or erase the displayed source, keyed drafts or open
    // editors. Keep that last-known work readable and
    // noncurrent; only an explicit reload starts anew.
    reviewCurrency.current = false;
    reviewCurrency.reason = "lease";
    renderView();
    return;
  }
  activeSourceId = sourceId;
  currentSnapshot = null;
  selectedRegionId = null;
  previewSuppressed = false;
  reviewCurrency.current = true;
  reviewCurrency.acknowledgedSettings = null;
  reviewCurrency.reason = null;
  // Source-scoped provenance resets only on an acknowledged new
  // Start, never on selection or a definite failure.
  metadataEpoch += 1;
  sourceSummaryRequestId = null;
  modelByRequest.clear();
  regionRecords.clear();
  regionDrafts.clear();
  questionRecords.clear();
  questionDrafts.clear();
  openEditors.clear();
  previewRecords.clear();
  sourceModelName.textContent = MODEL_UNAVAILABLE;
  renderView();
}

function handleBusy(busy) {
  isBusy = busy;
  renderView();
}

function handleSourceStarted(metadata) {
  if (metadata && typeof metadata.source_id === "string") {
    clearReviewForSource(metadata.source_id);
  }
}

// Exact closed provider operation -> snapshot adoption. The
// workspace operation owner calls this only with a snapshot whose
// source and closed request it already matched exactly; adoption
// (including the acknowledged-settings check inside
// adoptSnapshot) remains the currentness authority here. Returns
// whether the snapshot was adopted, so the owner releases the
// operation's logical wait only after reconciliation.
async function handleProviderSnapshot(info) {
  if (!info || workspace.isOutcomeUncertain()
      || !workspace.isUsableSnapshot(info.snapshot)
      || info.snapshot.source_id !== activeSourceId
      || info.snapshot.source_id !== info.sourceId) {
    return false;
  }
  if (info.kind !== "source" || info.outcome !== "ok") {
    const adopted = adoptSnapshot(info.snapshot);
    handoffAdoptedProviderStep(info, adopted);
    return adopted;
  }
  // The summary keeps its old meaning: the model of this source
  // analysis request only, never a global latest model.
  sourceSummaryRequestId =
    typeof info.requestId === "string" && info.requestId
      ? info.requestId : null;
  if (!sourceSummaryRequestId) {
    sourceModelName.textContent = MODEL_UNAVAILABLE;
    const adopted = adoptSnapshot(info.snapshot);
    handoffAdoptedProviderStep(info, adopted);
    return adopted;
  }
  // A known non-retained envelope decides provenance without a
  // raw read. An unknown retention (status-recovered snapshot)
  // lets the epoch-fenced raw read decide for itself.
  if (info.rawRetention !== null && info.rawRetention !== undefined
      && info.rawRetention !== "retained") {
    modelByRequest.set(
      metadataKey("source", sourceSummaryRequestId),
      { state: "unavailable", modelName: null, originals: null,
        reviewNotes: null, pageContinues: null,
        promise: Promise.resolve() });
    sourceModelName.textContent = MODEL_UNAVAILABLE;
    const adopted = adoptSnapshot(info.snapshot);
    handoffAdoptedProviderStep(info, adopted);
    return adopted;
  }
  const adopted = adoptSnapshot(info.snapshot);
  if (adopted) {
    // Provenance is read-only decoration of already-adopted
    // work: start the existing keyed/epoch-fenced raw read but
    // never await it here, so settlement releases the logical
    // wait and completed work stays usable while raw is slow.
    // Its later completion may paint only this request's model
    // attribution (or the honest unavailable state).
    void ensureModel("source", sourceSummaryRequestId);
  }
  handoffAdoptedProviderStep(info, adopted);
  return adopted;
}


// The review-currentness permission the workspace operation
// owner consults for provider sends it drives itself (the
// same-source analysis retry above all): current review state
// for exactly this active source. With no adopted snapshot yet
// (the initial post-Start analysis, just reinitialized by
// clearReviewForSource) a current coordinator for that source
// still permits the send.
function providerSendPermitted(info) {
  if (!info || workspace.isOutcomeUncertain()
      || !reviewCurrency.current
      || !activeSourceId || info.sourceId !== activeSourceId) {
    return false;
  }
  return !currentSnapshot
    || currentSnapshot.source_id === info.sourceId;
}

// The standalone settings permission the workspace settings
// control consults at rendering, activation and final send:
// only the sticky identity reason blocks it. A settings-reason
// withdrawal (including initial no-source setup, which has no
// withdrawal) leaves the deliberate correction/readback path
// open. This is a pure query of the one owner above; rendering
// and notification never recurse through it.
function standaloneSettingsPermitted() {
  return reviewCurrency.reason !== "identity"
    && reviewCurrency.reason !== "lease"
    && !workspace.isSessionExpired();
}

// A definite refusal that invalidates this page's session/source
// identity (401 session required, 403 forbidden request, 409
// source changed where the stage supports it) — from the
// provider lane or an ordinary settings/Start/region/question
// send — proves only that the send was not admitted; it cannot
// certify the displayed workspace as current. Withdraw
// currentness in this same owner, keeping last-known content
// and drafts readable without any current approval/preview
// claim. Ordinary refusals never reach here, and a notification
// whose captured source or UI generation has moved on is stale.
function handleIdentityInvalidatingRefusal(info) {
  if (workspace.isOutcomeUncertain()
      || (info && info.sourceId && activeSourceId
        && info.sourceId !== activeSourceId)
      || (info && typeof info.generation === "number"
        && info.generation !== workspace.currentGeneration())) {
    return;
  }
  reviewCurrency.current = false;
  if (workspace.isSessionExpired()
      || reviewCurrency.reason === "lease") {
    // Sticky lease expiry outranks a later identity refusal:
    // replacing it with the source-start-clearable identity
    // reason would unblock expiry.
    reviewCurrency.reason = "lease";
  } else {
    reviewCurrency.reason = "identity";
  }
  renderView();
}

async function handleSettingsAcknowledged(settings) {
  if (!workspace.hasCommittedSource() || workspace.isOutcomeUncertain()) {
    return;
  }
  if (workspace.isSessionExpired()
      || reviewCurrency.reason === "lease") {
    // A late settings acknowledgment cannot heal sticky lease
    // expiry, start a follow-on snapshot read, or reset the
    // retained snapshot and unsent drafts.
    reviewCurrency.current = false;
    reviewCurrency.reason = "lease";
    renderView();
    return;
  }
  const expectedSourceId = workspace.currentSourceId() || activeSourceId;
  if (!expectedSourceId) {
    return;
  }
  // An exact no-op needs no failure-prone read, but only while the
  // retained snapshot is already proven current for this source.
  // Equality alone never revives a withdrawn snapshot.
  if (reviewCurrency.current && currentSnapshot
      && currentSnapshot.source_id === expectedSourceId
      && activeSourceId === expectedSourceId
      && snapshotMatchesSettings(currentSnapshot, settings)) {
    return;
  }
  // Withdraw currentness synchronously, before any await, and show
  // that withdrawal now. The retained snapshot is left unmutated.
  reviewCurrency.current = false;
  if (reviewCurrency.reason !== "identity"
      && reviewCurrency.reason !== "lease") {
    reviewCurrency.reason = "settings";
  }
  reviewCurrency.acknowledgedSettings = {
    grade: settings.grade, objective: settings.objective };
  renderView();
  let snapshot = null;
  try {
    snapshot = await workspace.getJson("/api/session/snapshot");
  } catch {
    snapshot = null;
  }
  if (workspace.isOutcomeUncertain()
      || workspace.isSessionExpired()
      || workspace.currentSourceId() !== expectedSourceId
      || activeSourceId !== expectedSourceId) {
    // Expiry during the read leaves the read completion
    // read-only: never adopt it as current state.
    return;
  }
  const acknowledged = reviewCurrency.acknowledgedSettings;
  if (!acknowledged || acknowledged.grade !== settings.grade
      || acknowledged.objective !== settings.objective) {
    return;
  }
  if (workspace.isUsableSnapshot(snapshot)
      && snapshot.source_id === expectedSourceId
      && snapshotMatchesSettings(snapshot, settings)) {
    adoptSnapshot(snapshot);
    return;
  }
  renderView();
}

function handleUncertain() {
  metadataEpoch += 1;
  for (const entry of modelByRequest.values()) {
    if (entry.state === "pending") {
      entry.state = "unavailable";
      entry.modelName = null;
    }
  }
  previewSuppressed = true;
  renderView();
}

function handleRegionEvidence(regionId) {
  if (workspace.isOutcomeUncertain()) {
    return;
  }
  // The exact region identity rides along to the whole-page
  // source reference; its current text is shown there without
  // inventing any position, box or highlight.
  selectedRegionId = typeof regionId === "string" && regionId
    ? regionId : null;
  paintSourceSelection();
  const invoking = document.activeElement;
  workspace.showSourceEvidence(
    invoking && invoking !== document.body ? invoking : null);
}

function handleShowQuestion(questionId) {
  const record = questionRecords.get(questionId);
  if (!record) {
    return;
  }
  workspace.showWork();
  workspace.selectWorkflowStep?.("questions");
  record.heading.focus();
}


function handleRegionDraft(regionId, field, value) {
  let draft = regionDrafts.get(regionId);
  if (!draft) {
    const region = findRegion(currentSnapshot, regionId);
    if (!region) {
      return;
    }
    draft = regionDraftFrom(region);
    regionDrafts.set(regionId, draft);
  }
  draft[field] = value;
}

function handleRegionCancel(regionId) {
  const region = findRegion(currentSnapshot, regionId);
  if (!region) {
    return;
  }
  regionDrafts.set(regionId, regionDraftFrom(region));
  regionRecords.get(regionId)?.closeEditor?.();
  renderView();
}


function handleQuestionDraft(questionId, key, value) {
  let draft = questionDrafts.get(questionId);
  if (!draft) {
    const question = findQuestion(currentSnapshot, questionId);
    if (!question) {
      return;
    }
    draft = questionDraftFrom(question);
    questionDrafts.set(questionId, draft);
  }
  draft[key] = value;
  questionRecords.get(questionId)?.setDirtyState?.(
    isQuestionDirty(questionId, currentSnapshot));
}


function handleQuestionEditToggle(questionId) {
  const question = findQuestion(currentSnapshot, questionId);
  const record = questionRecords.get(questionId);
  if (!question || !record || isBusy
      || workspace.isOutcomeUncertain()) {
    return;
  }
  if (openEditors.has(questionId)) {
    openEditors.delete(questionId);
    record.closeEditor();
    return;
  }
  const existing = questionDrafts.get(questionId);
  const draft = existing && isQuestionDirty(questionId, currentSnapshot)
    ? existing : questionDraftFrom(question);
  questionDrafts.set(questionId, draft);
  openEditors.add(questionId);
  record.openEditor(question);
  if (draft === existing) {
    for (const [control, key] of editorControls(record)) {
      control.value = draft[key];
    }
    record.fields.question.focus();
  }
}

function handleQuestionEditCancel(questionId) {
  const record = questionRecords.get(questionId);
  if (!record) {
    return;
  }
  questionDrafts.delete(questionId);
  openEditors.delete(questionId);
  record.closeEditor();
  renderView();
  record.heading.focus();
}

function assertSendable(expectedSourceId) {
  if (workspace.isOutcomeUncertain() || !reviewCurrency.current
      || !currentSnapshot
      || currentSnapshot.source_id !== expectedSourceId
      || activeSourceId !== expectedSourceId) {
    throw ABORTED;
  }
}

async function sendSnapshotMutation(
    stage, url, method, payload, expectedSourceId, finalSendCheck) {
  await workspace.ensureSession();
  assertSendable(expectedSourceId);
  if (typeof finalSendCheck === "function"
      && finalSendCheck() !== true) {
    throw ABORTED;
  }
  const response = await workspace.apiFetch(
    stage, url, jsonMutationOptions(method, payload));
  const body = await workspace.readJsonBody(stage, response);
  if (!body || typeof body !== "object"
      || !workspace.isUsableSnapshot(body.snapshot)
      || body.snapshot.source_id !== expectedSourceId) {
    throw new Error("unusable snapshot");
  }
  if (workspace.isOutcomeUncertain()
      || activeSourceId !== expectedSourceId) {
    throw ABORTED;
  }
  return body.snapshot;
}


function definiteMessage(error, fallback) {
  if (workspace.isSessionExpiredError(error)) {
    return workspace.sessionExpiredMessage();
  }
  const status = error?.status;
  if (status === 401 || status === 403) {
    return SESSION_MESSAGE;
  }
  if (error?.stage === "session") {
    return "تعذّر بدء جلسة هذا المتصفح. تحقّق من الخادم المحلي ثم حاول مجددًا بضغطة جديدة.";
  }
  if (status === 409
      && detailCode(error?.detail) === "approval_blocked"
      && detailMessage(error?.detail)) {
    return detailMessage(error.detail);
  }
  if (status === 404) {
    return "لم يعد هذا العنصر متاحًا في المصدر الحالي. لم تتغيّر المسودات المعروضة؛ راجع العناصر الحالية ثم حاول بضغطة جديدة.";
  }
  return fallback;
}

function handleMutationError(error, fallback) {
  if (error === ABORTED) {
    return "aborted";
  }
  if (workspace.isUncertainOutcome(error)) {
    workspace.enterUncertainOutcome();
    return "uncertain";
  }
  workspace.setStatus(definiteMessage(error, fallback));
  return "definite";
}

function jsonMutationOptions(method, payload) {
  return {
    method,
    headers: workspace.mutationHeaders(
      { "Content-Type": "application/json" }),
    body: JSON.stringify(payload),
  };
}

function handleRegionCommit(regionId) {
  const expectedSourceId = currentSnapshot?.source_id;
  const draft = regionDrafts.get(regionId);
  if (!expectedSourceId || !draft
      || !LEGAL_INCLUSIONS.includes(draft.inclusion)
      || !reviewCurrency.current
      || workspace.isOutcomeUncertain()) {
    return;
  }
  const payload = {
    inclusion: draft.inclusion,
    current_text: draft.current_text,
    current_label: draft.current_label,
  };
  const button = itemButton(
    regionRecords.get(regionId), "region-review-commit");
  let outcome = "aborted";
  void workspace.runSerialized(async () => {
    try {
      assertSendable(expectedSourceId);
      if (!findRegion(currentSnapshot, regionId)) {
        throw ABORTED;
      }
      const snapshot = await sendSnapshotMutation(
        "region-review",
        `/api/session/regions/${encodeURIComponent(regionId)}`,
        "PUT", payload, expectedSourceId);
      outcome = adoptSnapshot(snapshot, { resetRegionId: regionId })
        ? "success" : "aborted";
      if (outcome === "success") {
        regionRecords.get(regionId)?.closeEditor?.();
        workspace.setStatus(REGION_SAVED);
      }
    } catch (error) {
      outcome = handleMutationError(error,
        "تعذّر حفظ مراجعة المقطع. بقيت مسودة هذا المقطع كما هي؛ راجع النص والتصنيف وقرار التضمين ثم احفظ بضغطة جديدة.");
    }
  }).then(() => {
    if (outcome === "success" || outcome === "definite") {
      focusIfUsable(button);
    }
  });
}


// Position comes only from the acknowledged snapshot order. A
// boundary no-op states the resulting position without claiming
// that a move occurred.
function announceMovePosition(snapshot, regionId, beforeIndex) {
  if (workspace.isOutcomeUncertain()) {
    return;
  }
  const ordered = orderedRegions(snapshot.regions);
  const index = ordered.findIndex(
    (region) => region.region_id === regionId);
  if (index < 0 || !ordered.length) {
    return;
  }
  const position =
    `موضع المقطع: ${arabicIndic(index + 1)} من ${arabicIndic(ordered.length)}`;
  workspace.setStatus(
    index !== beforeIndex ? `نُقل المقطع. ${position}` : position);
}

function handleRegionMove(regionId, direction) {
  const expectedSourceId = currentSnapshot?.source_id;
  const record = regionRecords.get(regionId);
  if (!expectedSourceId || !record || !reviewCurrency.current
      || workspace.isOutcomeUncertain()) {
    return;
  }
  const button = direction === "up" ? record.moveUp : record.moveDown;
  const suffix = direction === "up" ? "move-up" : "move-down";
  let outcome = "aborted";
  void workspace.runSerialized(async () => {
    try {
      assertSendable(expectedSourceId);
      if (!findRegion(currentSnapshot, regionId)) {
        throw ABORTED;
      }
      const beforeIndex = orderedRegions(currentSnapshot.regions)
        .findIndex((region) => region.region_id === regionId);
      const snapshot = await sendSnapshotMutation(
        "region-move",
        `/api/session/regions/${encodeURIComponent(regionId)}/${suffix}`,
        "POST", {}, expectedSourceId);
      outcome = adoptSnapshot(snapshot) ? "success" : "aborted";
      if (outcome === "success") {
        announceMovePosition(snapshot, regionId, beforeIndex);
      }
    } catch (error) {
      outcome = handleMutationError(error,
        "تعذّر نقل المقطع. بقي الترتيب والمسودات كما هي؛ حاول بضغطة جديدة.");
    }
  }).then(() => {
    if (outcome === "success" || outcome === "definite") {
      focusIfUsable(button);
    }
  });
}

function changedQuestionPayload(draft, question) {
  const payload = {};
  for (const [key, field] of QUESTION_EDIT_FIELDS) {
    if (draft[key] !== question[field]) {
      payload[key] = draft[key];
    }
  }
  return payload;
}

function handleQuestionEditCommit(questionId) {
  const expectedSourceId = currentSnapshot?.source_id;
  const question = findQuestion(currentSnapshot, questionId);
  const draft = questionDrafts.get(questionId);
  const record = questionRecords.get(questionId);
  if (!expectedSourceId || !question || !draft || !record
      || !reviewCurrency.current
      || workspace.isOutcomeUncertain()) {
    return;
  }
  if (!Object.keys(changedQuestionPayload(draft, question)).length) {
    questionDrafts.delete(questionId);
    openEditors.delete(questionId);
    record.closeEditor();
    renderView();
    record.heading.focus();
    return;
  }
  const draftCopy = { ...draft };
  let outcome = "aborted";
  void workspace.runSerialized(async () => {
    try {
      assertSendable(expectedSourceId);
      const current = findQuestion(currentSnapshot, questionId);
      if (!current) {
        throw ABORTED;
      }
      const payload = changedQuestionPayload(draftCopy, current);
      if (!Object.keys(payload).length) {
        questionDrafts.delete(questionId);
        openEditors.delete(questionId);
        record.closeEditor();
        renderView();
        outcome = "success";
        return;
      }
      const snapshot = await sendSnapshotMutation(
        "question-edit",
        `/api/session/questions/${encodeURIComponent(questionId)}`,
        "PATCH", payload, expectedSourceId);
      outcome = adoptSnapshot(snapshot, { resetQuestionId: questionId })
        ? "success" : "aborted";
      if (outcome === "success") {
        workspace.setStatus(QUESTION_SAVED);
      }
    } catch (error) {
      outcome = handleMutationError(error,
        "تعذّر حفظ تعديل السؤال. بقيت مسودة التعديل كما هي؛ راجع الحقول ثم احفظ بضغطة جديدة.");
    }
  }).then(() => {
    if (outcome === "success") {
      focusIfUsable(record.heading);
    } else if (outcome === "definite") {
      focusIfUsable(itemButton(record, "question-edit-commit"));
    }
  });
}

function handleQuestionDecision(questionId, action) {
  const expectedSourceId = currentSnapshot?.source_id;
  const record = questionRecords.get(questionId);
  if (!expectedSourceId || !record || !reviewCurrency.current
      || workspace.isOutcomeUncertain()
      || (action === "approve"
        && isQuestionDirty(questionId, currentSnapshot))) {
    return;
  }
  const fallback = action === "approve"
    ? "تعذّر اعتماد السؤال. بقيت حالة السؤال ومسوداته كما هي؛ راجع السؤال ثم اعتمده بضغطة جديدة."
    : "تعذّر رفض السؤال. بقيت حالة السؤال ومسوداته كما هي؛ حاول بضغطة جديدة.";
  let outcome = "aborted";
  void workspace.runSerialized(async () => {
    try {
      assertSendable(expectedSourceId);
      if (!findQuestion(currentSnapshot, questionId)) {
        throw ABORTED;
      }
      if (action === "approve"
          && isQuestionDirty(questionId, currentSnapshot)) {
        throw ABORTED;
      }
      const snapshot = await sendSnapshotMutation(
        "question-decision",
        `/api/session/questions/${encodeURIComponent(questionId)}/${action}`,
        "POST", {}, expectedSourceId,
        action === "approve"
          ? () => !isQuestionDirty(questionId, currentSnapshot)
          : undefined);
      outcome = adoptSnapshot(snapshot) ? "success" : "aborted";
      if (outcome === "success") {
        workspace.setStatus(action === "approve"
          ? QUESTION_APPROVED : QUESTION_REJECTED);
      }
    } catch (error) {
      outcome = handleMutationError(error, fallback);
    }
  }).then(() => {
    if (outcome === "success") {
      focusIfUsable(record.heading);
    } else if (outcome === "definite") {
      focusIfUsable(itemButton(record,
        action === "approve" ? "question-approve" : "question-reject"));
    }
  });
}


function handleQuestionApprove(questionId) {
  handleQuestionDecision(questionId, "approve");
}

function handleQuestionReject(questionId) {
  handleQuestionDecision(questionId, "reject");
}

function generationDefiniteMessage(status) {
  if (status === 401 || status === 403) {
    return SESSION_MESSAGE;
  }
  if (status === 409) {
    return "تعذّر بدء توليد الأسئلة لوجود تعارض في الحالة الحالية. لم تُنشر أسئلة جديدة، وبقيت مراجعة المصدر كما هي.";
  }
  if (status === 429) {
    return "توجد طلبات توليد كثيرة الآن. لم تُنشر أسئلة جديدة؛ انتظر قليلًا ثم حاول بضغطة جديدة.";
  }
  return "تعذّر بدء توليد الأسئلة. لم تُنشر أسئلة جديدة، وبقيت مراجعة المصدر كما هي؛ حاول بضغطة جديدة.";
}

function handleGenerate() {
  const expectedSourceId = currentSnapshot?.source_id;
  if (!expectedSourceId || !reviewCurrency.current
      || workspace.isSessionExpired()
      || workspace.isOutcomeUncertain()
      || !currentSnapshot.can_generate
      || hasWorkingRequest(currentSnapshot)) {
    return;
  }
  // Claim synchronously so a rapid duplicate activation sends
  // nothing; the queued task rechecks eligibility before the
  // operation's single exact POST.
  const operation = workspace.claimProviderOperation(
    "questions", expectedSourceId);
  if (!operation) {
    return;
  }
  let outcome = "aborted";
  void workspace.runSerialized(async () => {
    try {
      // Queue-boundary recheck: expiry while this claim waited
      // makes it unsendable before any session or provider send.
      if (workspace.isSessionExpired()) {
        throw ABORTED;
      }
      await workspace.ensureSession();
      assertSendable(expectedSourceId);
      if (!currentSnapshot.can_generate
          || hasWorkingRequest(currentSnapshot)) {
        throw ABORTED;
      }
      const result = await workspace.executeProviderOperation(
        operation);
      const ownsUi = operation.generation
          === workspace.currentGeneration()
        && activeSourceId === expectedSourceId
        && !workspace.isOutcomeUncertain()
        && !workspace.isSessionExpired();
      if (result.type === "success") {
        outcome = "success";
        if (ownsUi) {
          workspace.setStatus(generationSuccessText(
            operation.requestId, result.snapshot));
        }
      } else if (result.type === "terminal") {
        outcome = "terminal";
        if (ownsUi) {
          workspace.setStatus(result.outcome === "cancelled"
            ? GENERATION_CANCELLED
            : generationTerminalText(
              result.outcome, result.detail));
        }
      } else if (result.type === "refusal") {
        outcome = "definite";
        if (ownsUi) {
          workspace.setStatus(
            detailCode(result.detail) === "provider_unavailable"
              ? GENERATION_UNAVAILABLE
              : generationDefiniteMessage(result.httpStatus));
        }
      }
    } catch (error) {
      outcome = handleMutationError(error,
        generationDefiniteMessage(error?.status));
    } finally {
      workspace.finishProviderOperation(operation);
    }
  }).then(() => {
    // A newer mutation or operation owns focus now; this settled
    // operation must not steal it back.
    if (operation.generation !== workspace.currentGeneration()
        || activeSourceId !== expectedSourceId
        || workspace.isOutcomeUncertain()
        || workspace.isSessionExpired()) {
      return;
    }
    if ((outcome === "success" || outcome === "terminal")
        && hasAdoptedCurrentRequest(
          "questions", operation.requestId, expectedSourceId)) {
      workspace.selectWorkflowStep?.("questions");
      workspace.showWork();
      focusIfUsable(questionReviewHeading);
    } else if (outcome === "success") {
      focusIfUsable(questionReviewHeading);
    } else if (outcome === "terminal" || outcome === "definite") {
      if (!generateButton.disabled) {
        generateButton.focus();
      } else {
        focusIfUsable(sourceReviewHeading);
      }
    }
  });
}


workspace.onBusy(handleBusy);
workspace.onSettingsAcknowledged(handleSettingsAcknowledged);
workspace.onSourceStarted(handleSourceStarted);
workspace.onProviderSnapshot(handleProviderSnapshot);
workspace.onProviderSendPermission(providerSendPermitted);
workspace.onIdentityInvalidatingRefusal(
  handleIdentityInvalidatingRefusal);
workspace.onStandaloneSettingsPermission(standaloneSettingsPermitted);
workspace.onUncertain(handleUncertain);
workspace.onSessionExpired(() => {
  // Sticky lease withdrawal in the one currentness owner only:
  // displayed source, keyed drafts and open editors stay
  // readable as last-known work. Nothing is cleared, settled,
  // cancelled or resent here; renderView withdraws current
  // approval/preview and locks new writes.
  reviewCurrency.current = false;
  reviewCurrency.reason = "lease";
  renderView();
});
generateButton?.addEventListener("click", handleGenerate);
previewReturn?.addEventListener("click", () => {
  workspace.showWork();
  workspace.selectWorkflowStep?.("questions");
  focusIfUsable(questionReviewHeading);
});

if (workspace.isOutcomeUncertain()) {
  previewSuppressed = true;
}
renderView();
