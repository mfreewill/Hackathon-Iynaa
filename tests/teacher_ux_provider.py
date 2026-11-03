"""Server-only synthetic provider for teacher-UX browser tests.

Explicit test-only injection through the same server-side provider
seam as ``ControlsProvider`` from
``tests/test_http_teacher_controls.py`` and
``ReviewMetadataProvider`` from ``tests/review_metadata_provider``.
It is explicitly not Meta, makes no network calls, uses no
credentials, is never a product default or endpoint, and claims no
cognitive quality for its structured fields.

Modes (selected once by the test harness at construction):

- ``metadata`` (default): the unchanged ``ReviewMetadataProvider``
  outputs, including the Analyze draft whose missing required-order
  pair leaves the real ``required_order_missing`` approval block.
- ``structured``: the unchanged ``ControlsProvider`` question base
  with exactly three abstentions replaced by complete v2 drafts --
  one Apply draft with an explicit synthetic hypothetical
  assumption and one anchored source fact, one Evaluate draft with
  one nonempty source-origin criterion, and one Create draft with
  two objective-origin rubric entries carrying no source facts.
  Remember/Understand stay byte-for-byte the base outputs and
  Analyze stays abstained. No structured draft declares a required
  order. The final modified envelope replaces the base envelope in
  the inherited per-request ledger under the inherited lock.
- ``terminal``: source analysis keeps the inherited success; each
  question generation returns the caller-configured genuine
  terminal envelope (``raw`` is ``None``, exact permitted non-ok
  outcome, clearly synthetic model/response identity) through the
  provider factory. No exception is raised and no application
  status mapping is changed here.

The superclass request recording, ledger locks/dictionaries and
canonical reviewed-text/grade/objective/prompt success checks are
preserved in every mode.
"""
import copy

import review_metadata_provider as metadata_fixture
import test_http_teacher_controls as controls

STRUCTURED_MODEL_NAME = (
    "synthetic-teacher-ux-structured-model (not Meta)")
TERMINAL_MODEL_TEMPLATE = (
    "synthetic-teacher-ux-terminal-{outcome}-model (not Meta)")

PERMITTED_TERMINAL_OUTCOMES = (
    "incomplete", "invalid_schema", "empty", "error", "refusal",
    "timeout", "rate_limited")

APPLY_QUESTION = (
    "  إِذَا تَابَعْتَ جَرَيَانَ الْمَاءِ فِي النَّهْرِ، "
    "فَكَيْفَ تَصِفُ سُرْعَتَهُ لِزَمِيلِكَ؟  ")
APPLY_GUIDANCE = (
    "يَجْرِي الْمَاءُ بِسُرْعَةٍ فِي النَّهْرِ كَمَا فِي النَّصِّ "
    "الْمُرَاجَعِ.")
APPLY_COGNITIVE = "Synthetic apply task for the stated grade."
APPLY_ASSUMPTION = (
    "افْتِرَاضٌ افْتِرَاضِيٌّ تَجْرِيبِيٌّ صَرِيحٌ: يَقِفُ "
    "الطَّالِبُ صَبَاحًا عَلَى ضِفَّةِ النَّهْرِ وَيُتَابِعُ "
    "الْمَاءَ بِنَفْسِهِ.")

EVALUATE_QUESTION = (
    "  هَلْ تُوَافِقُ عَلَى أَنَّ الرَّبِيعَ أَنْسَبُ وَقْتٍ "
    "لِمُشَاهَدَةِ اخْضِرَارِ الشَّجَرِ عَلَى الضِّفَّةِ؟ "
    "عَلِّلْ حُكْمَكَ.  ")
EVALUATE_GUIDANCE = (
    "يُحْكَمُ بِالرُّجُوعِ إِلَى اخْضِرَارِ الشَّجَرِ فِي "
    "الرَّبِيعِ كَمَا فِي النَّصِّ الْمُرَاجَعِ.")
EVALUATE_COGNITIVE = "Synthetic evaluate task for the stated grade."
EVALUATE_CRITERION = (
    "يَسْتَنِدُ الْحُكْمُ إِلَى اخْضِرَارِ الشَّجَرِ فِي "
    "الرَّبِيعِ كَمَا وَرَدَ فِي الْمَصْدَرِ.")

CREATE_QUESTION = (
    "  اِكْتُبْ جُمْلَتَيْنِ مِنْ إِنْشَائِكَ تَصِفَانِ "
    "مَشْهَدَ النَّهْرِ وَالشَّجَرِ فِي الرَّبِيعِ.  ")
CREATE_GUIDANCE = (
    "لَا إِجَابَةَ وَاحِدَةً صَحِيحَةً؛ تُرَاجَعُ الْجُمْلَتَانِ "
    "بِسُلَّمِ التَّقْيِيمِ الْمُرْفَقِ.")
CREATE_COGNITIVE = "Synthetic create task for the stated grade."
CREATE_RUBRIC_A = (
    "تَرْتَبِطُ الْجُمْلَتَانِ بِهَدَفِ قِرَاءَةِ النَّصِّ "
    "وَوَصْفِهِ.")
CREATE_RUBRIC_B = (
    "تُسْتَعْمَلُ مُفْرَدَاتٌ وَاضِحَةٌ تُنَاسِبُ الصَّفَّ "
    "الْمُحَدَّدَ فِي الْهَدَفِ.")


class TeacherUXProvider(metadata_fixture.ReviewMetadataProvider):
    """Metadata default plus structured/terminal teacher-UX modes."""

    def __init__(self, mode="metadata", outcome=None, *, grade=controls.GRADE):
        if mode not in ("metadata", "structured", "terminal"):
            raise ValueError("unknown teacher-UX provider mode")
        if mode == "terminal":
            if outcome not in PERMITTED_TERMINAL_OUTCOMES:
                raise ValueError(
                    "terminal mode needs one permitted outcome")
        elif outcome is not None:
            raise ValueError(
                "outcome is only configurable in terminal mode")
        super().__init__(grade=grade)
        self._ux_mode = mode
        self._ux_outcome = outcome
        self._ux_question_count = 0

    def _next_question_count(self):
        with self._lock:
            self._ux_question_count += 1
            return self._ux_question_count

    def _questions(self, request):
        if self._ux_mode == "metadata":
            return super()._questions(request)
        if self._ux_mode == "structured":
            return self._structured_questions(request)
        return self._terminal_questions(request)

    def _structured_questions(self, request):
        # The ControlsProvider base enforces the canonical
        # reviewed-text, grade/objective and prompt conditions and
        # builds Remember/Understand plus abstentions elsewhere.
        result = controls.ControlsProvider._questions(self, request)
        reviewed = dict(request["reviewed_regions"])
        slots = [dict(slot) for slot in request["requested_slots"]]
        river_id = next(
            region_id for region_id, text in reviewed.items()
            if text == controls.EDITED_A)
        trees_id = next(
            region_id for region_id, text in reviewed.items()
            if text == controls.EDITED_B)
        by_level = {slot["level"]: slot for slot in slots}

        replacements = {}
        if "apply" in by_level:
            apply_item = controls.draft_item(
                by_level["apply"], APPLY_QUESTION, APPLY_GUIDANCE,
                APPLY_COGNITIVE, river_id, controls.FACT_A)
            apply_item["hypothetical_assumptions"] = [APPLY_ASSUMPTION]
            replacements["apply"] = apply_item
        if "evaluate" in by_level:
            evaluate_item = controls.draft_item(
                by_level["evaluate"], EVALUATE_QUESTION,
                EVALUATE_GUIDANCE, EVALUATE_COGNITIVE, trees_id,
                controls.FACT_B)
            evaluate_item["evaluate_criteria"] = [{
                "text": EVALUATE_CRITERION, "origin": "source",
                "supporting_facts": [{
                    "region_id": trees_id,
                    "quote": controls.FACT_B}]}]
            replacements["evaluate"] = evaluate_item
        if "create" in by_level:
            create_item = controls.draft_item(
                by_level["create"], CREATE_QUESTION, CREATE_GUIDANCE,
                CREATE_COGNITIVE, river_id, controls.FACT_A)
            create_item["create_rubric"] = [
                {"text": CREATE_RUBRIC_A, "origin": "objective",
                 "supporting_facts": []},
                {"text": CREATE_RUBRIC_B, "origin": "objective",
                 "supporting_facts": []}]
            replacements["create"] = create_item

        result["raw"]["items"] = [
            replacements.get(item["level"], item)
            for item in result["raw"]["items"]]
        count = self._next_question_count()
        result["model_name"] = STRUCTURED_MODEL_NAME
        result["response_id"] = (
            f"synthetic-teacher-ux-structured-response-{count}")
        # Store the final returned envelope, not the base version
        # the superclass ledger write just recorded.
        with self._lock:
            self.question_results[request["request_id"]] = (
                copy.deepcopy(result))
        return copy.deepcopy(result)

    def _terminal_questions(self, request):
        count = self._next_question_count()
        result = {
            "raw": None,
            "model_name": TERMINAL_MODEL_TEMPLATE.format(
                outcome=self._ux_outcome),
            "response_id": (
                f"synthetic-teacher-ux-terminal-{self._ux_outcome}"
                f"-response-{count}"),
            "outcome": self._ux_outcome,
        }
        with self._lock:
            self.question_results[request["request_id"]] = (
                copy.deepcopy(result))
        return copy.deepcopy(result)
