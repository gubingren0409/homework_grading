import json

from src.cognitive.engines.deepseek_engine import DeepSeekCognitiveEngine


def _new_engine() -> DeepSeekCognitiveEngine:
    return DeepSeekCognitiveEngine()


def test_repair_invalid_json_escapes_for_latex_parentheses():
    engine = _new_engine()
    raw = '{"x":"formula \\( f=1 \\)"}'
    repaired = engine._repair_invalid_json_escapes(raw)  # type: ignore[attr-defined]
    assert repaired == '{"x":"formula \\\\( f=1 \\\\)"}'
    parsed = json.loads(repaired)
    assert parsed["x"] == "formula \\( f=1 \\)"


def test_parse_json_object_repairs_and_parses():
    engine = _new_engine()
    raw = '{"teacher_rubric":{"question_id":"q1","correct_answer":"A","grading_points":[{"point_id":"p1","description":"Use \\(x\\)","score":1}]}}'
    parsed = engine._parse_json_object(raw)  # type: ignore[attr-defined]
    assert parsed["teacher_rubric"]["question_id"] == "q1"
