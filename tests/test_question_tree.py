from src.cognitive.question_tree import QuestionTreeExtractor
from src.schemas.perception_ir import BoundingBox, PerceptionNode, PerceptionOutput


def test_extract_from_markdown_builds_tree_and_rubrics():
    extractor = QuestionTreeExtractor()
    bundle = extractor.extract_from_markdown(
        """
        一、电磁波
        1．第一题答案
        2．第二题答案
        （1）第二题小问答案
        二、霓与虹
        6．第六题答案
        """,
        paper_id="paper-1",
    )

    assert [node.raw_label for node in bundle.question_tree] == ["一、", "二、"]
    assert bundle.question_tree[0].children[1].children[0].normalized_path == ["一", "2", "(1)"]
    assert [rubric.question_id for rubric in bundle.rubrics] == ["一/1", "一/2", "一/2/(1)", "二/6"]


def test_extract_from_markdown_handles_markdown_titles_and_inline_next_question():
    extractor = QuestionTreeExtractor()
    bundle = extractor.extract_from_markdown(
        """
        ## 一、电磁波
        【详解】第一题解析；2．第二题答案
        3．第三题答案
        """,
        paper_id="paper-inline",
    )

    assert [node.raw_label for node in bundle.question_tree] == ["一、"]
    assert [rubric.question_id for rubric in bundle.rubrics] == ["一/2", "一/3"]


def test_extract_from_markdown_does_not_split_decimal_numbers():
    extractor = QuestionTreeExtractor()
    bundle = extractor.extract_from_markdown(
        """
        ## 一、电磁波
        4．接收频率为 FM89.9MHz，结果为 1.3。
        5．下一题答案
        """,
        paper_id="paper-decimal",
    )

    assert [rubric.question_id for rubric in bundle.rubrics] == ["一/4", "一/5"]


def test_extract_from_markdown_ignores_large_numeric_jumps_inside_section():
    extractor = QuestionTreeExtractor()
    bundle = extractor.extract_from_markdown(
        """
        ## 三、电磁振荡
        12．第十二题答案
        13．第十三题答案
        14．第十四题答案
        15．第十五题答案
        23．这是第十四题详解中被 OCR 误拆出来的步骤编号
        24．这是第十五题详解中被 OCR 误拆出来的步骤编号
        ## 四、实验探究
        16．第十六题答案
        """,
        paper_id="paper-jump",
    )

    assert [rubric.question_id for rubric in bundle.rubrics] == [
        "三/12",
        "三/13",
        "三/14",
        "三/15",
        "四/16",
    ]
    assert "23．这是第十四题详解" in bundle.rubrics[3].correct_answer


def test_extract_from_markdown_keeps_duplicate_subquestion_as_answer_text():
    extractor = QuestionTreeExtractor()
    bundle = extractor.extract_from_markdown(
        """
        ## 四、实验探究
        16．第十六题
        (1)第一小问
        (2)第二小问
        （2）第二小问详解，不应再生成一个重复 rubric
        """,
        paper_id="paper-duplicate-subquestion",
    )

    assert [rubric.question_id for rubric in bundle.rubrics] == [
        "四/16",
        "四/16/(1)",
        "四/16/(2)",
    ]
    assert "第二小问详解" in bundle.rubrics[-1].correct_answer


def test_extract_from_markdown_redistributes_concentrated_answer_bank():
    extractor = QuestionTreeExtractor()
    bundle = extractor.extract_from_markdown(
        """
        ## 三、电磁振荡
        12．此时，①电容器上极板带A．正电 B．负电
        ②电流的变化情况是 A．增大 B．不变 C．减小
        ③回路中的能量转化情况是
        13．俯视来看，图中磁场的方向应为
        14．在线圈中插入铁芯后，①该回路的振荡周期
        ②实验发现，电流减小为 0 的时间更短了，原因是
        15．麦克斯韦指出，电场变化得越快，其产生的磁场越强。【答案】12. B C 电场能增加 13.逆时针 14.A 铁芯损耗 15.C
        """,
        paper_id="paper-answer-bank",
    )

    rubrics = {rubric.question_id: rubric.correct_answer for rubric in bundle.rubrics}
    assert "【集中答案】12. B C 电场能增加" in rubrics["三/12"]
    assert "【集中答案】12. B C 电场能增加" in rubrics["三/12/②"]
    assert "【集中答案】14.A 铁芯损耗" in rubrics["三/14/②"]
    assert "【集中答案】15.C" in rubrics["三/15"]
    assert "13.逆时针" not in rubrics["三/15"]


def test_extract_from_markdown_dedupes_adjacent_answer_bank_tokens():
    extractor = QuestionTreeExtractor()
    bundle = extractor.extract_from_markdown(
        """
        ## 三、电磁振荡
        12．第十二题
        13．第十三题
        14．第十四题【答案】12. B B C C 电场能在增加 电场能在增加 13.逆时针 13．逆时针 14.A
        """,
        paper_id="paper-answer-dedupe",
    )

    rubrics = {rubric.question_id: rubric.correct_answer for rubric in bundle.rubrics}
    assert "B B" not in rubrics["三/12"]
    assert "C C" not in rubrics["三/12"]
    assert "电场能在增加 电场能在增加" not in rubrics["三/12"]
    assert rubrics["三/13"].count("逆时针") == 1


def test_extract_from_markdown_redistributes_solution_sections_to_subquestions():
    extractor = QuestionTreeExtractor()
    bundle = extractor.extract_from_markdown(
        """
        ## 四、实验探究
        18．大题题干
        (1) 第一小问
        (2) 第二小问
        (3) 第三小问
        第(1)部分：第一问解析
        结论一
        第(2)部分：第二问解析
        结论二
        第(3)部分：第三问解析
        结论三
        """,
        paper_id="paper-solution-sections",
    )

    rubrics = {rubric.question_id: rubric.correct_answer for rubric in bundle.rubrics}
    assert "【集中解析】第(1)部分：第一问解析" in rubrics["四/18/(1)"]
    assert "结论一" in rubrics["四/18/(1)"]
    assert "【集中解析】第(2)部分：第二问解析" in rubrics["四/18/(2)"]
    assert "第(1)部分：第一问解析" not in rubrics["四/18/(3)"]
    assert "第(3)部分：第三问解析" in rubrics["四/18/(3)"]


def test_extract_from_markdown_builds_atomic_grading_points_from_score_marks():
    extractor = QuestionTreeExtractor()
    bundle = extractor.extract_from_markdown(
        """
        ## 四、实验探究
        18．大题题干
        (1) 单摆的周期 T 和摆长 L.
        周期：T = 4/7π s（2 分）
        由单摆周期公式求 L（1 分）
        摆长：L = 0.800m（1 分）
        (2) 摆球质量 m 和最大速度 vm.
        端点受力方程（1 分）
        最低点受力方程（1 分）
        机械能守恒（1 分）
        质量：m = 0.0799kg（1 分）
        最大速度：vm = 0.142m/s（2 分）
        方法二：等价方法
        替代推导不应重复计分（6 分）
        (3) 求磁场 B 的大小和方向。
        洛伦兹力不做功（1 分）
        第一次经过最低点和第二次经过最低点的拉力不同，需要分析洛伦兹力。
        磁场方向：垂直纸面向里（1 分）
        磁场大小：B = 0.0493T（2 分）
        """,
        paper_id="paper-grading-points",
    )

    points_by_question = {
        rubric.question_id: rubric.grading_points
        for rubric in bundle.rubrics
    }

    assert sum(point.score for point in points_by_question["四/18/(1)"]) == 4
    assert sum(point.score for point in points_by_question["四/18/(2)"]) == 6
    assert sum(point.score for point in points_by_question["四/18/(3)"]) == 6
    assert len(points_by_question["四/18/(1)"]) == 4
    assert len(points_by_question["四/18/(2)"]) == 6
    assert len(points_by_question["四/18/(3)"]) == 6
    assert all(point.score == 1.0 for points in points_by_question.values() for point in points)


def test_extract_from_markdown_recovers_informative_descriptions_from_pdf_like_score_blocks():
    extractor = QuestionTreeExtractor()
    bundle = extractor.extract_from_markdown(
        """
        ## 四、实验探究
        18．大题题干
        (1) 单摆的周期 T 和摆长 L.
        周期： � =
        4
        7 � s ≈ 1.80 s （2 分）
        由单摆周期公式：� = 2�
        �
        �，（1 分）
        摆长： � = 0.800 m （1 分）
        (2) 摆球质量 m 和最大速度 vm.
        在最低点 �：设速度为 ��，由牛顿第二定律：�max − �� =
        ���
        2
        � （1 分）
        机械能守恒（� 到 �）：��� 1 − cos� =
        1
        2 ���
        2 （1 分）
        """,
        paper_id="paper-pdf-noise",
    )

    points_by_question = {
        rubric.question_id: rubric.grading_points
        for rubric in bundle.rubrics
    }

    q1_descriptions = [point.description for point in points_by_question["四/18/(1)"]]
    q2_descriptions = [point.description for point in points_by_question["四/18/(2)"]]

    assert any("周期" in description for description in q1_descriptions)
    assert any("由单摆周期公式" in description for description in q1_descriptions)
    assert any("在最低点" in description for description in q2_descriptions)
    assert all(description not in {"�", "2"} for description in q1_descriptions + q2_descriptions)
    assert all(len(description) >= 8 for description in q1_descriptions + q2_descriptions)


def test_extract_from_markdown_redistributes_inline_subquestion_answer_bank():
    extractor = QuestionTreeExtractor()
    bundle = extractor.extract_from_markdown(
        """
        ## 四、实验探究
        16．第十六题
        (1)第一小问
        (2)第二小问【答案】(1)向上拔出 (2)BC
        """,
        paper_id="paper-inline-subquestion-answer",
    )

    rubrics = {rubric.question_id: rubric.correct_answer for rubric in bundle.rubrics}
    assert "【集中答案】(1)向上拔出" in rubrics["四/16/(1)"]
    assert "【集中答案】(2)BC" in rubrics["四/16/(2)"]
    assert "向上拔出" not in rubrics["四/16"]


def test_extract_from_perception_uses_bbox_reading_order():
    extractor = QuestionTreeExtractor()
    perception = PerceptionOutput(
        readability_status="CLEAR",
        elements=[
            PerceptionNode(
                element_id="q2",
                content_type="plain_text",
                raw_content="2．第二题答案",
                confidence_score=0.9,
                bbox=BoundingBox(x_min=0.1, y_min=0.2, x_max=0.5, y_max=0.25),
            ),
            PerceptionNode(
                element_id="title",
                content_type="plain_text",
                raw_content="一、电磁波",
                confidence_score=0.9,
                bbox=BoundingBox(x_min=0.1, y_min=0.05, x_max=0.3, y_max=0.1),
            ),
            PerceptionNode(
                element_id="q1",
                content_type="plain_text",
                raw_content="1．第一题答案",
                confidence_score=0.9,
                bbox=BoundingBox(x_min=0.1, y_min=0.12, x_max=0.5, y_max=0.18),
            ),
        ],
        global_confidence=0.9,
    )

    bundle = extractor.extract_from_perception(perception, paper_id="paper-2")

    assert [rubric.question_id for rubric in bundle.rubrics] == ["一/1", "一/2"]
    assert bundle.rubrics[0].correct_answer == "1．第一题答案"
