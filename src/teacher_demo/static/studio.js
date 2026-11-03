// Lesson-studio presentation only: setup phase, stage reflection
// and focused question selection over the existing live nodes.
// Canonical application values are read through the callback
// supplied at initialization or through render arguments, on
// every paint. This module keeps no snapshot, currentness,
// dirty, approval or confirmation state of its own.

let initialized = false;
let readSetupState = null;
let studioRoot = null;
let setupHeading = null;
let setupContinueButton = null;
let questionNavTrack = null;
let questionProgress = null;
let questionPrevButton = null;
let questionNextButton = null;
// The one presentation selection, plus the local setup view
// override ({ phase, candidate }) scoped to the exact candidate
// it was chosen under. Question order/records are references to
// the coordinator's latest render arguments, never copies of
// question content.
let selectedQuestionId = null;
let setupViewOverride = null;
let questionOrder = [];
let questionRecordsRef = null;
let showQuestionHandler = null;
const questionNavButtons = new Map();

function arabicIndic(number) {
  return String(number).replace(/\d/g, (digit) => "٠١٢٣٤٥٦٧٨٩"[digit]);
}

function refreshRoot() {
  if (!studioRoot || !studioRoot.isConnected) {
    studioRoot = document.querySelector(".studio-app");
  }
  return studioRoot;
}

function refreshQuestionRefs() {
  if (!questionNavTrack || !questionNavTrack.isConnected) {
    questionNavTrack = document.querySelector(
      "[data-studio='question-nav']");
  }
  if (!questionProgress || !questionProgress.isConnected) {
    questionProgress = document.querySelector(
      "[data-studio='question-progress']");
  }
  if (!questionPrevButton || !questionPrevButton.isConnected) {
    questionPrevButton = document.querySelector(
      "[data-studio='question-prev']");
  }
  if (!questionNextButton || !questionNextButton.isConnected) {
    questionNextButton = document.querySelector(
      "[data-studio='question-next']");
  }
}

export function isStudioElementVisible(element) {
  if (!element || !element.isConnected) {
    return false;
  }
  for (let node = element; node; node = node.parentElement) {
    if (node.hidden) {
      return false;
    }
    const style = typeof window.getComputedStyle === "function"
      ? window.getComputedStyle(node) : null;
    if (style && (style.display === "none"
        || style.visibility === "hidden"
        || style.visibility === "collapse")) {
      return false;
    }
    if (node === document.body) {
      break;
    }
  }
  return true;
}

function readState() {
  if (typeof readSetupState !== "function") {
    return null;
  }
  const state = readSetupState();
  return state && typeof state === "object" ? state : null;
}

function candidateOf(state) {
  return state && state.candidate ? state.candidate : null;
}

function isConfirmPhase(state) {
  const candidate = candidateOf(state);
  const candidateState = typeof state.candidateState === "string"
    ? state.candidateState : candidate?.state;
  return Boolean(candidate && state.candidateCurrent
    && state.hasCommittedSource && !state.initialAnalysisAttempted
    && (candidateState === "loading" || candidateState === "ready")
    && !state.outcomeUncertain && !state.sessionExpired
    && !state.setupErrorVisible);
}

function paintSetupView(state = readState()) {
  const root = refreshRoot();
  if (!root || !state) {
    return;
  }
  const activeElement = document.activeElement;
  const focusedSetupElement = activeElement
    && activeElement !== document.body
    && typeof activeElement.closest === "function"
    && activeElement.closest("[data-workflow-step='setup']")
    && isStudioElementVisible(activeElement)
    ? activeElement : null;
  if (setupViewOverride
      && setupViewOverride.candidate !== candidateOf(state)) {
    setupViewOverride = null;
  }
  const phase = setupViewOverride ? setupViewOverride.phase
    : isConfirmPhase(state) ? "confirm"
      : state.hasFile ? "configure" : "choose";
  if (root.dataset.setupPhase !== phase) {
    root.dataset.setupPhase = phase;
  }
  if (typeof state.stage === "string" && state.stage
      && root.dataset.studioStage !== state.stage) {
    root.dataset.studioStage = state.stage;
  }
  root.dataset.hasSource = String(Boolean(state.hasCommittedSource));
  if (setupContinueButton) {
    const showContinue = phase === "choose" && Boolean(state.hasFile);
    if (setupContinueButton.hidden === showContinue) {
      setupContinueButton.hidden = !showContinue;
    }
  }
  if (focusedSetupElement
      && !isStudioElementVisible(focusedSetupElement)
      && setupHeading && isStudioElementVisible(setupHeading)) {
    setupHeading.focus();
  }
}

function focusSetupHeadingIfSourceHidden(sourceButton) {
  if (!sourceButton || document.activeElement !== sourceButton
      || isStudioElementVisible(sourceButton)) {
    return;
  }
  if (setupHeading && isStudioElementVisible(setupHeading)) {
    setupHeading.focus();
  }
}

function chooseSetupPhase(phase, sourceButton) {
  const state = readState();
  const isSettingsEntry = sourceButton?.dataset?.studio
    === "setup-settings";
  if (phase === "configure" && state && !state.hasFile
      && !candidateOf(state) && !isSettingsEntry) {
    return;
  }
  setupViewOverride = { phase, candidate: candidateOf(state) };
  paintSetupView(state);
  focusSetupHeadingIfSourceHidden(sourceButton);
}

export function reflectStudioStage(stage) {
  if (!initialized) {
    return;
  }
  const root = refreshRoot();
  if (!root) {
    return;
  }
  if (typeof stage === "string" && stage
      && root.dataset.studioStage !== stage) {
    root.dataset.studioStage = stage;
  }
  paintSetupView();
}

export function syncStudioSetupView() {
  if (!initialized) {
    return;
  }
  paintSetupView();
}

export function initStudioView(readSetupStateCallback) {
  if (typeof readSetupStateCallback === "function") {
    readSetupState = readSetupStateCallback;
  }
  if (initialized) {
    paintSetupView();
    return;
  }
  initialized = true;
  refreshRoot();
  refreshQuestionRefs();
  setupHeading = document.getElementById("setup-heading");
  setupContinueButton = document.querySelector(
    "[data-studio='setup-continue']");
  const fileInput = document.getElementById("page-file");
  const setupBackButton = document.querySelector(
    "[data-studio='setup-back']");
  const setupEditButton = document.querySelector(
    "[data-studio='setup-edit']");
  const setupSettingsButton = document.querySelector(
    "[data-studio='setup-settings']");
  // View-only listeners on the new studio controls, plus one
  // change listener that only re-derives the setup phase from
  // the retained file selection. Existing domain controls keep
  // their original handlers untouched.
  fileInput?.addEventListener("change", () => {
    setupViewOverride = null;
    paintSetupView();
  });
  setupBackButton?.addEventListener("click", () => {
    chooseSetupPhase("choose", setupBackButton);
  });
  setupContinueButton?.addEventListener("click", () => {
    chooseSetupPhase("configure", setupContinueButton);
  });
  setupEditButton?.addEventListener("click", () => {
    chooseSetupPhase("configure", setupEditButton);
  });
  setupSettingsButton?.addEventListener("click", () => {
    chooseSetupPhase("configure", setupSettingsButton);
  });
  questionPrevButton?.addEventListener("click", () => {
    showAdjacentQuestion(-1);
  });
  questionNextButton?.addEventListener("click", () => {
    showAdjacentQuestion(1);
  });
  paintSetupView();
}

function paintQuestionCards() {
  if (!questionRecordsRef) {
    return;
  }
  for (const questionId of questionOrder) {
    const node = questionRecordsRef.get(questionId)?.node;
    if (node) {
      node.hidden = questionId !== selectedQuestionId;
    }
  }
}

function paintQuestionProgress() {
  const total = questionOrder.length;
  const position = selectedQuestionId
    ? questionOrder.indexOf(selectedQuestionId) : -1;
  if (questionProgress) {
    questionProgress.textContent = total > 0 && position >= 0
      ? `السؤال ${arabicIndic(position + 1)} من ${arabicIndic(total)}`
      : "";
  }
  if (questionPrevButton) {
    questionPrevButton.disabled = position <= 0;
  }
  if (questionNextButton) {
    questionNextButton.disabled = position < 0
      || position >= total - 1;
  }
}

function showAdjacentQuestion(offset) {
  if (typeof showQuestionHandler !== "function") {
    return;
  }
  const position = questionOrder.indexOf(selectedQuestionId);
  const target = questionOrder[position + offset];
  if (target) {
    showQuestionHandler(target);
  }
}

function paintQuestionNav(questions) {
  const seen = new Set();
  let index = -1;
  questions.forEach((question) => {
    const questionId = question?.question_id;
    if (typeof questionId !== "string" || !questionId
        || seen.has(questionId)) {
      return;
    }
    index += 1;
    seen.add(questionId);
    let button = questionNavButtons.get(questionId);
    if (!button) {
      button = document.createElement("button");
      button.type = "button";
      button.dataset.questionId = questionId;
      button.addEventListener("click", () => {
        if (typeof showQuestionHandler === "function") {
          showQuestionHandler(questionId);
        }
      });
      questionNavButtons.set(questionId, button);
    }
    const number = arabicIndic(index + 1);
    if (button.textContent !== number) {
      button.textContent = number;
    }
    const wording = typeof question.current_question === "string"
      ? question.current_question : "";
    const label = wording
      ? `السؤال ${number}: ${wording}` : `السؤال ${number}`;
    if (button.getAttribute("aria-label") !== label) {
      button.setAttribute("aria-label", label);
    }
    if (wording && button.title !== wording) {
      button.title = wording;
    } else if (!wording && button.hasAttribute("title")) {
      button.removeAttribute("title");
    }
    if (questionId === selectedQuestionId) {
      if (button.getAttribute("aria-current") !== "true") {
        button.setAttribute("aria-current", "true");
      }
    } else if (button.hasAttribute("aria-current")) {
      button.removeAttribute("aria-current");
    }
    if (questionNavTrack.children[index] !== button) {
      questionNavTrack.insertBefore(
        button, questionNavTrack.children[index] || null);
    }
  });
  for (const [questionId, button] of [...questionNavButtons]) {
    if (!seen.has(questionId)) {
      button.remove();
      questionNavButtons.delete(questionId);
    }
  }
}

export function renderStudioQuestions(
    questions, questionRecords, onShowQuestion) {
  const list = Array.isArray(questions) ? questions : [];
  if (questionRecords instanceof Map) {
    questionRecordsRef = questionRecords;
  }
  if (typeof onShowQuestion === "function") {
    showQuestionHandler = onShowQuestion;
  }
  questionOrder = list
    .map((question) => question?.question_id)
    .filter((questionId) => typeof questionId === "string"
      && questionId);
  if (!selectedQuestionId
      || !questionOrder.includes(selectedQuestionId)) {
    selectedQuestionId = questionOrder[0] || null;
  }
  refreshQuestionRefs();
  // Without the studio shell hooks the existing all-cards
  // baseline stays exactly as the renderer left it.
  if (!refreshRoot() || !questionNavTrack) {
    return;
  }
  paintQuestionCards();
  paintQuestionNav(list);
  paintQuestionProgress();
}

export function selectStudioQuestion(questionId) {
  if (typeof questionId !== "string" || !questionId) {
    return;
  }
  selectedQuestionId = questionId;
  refreshQuestionRefs();
  if (!refreshRoot() || !questionNavTrack
      || (questionOrder.length > 0
        && !questionOrder.includes(questionId))) {
    return;
  }
  paintQuestionCards();
  for (const [navId, button] of questionNavButtons) {
    if (navId === selectedQuestionId) {
      if (button.getAttribute("aria-current") !== "true") {
        button.setAttribute("aria-current", "true");
      }
    } else if (button.hasAttribute("aria-current")) {
      button.removeAttribute("aria-current");
    }
  }
  paintQuestionProgress();
}

export function resetStudioQuestionSelection() {
  selectedQuestionId = null;
}


