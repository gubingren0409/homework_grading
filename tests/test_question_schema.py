from src.schemas.question_ir import QuestionNumber
from src.schemas.rubric_ir import RubricBundle, TeacherRubric


def test_question_number_supports_nested_tree():
    tree = QuestionNumber.model_validate(
        {
            "raw_label": "一、",
            "normalized_path": ["一"],
            "order_index": 0,
            "children": [
                {
                    "raw_label": "1.",
                    "normalized_path": ["一", "1"],
                    "order_index": 1,
                    "children": [
                        {
                            "raw_label": "(1)",
                            "normalized_path": ["一", "1", "(1)"],
                            "order_index": 2,
                        }
                    ],
                }
            ],
        }
    )

    assert tree.children[0].normalized_path == ["一", "1"]
    assert tree.children[0].children[0].raw_label == "(1)"


def test_rubric_bundle_keeps_per_question_rubrics():
    bundle = RubricBundle(
        paper_id="paper-physics-march",
        rubrics=[
            TeacherRubric(question_id="一/1", correct_answer="A"),
            TeacherRubric(question_id="一/2", correct_answer="B"),
        ],
        question_tree=[
            QuestionNumber(raw_label="一、", normalized_path=["一"], order_index=0),
        ],
    )

    payload = bundle.model_dump()

    assert payload["paper_id"] == "paper-physics-march"
    assert [item["question_id"] for item in payload["rubrics"]] == ["一/1", "一/2"]
    assert payload["question_tree"][0]["raw_label"] == "一、"
