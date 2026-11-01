"""Server-only synthetic provider for review-metadata browser tests.

This fixture is test code injected by the test harness through the same
server-side provider seam as ``ControlsProvider`` from
``tests/test_http_teacher_controls.py``. It is explicitly not Meta
and is never a product default. It makes no network calls, uses no
credentials or sleeps, performs no browser injection, mutates no
domain/app private state and installs no semantic precedence: the
required-order pair below is ordinary provider proposal data, so
the Analyze card remains blocked by the real domain until a
teacher-facing writer exists.

Relative to the ``ControlsProvider`` outputs it changes
only what the metadata tracer needs:

- the source result carries a distinct synthetic source model name;
- every question-generation request carries a different exact
  synthetic model name (numbered per request on this instance), so
  per-request attribution can be checked across two generations;
- the Analyze slot returns one valid draft (complete v2 keys, one
  real exact evidence fact) whose ``required_order_relationships``
  carries the two current region IDs as one required-order pair
  ``[<river>, <trees>]``;
- every other slot outcome is byte-for-byte the outcome the
  original ``ControlsProvider`` produces (the two ordinary drafts
  and the remaining abstentions), under the same canonical
  ``EDITED_A``/``EDITED_B`` text conditions enforced by the
  superclass.

This fixture is used through the browser tests.
"""
import copy

import test_http_teacher_controls as controls

SOURCE_MODEL_NAME = "synthetic-metadata-source-model (not Meta)"
QUESTION_MODEL_TEMPLATE = "synthetic-metadata-question-model-{n} (not Meta)"

ANALYZE_QUESTION = (
    "  قَارِنْ بَيْنَ جَرَيَانِ الْمَاءِ فِي النَّهْرِ "
    "وَاخْضِرَارِ الشَّجَرِ فِي الرَّبِيعِ.  ")
ANALYZE_GUIDANCE = (
    "اَلْمَاءُ يَجْرِي بِسُرْعَةٍ فِي النَّهْرِ، "
    "وَالشَّجَرُ يَخْضَرُّ فِي الرَّبِيعِ عَلَى الضِّفَّةِ.")
ANALYZE_COGNITIVE = "Synthetic analysis task for the stated grade."


class ReviewMetadataProvider(controls.ControlsProvider):
    """ControlsProvider outputs plus per-request model attribution."""

    def __init__(self):
        super().__init__()
        self._question_model_count = 0

    def _source(self, request):
        result = super()._source(request)
        result["model_name"] = SOURCE_MODEL_NAME
        result["response_id"] = "synthetic-metadata-source-response-1"
        with self._lock:
            self.source_results[request["request_id"]] = (
                copy.deepcopy(result))
        return copy.deepcopy(result)

    def _questions(self, request):
        # The superclass enforces the canonical reviewed-text,
        # grade/objective and prompt conditions and builds the two
        # ordinary drafts plus abstentions for the other slots.
        result = super()._questions(request)
        reviewed = dict(request["reviewed_regions"])
        slots = [dict(slot) for slot in request["requested_slots"]]
        river_id = next(
            region_id for region_id, text in reviewed.items()
            if text == controls.EDITED_A)
        trees_id = next(
            region_id for region_id, text in reviewed.items()
            if text == controls.EDITED_B)
        analyze_slots = [
            slot for slot in slots if slot["level"] == "analyze"]
        if analyze_slots:
            analyze_item = controls.draft_item(
                analyze_slots[0], ANALYZE_QUESTION, ANALYZE_GUIDANCE,
                ANALYZE_COGNITIVE, river_id, controls.FACT_A)
            analyze_item["required_order_relationships"] = [[
                river_id, trees_id]]
            result["raw"]["items"] = [
                analyze_item if item["level"] == "analyze" else item
                for item in result["raw"]["items"]]
        with self._lock:
            self._question_model_count += 1
            count = self._question_model_count
        result["model_name"] = QUESTION_MODEL_TEMPLATE.format(n=count)
        result["response_id"] = (
            f"synthetic-metadata-question-response-{count}")
        with self._lock:
            self.question_results[request["request_id"]] = (
                copy.deepcopy(result))
        return copy.deepcopy(result)
