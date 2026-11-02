// Keyed DOM builders for the review stages. This module creates
// and updates nodes only: it never fetches, never computes a
// server predicate, and writes every arbitrary string with
// textContent or form values.

export const BLOOM_ARABIC = {
  remember: "تذكّر", understand: "فهم", apply: "تطبيق",
  analyze: "تحليل", evaluate: "تقييم", create: "إنشاء",
};
export const INCLUSION_ARABIC = {
  unreviewed: "بانتظار المراجعة", included: "مشمول",
  excluded: "مستبعد", unresolved: "غير محسوم",
};
export const AUTHOR_ARABIC = { model: "النموذج", teacher: "المعلم" };
export const CURRENT_AUTHOR_ARABIC = {
  model: "اقتراح النموذج كما ورد", teacher: "بصياغة المعلم الحالية",
};
export const STATUS_ARABIC = {
  draft: "مسودة", approved: "معتمد", rejected: "مرفوض",
  outdated: "يحتاج إلى تحديث",
};
export const DECISION_ARABIC = {
  draft: "قرار المعلم: بانتظار القرار",
  approved: "قرار المعلم: معتمد",
  rejected: "قرار المعلم: مرفوض",
  outdated: "قرار المعلم: كان معتمدًا ويحتاج إلى مراجعة",
};
export const LEGAL_INCLUSIONS = ["included", "excluded", "unresolved"];
// The one rendering location for backend approval-block copy.
// Each chip keeps its data-block technical identity; teachers see
// what needs review. An unknown code gets an honest unavailable
// explanation, never an invented reason or the raw code as prose.
export const BLOCK_ARABIC = {
  blank_field:
    "توجد حقول فارغة في هذا السؤال؛ أكملها قبل الاعتماد.",
  grade_objective_mismatch:
    "لا يطابق صف هذا السؤال أو هدفه إعدادات الصفحة الحالية؛ راجعهما قبل الاعتماد.",
  dependency_not_included:
    "يعتمد هذا السؤال على مقطع غير مشمول في المصدر المراجَع؛ راجع تضمين مقاطعه قبل الاعتماد.",
  anchor_not_resolved:
    "تعذّر التحقق من أحد اقتباسات الدليل في النص المراجَع الحالي؛ راجع دليله قبل الاعتماد.",
  required_order_missing:
    "يتطلب هذا السؤال ترتيبًا لازمًا بين مقطعين لم يُثبَّت بعد؛ راجع الترتيب قبل الاعتماد.",
  criterion_invalid:
    "معايير التقييم أو سلم الإنشاء في هذا السؤال غير مكتملة أو غير صالحة؛ راجعها قبل الاعتماد.",
};
export const BLOCK_UNKNOWN_ARABIC =
  "يوجد سبب آخر يمنع اعتماد هذا السؤال حاليًا؛ شرحه غير متاح.";

function arabicIndic(number) {
  return String(number).replace(/\d/g, (digit) => "٠١٢٣٤٥٦٧٨٩"[digit]);
}

export function currentAuthor(question) {
  const pairs = Array.isArray(question.field_authors)
    ? question.field_authors : [];
  return pairs.some((pair) => Array.isArray(pair) && pair[1] === "teacher")
    ? "teacher" : "model";
}

let fieldSequence = 0;

function el(tag, options = {}) {
  const node = document.createElement(tag);
  if (options.className) node.className = options.className;
  if (options.testId) node.dataset.testid = options.testId;
  if (options.text !== undefined) node.textContent = options.text;
  for (const [name, value] of Object.entries(options.attrs || {})) {
    node.setAttribute(name, value);
  }
  return node;
}

function metaLine(label, testId) {
  const line = el("p", { className: "question-meta" });
  line.append(`${label}: `);
  const value = el("span", { testId });
  line.append(value);
  return { line, value };
}

const UNAVAILABLE = "غير متاح";

// Request/model identifiers are direction-isolated values.
function identifierLine(label, testId) {
  const line = el("p", { className: "question-meta" });
  line.append(`${label}: `);
  const value = el("bdi", { className: "technical-value", testId });
  line.append(value);
  return { line, value };
}

function requestIdTextOf(item) {
  return item && typeof item.request_id === "string" && item.request_id
    ? item.request_id : UNAVAILABLE;
}

function labeledField(labelText, control) {
  const id = `review-field-${++fieldSequence}`;
  control.id = id;
  const label = el("label", { text: labelText, attrs: { for: id } });
  const wrap = el("div");
  wrap.append(label, control);
  return wrap;
}

function setValueIfIdle(control, value) {
  if (document.activeElement !== control && control.value !== value) {
    control.value = value;
  }
}

function button(testId, text, onClick) {
  const node = el("button", { testId, text, attrs: { type: "button" } });
  node.addEventListener("click", onClick);
  return node;
}

export function createRegionItem(region, callbacks) {
  const node = el("article", {
    className: "region-item", testId: "region-item",
    attrs: {
      "data-region-id": region.region_id,
      "data-external-section-id": region.external_section_id,
    },
  });
  const heading = el("h3", {
    testId: "region-heading", attrs: { tabindex: "-1" } });
  // Display order only: this number orients the teacher on the
  // page and is never evidence precedence.
  const positionLine = metaLine("موضع العرض", "region-position");
  const inclusionState = el("p", {
    className: "region-meta", testId: "region-inclusion-state" });
  const textAuthorLine = metaLine("مؤلف النص", "region-text-author");
  const labelAuthorLine = metaLine(
    "مؤلف التصنيف", "region-label-author");
  const requestIdLine = identifierLine(
    "معرّف الطلب", "region-request-id");
  const modelLine = identifierLine("النموذج", "region-model-name");
  const flagsWrap = el("div");
  const flagsLabel = el("span", { text: "تنبيهات النموذج: " });
  const flags = el("ul", { className: "region-flags" });
  flagsWrap.append(flagsLabel, flags);
  const linkedWrap = el("div", { testId: "region-linked-questions" });
  const currentText = el("p", {
    className: "region-current-text", testId: "region-current-text" });

  const textInput = el("textarea", {
    testId: "region-current-text-input" });
  const labelInput = el("input", {
    testId: "region-current-label-input", attrs: { type: "text" } });
  const inclusionInput = el("select", {
    testId: "region-inclusion-input" });
  for (const [value, label, disabled] of [
      ["unreviewed", INCLUSION_ARABIC.unreviewed, true],
      ["included", INCLUSION_ARABIC.included, false],
      ["excluded", INCLUSION_ARABIC.excluded, false],
      ["unresolved", INCLUSION_ARABIC.unresolved, false]]) {
    const option = el("option", { text: label, attrs: { value } });
    option.disabled = disabled;
    inclusionInput.append(option);
  }
  const id = region.region_id;
  for (const [control, field] of [
      [textInput, "current_text"], [labelInput, "current_label"],
      [inclusionInput, "inclusion"]]) {
    const eventName = control === inclusionInput ? "change" : "input";
    control.addEventListener(eventName, () => {
      callbacks.onRegionDraft(id, field, control.value);
      refreshCommit();
    });
  }
  const commit = button(
    "region-review-commit", "حفظ مراجعة المقطع",
    () => callbacks.onRegionCommit(id));
  const cancel = button(
    "region-review-cancel", "إلغاء التعديل",
    () => callbacks.onRegionCancel(id));
  const moveUp = button(
    "region-move-up", "نقل المقطع لأعلى",
    () => callbacks.onRegionMove(id, "up"));
  moveUp.setAttribute("aria-label", "نقل المقطع لأعلى");
  const moveDown = button(
    "region-move-down", "نقل المقطع لأسفل",
    () => callbacks.onRegionMove(id, "down"));
  moveDown.setAttribute("aria-label", "نقل المقطع لأسفل");
  const evidence = button(
    "region-evidence-link", "إظهار الصفحة الكاملة",
    () => callbacks.onRegionEvidence(id));
  // Read-mode default: acknowledged text, flags and the explicit
  // inclusion choice/save stay visible. The optional text/label
  // editor is a view-only disclosure owned here; its values stay
  // the coordinator's keyed draft and the toggle sends nothing.
  const editor = el("div", { className: "region-editor" });
  editor.hidden = true;
  editor.append(
    labeledField("النص الحالي", textInput),
    labeledField("التصنيف الحالي", labelInput));
  const editToggle = button(
    "region-edit", "تحرير النص والتصنيف", () => {
      editor.hidden = !editor.hidden;
      editToggle.setAttribute(
        "aria-expanded", String(!editor.hidden));
      if (!editor.hidden) {
        textInput.focus();
      }
    });
  editToggle.setAttribute("aria-expanded", "false");
  const form = el("div", { className: "review-form" });
  form.append(
    editor, labeledField("قرار التضمين", inclusionInput));
  const actions = el("div", { className: "item-actions" });
  actions.append(editToggle, commit, cancel, moveUp, moveDown, evidence);
  form.append(actions);

  const original = el("details", { testId: "region-original" });
  const summary = el("summary", { text: "النص الأصلي كما ورد" });
  const rawText = el("p", { testId: "region-raw-text" });
  const rawLabelLine = el("p");
  const rawLabel = el("span", { testId: "region-raw-label" });
  rawLabelLine.append("التصنيف الأصلي: ", rawLabel);
  original.append(summary, rawText, rawLabelLine);
  node.append(heading, currentText, flagsWrap, inclusionState,
    positionLine.line, textAuthorLine.line, labelAuthorLine.line,
    requestIdLine.line, modelLine.line, linkedWrap, form, original);

  let linkedSignature = null;
  let linkedEntries = [];

  function refreshCommit() {
    commit.disabled = !LEGAL_INCLUSIONS.includes(inclusionInput.value)
      || Boolean(record.locked);
  }

  const record = {
    node,
    heading,
    moveUp,
    moveDown,
    locked: false,
    setModelName(text) {
      modelLine.value.textContent = text;
    },
    // View-only: the coordinator calls this after an acknowledged
    // Save or an explicit Cancel. Repaints never close the editor.
    closeEditor() {
      if (editor.contains(document.activeElement)) {
        editToggle.focus();
      }
      editor.hidden = true;
      editToggle.setAttribute("aria-expanded", "false");
    },
    update(next, draft, locked, linkedQuestions, provenance,
        linkedStatusVisible = true) {
      record.locked = locked;
      requestIdLine.value.textContent =
        provenance?.requestIdText ?? requestIdTextOf(next);
      modelLine.value.textContent = provenance?.modelName ?? UNAVAILABLE;
      heading.textContent = next.current_label;
      // Acknowledged text only; uncommitted typing lives in the
      // coordinator draft and the (possibly open) editor fields.
      currentText.textContent = next.current_text;
      positionLine.value.textContent =
        Number.isFinite(next.display_position)
          ? arabicIndic(next.display_position) : "";
      inclusionState.textContent =
        INCLUSION_ARABIC[next.inclusion] || next.inclusion;
      textAuthorLine.value.textContent =
        AUTHOR_ARABIC[next.text_author] || next.text_author;
      labelAuthorLine.value.textContent =
        AUTHOR_ARABIC[next.label_author] || next.label_author;
      flags.replaceChildren(...(next.model_flags || []).map(
        (flag) => el("li", { testId: "region-model-flag", text: flag })));
      flagsWrap.hidden = !(next.model_flags || []).length;
      setValueIfIdle(textInput, draft.current_text);
      setValueIfIdle(labelInput, draft.current_label);
      setValueIfIdle(inclusionInput, draft.inclusion);
      rawText.textContent = next.raw_text;
      rawLabel.textContent = next.raw_label;
      for (const control of [textInput, labelInput, inclusionInput,
          cancel, moveUp, moveDown, evidence]) {
        control.disabled = locked;
      }
      refreshCommit();
      const signature = JSON.stringify(
        linkedQuestions.map((q) => [q.question_id, q.status,
          q.current_question]));
      if (signature !== linkedSignature) {
        linkedSignature = signature;
        linkedEntries = [];
        linkedWrap.replaceChildren();
        if (linkedQuestions.length) {
          linkedWrap.append("الأسئلة المرتبطة: ");
          for (const question of linkedQuestions) {
            const link = el("button", {
              testId: "region-linked-question",
              attrs: { type: "button" } });
            link.dataset.questionId = question.question_id;
            const wording = el("span", {
              text: question.current_question });
            const statusNode = el("span", {
              testId: "region-linked-question-status" });
            link.append(wording, " ", statusNode);
            link.addEventListener("click", () => {
              callbacks.onShowQuestion(question.question_id);
            });
            linkedEntries.push({
              questionId: question.question_id, link, statusNode });
            linkedWrap.append(link, " ");
          }
        }
      }
      // The coordinator owns currentness: while it is withdrawn or
      // the outcome is uncertain the stale status carries no text,
      // so no approval claim remains in the link. Busy (locked)
      // alone never suppresses it.
      const linkedById = new Map(
        linkedQuestions.map((q) => [q.question_id, q]));
      for (const entry of linkedEntries) {
        entry.link.disabled = locked;
        const question = linkedById.get(entry.questionId);
        if (linkedStatusVisible && question) {
          entry.statusNode.textContent =
            STATUS_ARABIC[question.status] || question.status;
          entry.statusNode.hidden = false;
        } else {
          entry.statusNode.textContent = "";
          entry.statusNode.hidden = true;
        }
      }
    },
  };
  return record;
}

function factRow(quote, regionId, callbacks, testId) {
  const row = el("div", { className: "evidence-row" });
  const fact = el("span", { testId, text: quote });
  fact.dataset.regionId = regionId;
  const source = button(
    "question-evidence-source", "إظهار في المصدر",
    () => callbacks.onRegionEvidence(regionId));
  source.dataset.regionId = regionId;
  row.append(fact, " ", source);
  return row;
}

// One immutable order pair with a whole-page evidence button for
// each actual endpoint. No crop, highlight or precedence claim.
function orderRelationship(pair, callbacks, relationshipTestId,
    sourceTestId) {
  const beforeId = Array.isArray(pair) ? pair[0] : "";
  const afterId = Array.isArray(pair) ? pair[1] : "";
  const relationship = el("span", { testId: relationshipTestId });
  relationship.append(`${beforeId} قبل ${afterId} `);
  for (const regionId of [beforeId, afterId]) {
    const source = button(
      sourceTestId, "إظهار في المصدر",
      () => callbacks.onRegionEvidence(regionId));
    source.dataset.regionId = regionId;
    relationship.append(source, " ");
  }
  return relationship;
}

function criterionList(criteria, callbacks) {
  const list = el("ul");
  for (const criterion of criteria || []) {
    const item = el("li", { testId: "question-criterion" });
    const origin = criterion.origin === "source"
      ? "من المصدر" : "من الهدف";
    item.append(`${origin}: `);
    item.append(el("span", {
      testId: "question-criterion-text", text: criterion.text }));
    for (const fact of criterion.supporting_facts || []) {
      const pair = Array.isArray(fact)
        ? fact : [fact.region_id, fact.quote];
      item.append(factRow(pair[1], pair[0], callbacks,
        "question-criterion-fact"));
    }
    list.append(item);
  }
  return list;
}

const ORIGINAL_UNAVAILABLE_TEXT =
  "المواد الأصلية الكاملة غير متاح تحميلها في هذه الجلسة.";

function originalMaterialParts(prefix) {
  return {
    level: el("p", { testId: `${prefix}-level` }),
    evidence: el("div", { testId: `${prefix}-evidence` }),
    order: el("div", { testId: `${prefix}-order` }),
    assumptions: el("div", { testId: `${prefix}-assumptions` }),
    criteria: el("div", { testId: `${prefix}-criteria` }),
    rubric: el("div", { testId: `${prefix}-rubric` }),
  };
}

// Paints only the validated same-request raw original material
// the coordinator hands over. Pending paints nothing yet; a
// missing/failed/invalid raw item says so honestly and never
// falls back to mutable current fields. Empty original arrays
// fabricate no rows. Repaints are signature-gated so an open
// disclosure and its focus are never churned.
function makeOriginalMaterialPainter(parts, prefix, callbacks) {
  let signature = null;
  return function paintOriginalMaterial(view) {
    const state = view && view.state ? view.state : "pending";
    const item = state === "known" ? view.item : null;
    const nextSignature = JSON.stringify(item ? ["known",
      item.evidence, item.required_order_relationships,
      item.hypothetical_assumptions,
      item.evaluate_criteria, item.create_rubric] : [state]);
    if (nextSignature === signature) {
      return;
    }
    signature = nextSignature;
    const containers = [parts.evidence, parts.order,
      parts.assumptions, parts.criteria, parts.rubric];
    for (const container of containers) {
      container.replaceChildren();
    }
    if (state === "pending") {
      return;
    }
    if (!item) {
      for (const container of containers) {
        container.append(
          el("span", { text: ORIGINAL_UNAVAILABLE_TEXT }));
      }
      return;
    }
    for (const fact of item.evidence || []) {
      parts.evidence.append(factRow(
        fact.quote, fact.region_id, callbacks, `${prefix}-fact`));
    }
    // Original required order, from the same validated raw item,
    // under its own markers; the current card/preview order links
    // stay separate and an empty pair list creates no rows.
    if ((item.required_order_relationships || []).length) {
      parts.order.append("ترتيب لازم: ");
      for (const pair of item.required_order_relationships) {
        parts.order.append(orderRelationship(
          pair, callbacks, `${prefix}-order-relationship`,
          `${prefix}-order-source`), " ");
      }
    }
    for (const assumption of item.hypothetical_assumptions || []) {
      parts.assumptions.append(el("span", {
        testId: `${prefix}-assumption`, text: assumption }), " ");
    }
    if ((item.evaluate_criteria || []).length) {
      parts.criteria.append(
        criterionList(item.evaluate_criteria, callbacks));
    }
    if ((item.create_rubric || []).length) {
      parts.rubric.append(
        criterionList(item.create_rubric, callbacks));
    }
  };
}

export function createQuestionCard(question, callbacks) {
  const id = question.question_id;
  const node = el("article", {
    className: "question-card", testId: "question-card",
    attrs: {
      "data-question-id": id, "data-slot-id": question.slot_id } });
  const heading = el("h3", {
    testId: "question-heading", attrs: { tabindex: "-1" } });
  const statusLine = metaLine("الحالة", "question-status");
  const decision = el("p", {
    className: "question-meta", testId: "question-decision" });
  const author = el("p", {
    className: "question-meta", testId: "question-author" });
  const levelLine = metaLine("مستوى بلوم الحالي", "question-level");
  const gradeLine = metaLine("الصف الحالي", "question-grade");
  const objectiveLine = metaLine("الهدف الحالي", "question-objective");
  const generationGradeLine = metaLine(
    "الصف عند التوليد", "question-generation-grade");
  const generationObjectiveLine = metaLine(
    "الهدف عند التوليد", "question-generation-objective");
  const requestIdLine = identifierLine(
    "معرّف الطلب", "question-request-id");
  const modelLine = identifierLine("النموذج", "question-model-name");
  const guidanceLine = metaLine(
    "إرشادات الإجابة الحالية", "question-guidance");
  const cognitiveLine = metaLine(
    "التعليل المعرفي الحالي", "question-cognitive");
  const blocks = el("div", { className: "question-blocks" });
  const draftStatus = el("p", {
    className: "question-draft-status", testId: "question-draft-status",
    attrs: { role: "status" } });
  draftStatus.hidden = true;
  const evidenceWrap = el("div");
  evidenceWrap.append("الدليل: ");
  const orderWrap = el("div", { testId: "question-order" });
  const assumptionsWrap = el("div", {
    testId: "question-assumptions" });
  const criteriaWrap = el("div", { testId: "question-criteria" });
  const original = el("details", { testId: "question-original" });
  const originalText = el("p", { testId: "question-original-text" });
  const originalGuidance = el("p", {
    testId: "question-original-guidance" });
  const originalCognitive = el("p", {
    testId: "question-original-cognitive" });
  const originalParts = originalMaterialParts("question-original");
  const paintOriginalMaterial = makeOriginalMaterialPainter(
    originalParts, "question-original", callbacks);
  original.append(
    el("summary", { text: "السؤال الأصلي كما ورد" }),
    originalText, originalGuidance, originalCognitive,
    originalParts.level, originalParts.evidence,
    originalParts.order, originalParts.assumptions,
    originalParts.criteria, originalParts.rubric);

  const editButton = button(
    "question-edit", "تحرير السؤال",
    () => callbacks.onQuestionEditToggle(id));
  const approveButton = button(
    "question-approve", "اعتماد السؤال",
    () => callbacks.onQuestionApprove(id));
  const rejectButton = button(
    "question-reject", "رفض السؤال",
    () => callbacks.onQuestionReject(id));
  const actions = el("div", { className: "item-actions" });
  actions.append(editButton, approveButton, rejectButton);

  const editForm = el("div", {
    className: "question-edit-form", testId: "question-edit-form" });
  editForm.hidden = true;
  const fields = {
    question: el("textarea", { testId: "question-edit-question" }),
    guidance: el("textarea", { testId: "question-edit-guidance" }),
    cognitive: el("textarea", { testId: "question-edit-cognitive" }),
    grade: el("input", {
      testId: "question-edit-grade", attrs: { type: "text" } }),
    objective: el("input", {
      testId: "question-edit-objective", attrs: { type: "text" } }),
    level: el("select", { testId: "question-edit-level" }),
  };
  for (const [value, label] of Object.entries(BLOOM_ARABIC)) {
    fields.level.append(el("option", { text: label, attrs: { value } }));
  }
  for (const [field, key] of [
      [fields.question, "question"], [fields.guidance, "answer_guidance"],
      [fields.cognitive, "cognitive_explanation"],
      [fields.grade, "grade"], [fields.objective, "objective"],
      [fields.level, "bloom_level"]]) {
    field.addEventListener("input", () => {
      callbacks.onQuestionDraft(id, key, field.value);
    });
    field.addEventListener("change", () => {
      callbacks.onQuestionDraft(id, key, field.value);
    });
  }
  const editCommit = button(
    "question-edit-commit", "حفظ التعديل",
    () => callbacks.onQuestionEditCommit(id));
  const editCancel = button(
    "question-edit-cancel", "إلغاء التعديل",
    () => callbacks.onQuestionEditCancel(id));
  const editActions = el("div", { className: "item-actions" });
  editActions.append(editCommit, editCancel);
  editForm.append(
    labeledField("نص السؤال الحالي", fields.question),
    labeledField("إرشادات الإجابة الحالية", fields.guidance),
    labeledField("التعليل المعرفي الحالي", fields.cognitive),
    labeledField("الصف الحالي", fields.grade),
    labeledField("الهدف الحالي", fields.objective),
    labeledField("مستوى بلوم الحالي", fields.level),
    editActions);
  // Supplementary current facts live behind one disclosure; the
  // primary wording, Bloom level, guidance, author/decision and
  // blocks stay readable before the actions. The original
  // disclosure stays independently reachable after it.
  const details = el("details", { testId: "question-details" });
  details.append(
    el("summary", { text: "تفاصيل السؤال ودليله" }),
    gradeLine.line, objectiveLine.line,
    generationGradeLine.line, generationObjectiveLine.line,
    requestIdLine.line, modelLine.line, cognitiveLine.line,
    evidenceWrap, orderWrap, assumptionsWrap, criteriaWrap);
  node.append(heading, statusLine.line, decision, author,
    levelLine.line, guidanceLine.line, blocks, draftStatus,
    actions, details, original, editForm);

  let evidenceSignature = null;
  // One gate writer for Approve: the coordinator lock, actual
  // server blocks and the coordinator-computed dirty state. The
  // renderer never recomputes dirtiness or overwrites typed
  // field values; a clean open editor is not dirty by itself.
  let gateLocked = false;
  let gateBlocked = false;
  let gateDirty = false;
  function paintDraftStatus() {
    draftStatus.hidden = !gateDirty;
    draftStatus.textContent = gateDirty
      ? "تعديلات غير محفوظة في هذا السؤال — احفظ التعديل أو ألغِه قبل الاعتماد."
      : "";
  }
  function refreshApproveGate() {
    approveButton.disabled = gateLocked || gateBlocked || gateDirty;
  }
  const record = {
    node,
    heading,
    fields,
    editForm,
    openEditor(next) {
      fields.question.value = next.current_question;
      fields.guidance.value = next.current_answer_guidance;
      fields.cognitive.value = next.current_cognitive_explanation;
      fields.grade.value = next.current_grade;
      fields.objective.value = next.current_objective;
      fields.level.value = next.current_level;
      editForm.hidden = false;
      fields.question.focus();
    },
    closeEditor() {
      editForm.hidden = true;
    },
    setDirtyState(dirty) {
      gateDirty = Boolean(dirty);
      paintDraftStatus();
      refreshApproveGate();
    },
    setModelName(text) {
      modelLine.value.textContent = text;
    },
    setOriginalMaterial(view) {
      paintOriginalMaterial(view);
    },
    update(next, locked, provenance, dirty = false) {
      heading.textContent = next.current_question;
      statusLine.value.textContent =
        STATUS_ARABIC[next.status] || next.status;
      decision.textContent =
        DECISION_ARABIC[next.status] || next.status;
      author.textContent =
        CURRENT_AUTHOR_ARABIC[currentAuthor(next)];
      levelLine.value.textContent =
        BLOOM_ARABIC[next.current_level] || next.current_level;
      gradeLine.value.textContent = next.current_grade;
      objectiveLine.value.textContent = next.current_objective;
      generationGradeLine.value.textContent =
        next.generation_grade ?? "";
      generationObjectiveLine.value.textContent =
        next.generation_objective ?? "";
      requestIdLine.value.textContent =
        provenance?.requestIdText ?? requestIdTextOf(next);
      modelLine.value.textContent = provenance?.modelName ?? UNAVAILABLE;
      guidanceLine.value.textContent = next.current_answer_guidance;
      cognitiveLine.value.textContent =
        next.current_cognitive_explanation;
      originalText.textContent = next.original_question;
      originalGuidance.textContent = next.original_answer_guidance;
      originalCognitive.textContent =
        next.original_cognitive_explanation;
      originalParts.level.textContent =
        BLOOM_ARABIC[next.model_level] || next.model_level || "";
      paintOriginalMaterial(provenance?.original || null);
      blocks.replaceChildren(...(next.blocks || []).map((code) => {
        const chip = el("span", {
          className: "question-block", testId: "question-block",
          text: BLOCK_ARABIC[code] || BLOCK_UNKNOWN_ARABIC });
        chip.dataset.block = code;
        return chip;
      }));
      const signature = JSON.stringify([
        next.anchors, next.required_order_relationships,
        next.hypothetical_assumptions, next.evaluate_criteria,
        next.create_rubric]);
      if (signature !== evidenceSignature) {
        evidenceSignature = signature;
        evidenceWrap.replaceChildren("الدليل: ");
        for (const anchor of next.anchors || []) {
          evidenceWrap.append(factRow(
            anchor[1], anchor[0], callbacks,
            "question-evidence-fact"));
        }
        orderWrap.replaceChildren();
        if ((next.required_order_relationships || []).length) {
          orderWrap.append("ترتيب لازم: ");
          for (const pair of next.required_order_relationships) {
            orderWrap.append(orderRelationship(
              pair, callbacks, "question-order-relationship",
              "question-order-source"), " ");
          }
        }
        assumptionsWrap.replaceChildren();
        if ((next.hypothetical_assumptions || []).length) {
          assumptionsWrap.append("افتراضات: ");
          for (const item of next.hypothetical_assumptions) {
            assumptionsWrap.append(el("span", {
              testId: "question-assumption", text: item }), " ");
          }
        }
        criteriaWrap.replaceChildren();
        if ((next.evaluate_criteria || []).length) {
          criteriaWrap.append("معايير التقييم: ",
            criterionList(next.evaluate_criteria, callbacks));
        }
        if ((next.create_rubric || []).length) {
          criteriaWrap.append("سلم الإنشاء: ",
            criterionList(next.create_rubric, callbacks));
        }
      }
      // Decisions stay visible in every decided state. Approve
      // goes through the one gate writer combining the lock,
      // actual backend blocks and the coordinator's dirty state;
      // read-only currency re-enables in review.js never touch it.
      approveButton.hidden = false;
      rejectButton.hidden = false;
      for (const control of [editButton, approveButton, rejectButton,
          editCommit, editCancel, ...Object.values(fields)]) {
        control.disabled = locked;
      }
      gateLocked = Boolean(locked);
      gateBlocked = (next.blocks || []).length > 0;
      gateDirty = Boolean(dirty);
      paintDraftStatus();
      refreshApproveGate();
    },
  };
  return record;
}

export function createPreviewItem(entry, callbacks) {
  const node = el("article", {
    className: "preview-item", testId: "preview-item",
    attrs: { "data-question-id": entry.question_id } });
  const heading = el("h3", { testId: "preview-question" });
  const author = el("p", {
    className: "preview-meta", testId: "preview-author" });
  const decision = el("p", {
    className: "preview-meta", testId: "preview-decision" });
  const levelLine = metaLine("مستوى بلوم", "preview-level");
  const requestIdLine = identifierLine(
    "معرّف الطلب", "preview-request-id");
  const modelLine = identifierLine("النموذج", "preview-model-name");
  const checking = el("details", { testId: "preview-checking" });
  const guidance = el("p", { testId: "preview-guidance" });
  const factsWrap = el("div");
  const extrasWrap = el("div");
  checking.append(
    el("summary", { text: "مواد التحقق" }), guidance, factsWrap,
    extrasWrap);
  const original = el("details", { testId: "preview-original" });
  const originalText = el("p", { testId: "preview-original-text" });
  const originalGuidance = el("p", {
    testId: "preview-original-guidance" });
  const originalCognitive = el("p", {
    testId: "preview-original-cognitive" });
  const originalParts = originalMaterialParts("preview-original");
  const paintOriginalMaterial = makeOriginalMaterialPainter(
    originalParts, "preview-original", callbacks);
  original.append(
    el("summary", { text: "السؤال الأصلي كما ورد" }),
    originalText, originalGuidance, originalCognitive,
    originalParts.level, originalParts.evidence,
    originalParts.order, originalParts.assumptions,
    originalParts.criteria, originalParts.rubric);
  node.append(heading, author, decision, levelLine.line,
    requestIdLine.line, modelLine.line, checking, original);
  let factsSignature = null;
  return {
    node,
    setModelName(text) {
      modelLine.value.textContent = text;
    },
    setOriginalMaterial(view) {
      paintOriginalMaterial(view);
    },
    update(nextEntry, question, provenance) {
      heading.textContent = nextEntry.question;
      author.textContent =
        CURRENT_AUTHOR_ARABIC[nextEntry.author] || nextEntry.author;
      decision.textContent = "قرار المعلم: معتمد";
      levelLine.value.textContent =
        BLOOM_ARABIC[nextEntry.bloom_level] || nextEntry.bloom_level;
      requestIdLine.value.textContent =
        provenance?.requestIdText ?? requestIdTextOf(question);
      modelLine.value.textContent = provenance?.modelName ?? UNAVAILABLE;
      guidance.textContent = nextEntry.answer_guidance;
      originalText.textContent = question
        ? question.original_question : nextEntry.original_question;
      originalGuidance.textContent =
        nextEntry.original_answer_guidance;
      originalCognitive.textContent = question
        ? question.original_cognitive_explanation : "";
      originalParts.level.textContent = question
        ? BLOOM_ARABIC[question.model_level]
          || question.model_level || "" : "";
      paintOriginalMaterial(provenance?.original || null);
      const signature = JSON.stringify([
        nextEntry.resolved_anchors,
        question && question.hypothetical_assumptions,
        question && question.evaluate_criteria,
        question && question.create_rubric,
        question && question.required_order_relationships]);
      if (signature === factsSignature) {
        return;
      }
      factsSignature = signature;
      factsWrap.replaceChildren();
      for (const anchor of nextEntry.resolved_anchors || []) {
        const fact = el("div", {
          className: "evidence-row",
          testId: "preview-evidence-fact" });
        fact.dataset.regionId = anchor[0];
        fact.append(
          el("span", {
            testId: "preview-evidence-quote", text: anchor[1] }),
          " — ",
          el("span", {
            testId: "preview-evidence-text", text: anchor[2] }),
          " ");
        const source = button(
          "preview-evidence-source", "إظهار في المصدر",
          () => callbacks.onRegionEvidence(anchor[0]));
        source.dataset.regionId = anchor[0];
        fact.append(source);
        factsWrap.append(fact);
      }
      extrasWrap.replaceChildren();
      if (question) {
        if ((question.hypothetical_assumptions || []).length) {
          extrasWrap.append("افتراضات: ");
          for (const item of question.hypothetical_assumptions) {
            extrasWrap.append(el("span", {
              testId: "preview-assumption", text: item }), " ");
          }
        }
        if ((question.evaluate_criteria || []).length) {
          extrasWrap.append("معايير التقييم: ",
            criterionList(question.evaluate_criteria, callbacks));
        }
        if ((question.create_rubric || []).length) {
          extrasWrap.append("سلم الإنشاء: ",
            criterionList(question.create_rubric, callbacks));
        }
        if ((question.required_order_relationships || []).length) {
          extrasWrap.append("ترتيب لازم: ");
          for (const pair of question.required_order_relationships) {
            extrasWrap.append(orderRelationship(
              pair, callbacks, "preview-order-relationship",
              "preview-order-source"), " ");
          }
        }
      }
    },
  };
}

export function orderChildren(list, nodes) {
  nodes.forEach((node, index) => {
    if (list.children[index] !== node) {
      list.insertBefore(node, list.children[index] || null);
    }
  });
  while (list.children.length > nodes.length) {
    list.lastElementChild.remove();
  }
}
