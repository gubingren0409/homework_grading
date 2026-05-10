from src.utils.question_id_utils import (
    extract_subquestion_slot,
    is_subquestion_token,
    last_numeric_question_token,
    parent_question_id,
    question_token_order,
)


def test_parent_question_id_drops_terminal_subquestion_slot():
    assert parent_question_id("一/18/(2)") == "一/18"
    assert parent_question_id("四/18(2)") == "四/18"
    assert parent_question_id("18/③") == "18"
    assert parent_question_id("18") == "18"


def test_extract_subquestion_slot_normalizes_parenthesized_tokens():
    assert extract_subquestion_slot("18/(2)") == "(2)"
    assert extract_subquestion_slot("18/（2）") == "(2)"
    assert extract_subquestion_slot("18(2)") == "(2)"
    assert extract_subquestion_slot("18/③") == "③"
    assert extract_subquestion_slot("18") is None


def test_is_subquestion_token_supports_parentheses_and_circled_numbers():
    assert is_subquestion_token("(1)") is True
    assert is_subquestion_token("（2）") is True
    assert is_subquestion_token("③") is True
    assert is_subquestion_token("3") is False
    assert is_subquestion_token("一") is False


def test_last_numeric_question_token_skips_terminal_subquestion_slot():
    assert last_numeric_question_token("一/18/(2)") == 18
    assert last_numeric_question_token("四/18(2)") == 18
    assert last_numeric_question_token("18/③") == 18
    assert last_numeric_question_token("一/二/③") is None


def test_question_token_order_supports_numeric_subquestion_tokens():
    assert question_token_order("4") == 4
    assert question_token_order("(2)") == 2
    assert question_token_order("（3）") == 3
    assert question_token_order("⑤") == 5
    assert question_token_order("一") is None
