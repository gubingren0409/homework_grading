# smoke_20260511_0239 整卷批改报告

- 来源文件：p1.jpg, p2.jpg, p3.jpg, p4.jpg, p5.jpg
- paper_id：teacher-paper-20260511_024238
- 识别题数：15/19
- 总扣分：1.0
- 是否建议人工复核：是
- 卷级复核原因：UNMATCHED_REGION_WITHOUT_RUBRIC；PAGE_OCR_TIMEOUT_REVIEW；MISSING_ANSWER_REGION；感知层提取内容与本题考点无关，疑似跨题错位；FILL_BLANK_ALIGNMENT_RISK；LOW_QUALITY_CROP；感知层提取的文本与本题考点完全不匹配；内容实际为化学乙炔选择题，而非题目要求的通信调制填空题；感知层学生作答文本（化学题）与Rubric题目（物理题）内容不匹配，无法判分；感知提取内容与题目考点完全不匹配（乙炔选择题 vs 收音机电路填空题），无法基于当前证据判分。；学生作答内容与 Rubric 描述的题目考点不匹配；无法建立有效的评判映射；感知层题目与Rubric内容完全不匹配；感知提取的学生作答与Rubric要求作图题目严重不符，无法判分；提取文本与评分标准题目不匹配，疑似题目错位；Rubric与感知层题目内容完全不匹配；IR内容与Rubric题目考点完全不匹配，学生回答的是化学实验而非变压器实验，无法按照给定Rubric评分。；题目编号相同但内容不一致，可能属于数据错配，需人工确认正确题号与对应作答。；学生作答内容与Rubric题目不匹配；学生作答内容与题目考点完全不符

## 运行时概览

- 总耗时（s）：887.428
- 页面 OCR（s）：329.754
- 页面 layout（s）：20.212
- 切题（s）：0.589
- 作答区预处理（s）：0.965
- 作答区 OCR（s）：448.927
- 认知评分（s）：86.806
- 产物写盘（s）：0.163

### 慢调用摘要

- student_page_ocr: 180.011s, provider=unknown, retries=0, fallbacks=0 page=0
- answer_region_ocr: 128.535s, provider=qwen, retries=1, fallbacks=0 question=四/17
- answer_region_ocr: 92.934s, provider=qwen, retries=0, fallbacks=0 question=三/12

## 逐题结果

### 题号 三/13
- 状态：REJECTED_UNREADABLE
- 扣分：0.0
- 置信度：0.00
- 是否建议人工复核：是
- 复核原因：MISSING_ANSWER_REGION

未找到题目 三/13 的对应作答区域。

### 题号 三/14
- 状态：REJECTED_UNREADABLE
- 扣分：0.0
- 置信度：0.00
- 是否建议人工复核：是
- 复核原因：MISSING_ANSWER_REGION

未找到题目 三/14 的对应作答区域。

### 题号 三/15
- 状态：REJECTED_UNREADABLE
- 扣分：0.0
- 置信度：0.00
- 是否建议人工复核：是
- 复核原因：MISSING_ANSWER_REGION

未找到题目 三/15 的对应作答区域。

### 题号 四/16
- 状态：REJECTED_UNREADABLE
- 扣分：0.0
- 置信度：0.00
- 是否建议人工复核：是
- 复核原因：MISSING_ANSWER_REGION

未找到题目 四/16 的对应作答区域。

### 题号 一/1
- 状态：REJECTED_UNREADABLE
- 扣分：0.0
- 置信度：0.00
- 是否建议人工复核：是
- 复核原因：感知层提取内容与本题考点无关，疑似跨题错位；FILL_BLANK_ALIGNMENT_RISK；LOW_QUALITY_CROP
- 提取来源：一/1=missing
- 提取告警：NO_STUDENT_TAGS_FOUND；FILL_BLANK_ALIGNMENT_UNRESOLVED
- crop 回看：file:///E:/ai%E6%89%B9%E6%94%B9/homework_grader_system/data/uploads/paper_crops/paper_crop_%E4%B8%80_1_p1_c1_0aaa7cc7979c43a1aead69010ec82a3d.png；file:///E:/ai%E6%89%B9%E6%94%B9/homework_grader_system/data/uploads/paper_crops/paper_crop_%E4%B8%80_1_p1_c2_569fd35d2b4c47299d46e8bbe64a0470.png

感知层提取的文本内容为乙炔分子相关选项（A.乙炔分子中碳碳原子之间有三对共用电子对等），与本题“收音机接收电磁波/扬声器发出机械波”的考点完全不符，且学生仅在其中一个选项旁标注了“√”，无法判断其本题真实作答。该数据存在严重的跨题错位，无法进行有效批改，故拒绝评分。

### 题号 一/2
- 状态：REJECTED_UNREADABLE
- 扣分：0.0
- 置信度：0.00
- 是否建议人工复核：是
- 复核原因：感知层提取的文本与本题考点完全不匹配；内容实际为化学乙炔选择题，而非题目要求的通信调制填空题；FILL_BLANK_ALIGNMENT_RISK；LOW_QUALITY_CROP
- 提取来源：一/2=missing
- 提取告警：NO_STUDENT_TAGS_FOUND；FILL_BLANK_ALIGNMENT_UNRESOLVED
- crop 回看：file:///E:/ai%E6%89%B9%E6%94%B9/homework_grader_system/data/uploads/paper_crops/paper_crop_%E4%B8%80_2_p1_c1_f9827e960c5e4497966f2578eaa28079.png；file:///E:/ai%E6%89%B9%E6%94%B9/homework_grader_system/data/uploads/paper_crops/paper_crop_%E4%B8%80_2_p1_c2_4f89dfce12b74d5eafe4f9297b6aef5c.png

感知层提取的内容与本题（关于调制、调幅、调谐、解调的填空题）完全无关，实际呈现的是关于乙炔的化学选择题。属于毫无关联的作答，无法进行批改，需人工核实。

### 题号 一/3
- 状态：REJECTED_UNREADABLE
- 扣分：0.0
- 置信度：0.10
- 是否建议人工复核：是
- 复核原因：感知层学生作答文本（化学题）与Rubric题目（物理题）内容不匹配，无法判分；LOW_QUALITY_CROP
- 提取来源：一/3=student_tags
- crop 回看：file:///E:/ai%E6%89%B9%E6%94%B9/homework_grader_system/data/uploads/paper_crops/paper_crop_%E4%B8%80_3_p1_c1_f5c908fac4644a64a669a8473f9f1dfc.png；file:///E:/ai%E6%89%B9%E6%94%B9/homework_grader_system/data/uploads/paper_crops/paper_crop_%E4%B8%80_3_p1_c2_7459947fc2a34354a305611bc9e8f42a.png

感知层提取的学生作答内容（关于乙烷、乙烯、乙炔的性质选择）与标准答案提供的题目（关于电磁波的传播）在学科和内容上完全不一致，无法确定学生实际作答本题的意图。可能存在试题与答题区域错位或提取错误，无法基于当前证据进行有效评分。需要人工复核以确认真正的作答对应关系。

### 题号 一/4
- 状态：SCORED
- 扣分：0.0
- 置信度：0.98
- 是否建议人工复核：否
- 复核原因：LOW_QUALITY_CROP
- 提取来源：一/4=student_tags
- crop 回看：file:///E:/ai%E6%89%B9%E6%94%B9/homework_grader_system/data/uploads/paper_crops/paper_crop_%E4%B8%80_4_p1_c1_47aec9172b084dceaa58367283192a16.png；file:///E:/ai%E6%89%B9%E6%94%B9/homework_grader_system/data/uploads/paper_crops/paper_crop_%E4%B8%80_4_p1_c2_7b7aeabfe6154877ac1eadbb916f48e6.png

第4题（调频信号识别）学生选择选项D，与正确答案一致，完全正确。感知层提取的化学题内容与本题无关，不影响本小题评判。

### 题号 一/5
- 状态：REJECTED_UNREADABLE
- 扣分：0.0
- 置信度：0.00
- 是否建议人工复核：是
- 复核原因：感知提取内容与题目考点完全不匹配（乙炔选择题 vs 收音机电路填空题），无法基于当前证据判分。；FILL_BLANK_ALIGNMENT_RISK；LOW_QUALITY_CROP
- 提取来源：一/5=student_tags
- 提取告警：FILL_BLANK_ALIGNMENT_UNRESOLVED
- crop 回看：file:///E:/ai%E6%89%B9%E6%94%B9/homework_grader_system/data/uploads/paper_crops/paper_crop_%E4%B8%80_5_p1_c1_c5ebdcc26e074469aa7352d83324ad68.png；file:///E:/ai%E6%89%B9%E6%94%B9/homework_grader_system/data/uploads/paper_crops/paper_crop_%E4%B8%80_5_p1_c2_1d3b3e553b9b4996a5a8ca854fcbd477.png

感知层提取的学生作答内容为关于乙炔分子的选择题，与本题（收音机接收电路原理）的考点毫无关联，无法进行有效批改。请人工核验该题对应的实际学生作答，确认是否为扫描或识别错位。

### 题号 二/6
- 状态：REJECTED_UNREADABLE
- 扣分：0.0
- 置信度：0.50
- 是否建议人工复核：是
- 复核原因：学生作答内容与 Rubric 描述的题目考点不匹配；无法建立有效的评判映射；LOW_QUALITY_CROP
- 提取来源：二/6=student_tags
- crop 回看：file:///E:/ai%E6%89%B9%E6%94%B9/homework_grader_system/data/uploads/paper_crops/paper_crop_%E4%BA%8C_6_p1_c1_e06ca7d2179749289e01bfe8632533f7.png；file:///E:/ai%E6%89%B9%E6%94%B9/homework_grader_system/data/uploads/paper_crops/paper_crop_%E4%BA%8C_6_p1_c2_cce0af0e580540a6bced3e0451137c67.png

感知层提取的学生作答内容为四道关于乙炔的选择题（选项分别为 D、A、A、C），而题目标准答案及评分要点是关于虹与霓的填空题（两、一、两、两）。学生作答与本题考点完全无关，无法根据现有评分细则进行批改，需要人工复核确认实际题目对应关系。

### 题号 二/7
- 状态：REJECTED_UNREADABLE
- 扣分：0.0
- 置信度：1.00
- 是否建议人工复核：否
- 复核原因：LOW_QUALITY_CROP
- 提取来源：二/7=missing
- 提取告警：NO_STUDENT_TAGS_FOUND；NO_STUDENT_TAGS_FOUND
- crop 回看：file:///E:/ai%E6%89%B9%E6%94%B9/homework_grader_system/data/uploads/paper_crops/paper_crop_%E4%BA%8C_7_p2_c1_ea79de2760444f7db63e5d7f059ca500.png；file:///E:/ai%E6%89%B9%E6%94%B9/homework_grader_system/data/uploads/paper_crops/paper_crop_%E4%BA%8C_7_p2_c2_bd480ae79007444695258bbe71e8a067.png

试卷未作答（检测到空白卷或无手写作答痕迹）。

### 题号 二/8
- 状态：REJECTED_UNREADABLE
- 扣分：0.0
- 置信度：0.00
- 是否建议人工复核：是
- 复核原因：感知层题目与Rubric内容完全不匹配；LOW_QUALITY_CROP
- 提取来源：二/8=student_tags
- 提取告警：ANSWER_TEXT_INFERRED_WITHOUT_STUDENT_TAGS
- crop 回看：file:///E:/ai%E6%89%B9%E6%94%B9/homework_grader_system/data/uploads/paper_crops/paper_crop_%E4%BA%8C_8_p2_c1_ac82ac3162554b63a2d3caf0ff2c8ca2.png；file:///E:/ai%E6%89%B9%E6%94%B9/homework_grader_system/data/uploads/paper_crops/paper_crop_%E4%BA%8C_8_p2_c2_586dd078a362489ea4bdf3a9acf26eac.png

感知层提取的题目内容与提供的评分标准（Rubric）不匹配。感知层显示为一道化学选择题（高分子化合物单体识别），而Rubric对应的是物理计算题（水滴中红光的传播时间），两者考点完全无关，无法进行统一评判。请人工核查数据归属或重传正确的答题卡与Rubric。

### 题号 二/9
- 状态：REJECTED_UNREADABLE
- 扣分：0.0
- 置信度：0.10
- 是否建议人工复核：是
- 复核原因：感知提取的学生作答与Rubric要求作图题目严重不符，无法判分；LOW_QUALITY_CROP
- 提取来源：二/9=student_tags
- crop 回看：file:///E:/ai%E6%89%B9%E6%94%B9/homework_grader_system/data/uploads/paper_crops/paper_crop_%E4%BA%8C_9_p2_c1_da9634982c2042a185f4167cc1ce1721.png；file:///E:/ai%E6%89%B9%E6%94%B9/homework_grader_system/data/uploads/paper_crops/paper_crop_%E4%BA%8C_9_p2_c2_ce45daf4b4f8481599ed6f7b09f1912a.png

感知提取的学生作答内容与 Rubric 中要求的作图题严重不符：Rubric 要求 '在答题纸上的图中用铅笔和尺画出紫光在水滴中的可能路径'，但感知数据中学生只写下了 'c' 和 'D'，并出现了选择题题干 '9. 下列说法中正确的是' 及选项。这种信息冲突导致无法按正常流程判分，需要人工复核确认学生实际应作答的题目以及正确映射关系。

### 题号 二/10
- 状态：REJECTED_UNREADABLE
- 扣分：0.0
- 置信度：0.50
- 是否建议人工复核：是
- 复核原因：提取文本与评分标准题目不匹配，疑似题目错位；LOW_QUALITY_CROP
- 提取来源：二/10=student_tags
- crop 回看：file:///E:/ai%E6%89%B9%E6%94%B9/homework_grader_system/data/uploads/paper_crops/paper_crop_%E4%BA%8C_10_p2_c1_1fe1426a555b4634a2e03f58f1cf7e60.png；file:///E:/ai%E6%89%B9%E6%94%B9/homework_grader_system/data/uploads/paper_crops/paper_crop_%E4%BA%8C_10_p2_c2_9271453c7a394799b6e3dac32a6cf13e.png

感知层提取的内容与评分标准规定的题目（“二/10”三棱镜色散实验）完全无关，实际提取的是化学第8、9题的选择答案和机械结构图，无法确定学生是否作答本题，也无法进行有效批改。请人工复核确认题目与作答的对应关系。

### 题号 二/11
- 状态：SCORED
- 扣分：1.0
- 置信度：0.98
- 是否建议人工复核：否
- 复核原因：LOW_QUALITY_CROP
- 提取来源：二/11=student_tags
- 提取告警：ANSWER_TEXT_INFERRED_WITHOUT_STUDENT_TAGS
- crop 回看：file:///E:/ai%E6%89%B9%E6%94%B9/homework_grader_system/data/uploads/paper_crops/paper_crop_%E4%BA%8C_11_p2_c1_5fe07504882f4c1cacaf3faeb297bdfc.png；file:///E:/ai%E6%89%B9%E6%94%B9/homework_grader_system/data/uploads/paper_crops/paper_crop_%E4%BA%8C_11_p2_c2_c028e57d76204aafa14f866b9a8c3fbf.png

第11题为多选题，正确答案是A和D。你的答案只选择了D，漏选了A，因此得分1分（共2分）。建议仔细分析增透膜厚度与波长的关系，确认A选项也正确。

### 题号 三/12
- 状态：REJECTED_UNREADABLE
- 扣分：0.0
- 置信度：0.00
- 是否建议人工复核：是
- 复核原因：Rubric与感知层题目内容完全不匹配；FILL_BLANK_ALIGNMENT_RISK；LOW_QUALITY_CROP
- 提取来源：三/12=student_tags
- 提取告警：ANSWER_TEXT_INFERRED_WITHOUT_STUDENT_TAGS；FILL_BLANK_ALIGNMENT_UNRESOLVED
- crop 回看：file:///E:/ai%E6%89%B9%E6%94%B9/homework_grader_system/data/uploads/paper_crops/paper_crop_%E4%B8%89_12_p2_c1_bbfb033d7d2b4b359d16228c1a550a4b.png；file:///E:/ai%E6%89%B9%E6%94%B9/homework_grader_system/data/uploads/paper_crops/paper_crop_%E4%B8%89_12_p2_c2_59f7dfc57c804932978f6f403a3b8651.png

感知层提取的学生作答内容为一道化学选择题（C₄H₆的结构鉴定），而提供的Rubric描述的是电容器电路（LC振荡回路）的物理题，两者考点完全无关，无法进行有效批改。按照判分纪律，本题拒绝批改，需人工复核。

### 题号 四/17
- 状态：REJECTED_UNREADABLE
- 扣分：0.0
- 置信度：0.00
- 是否建议人工复核：是
- 复核原因：IR内容与Rubric题目考点完全不匹配，学生回答的是化学实验而非变压器实验，无法按照给定Rubric评分。；题目编号相同但内容不一致，可能属于数据错配，需人工确认正确题号与对应作答。
- 提取来源：四/17=student_tags
- crop 回看：file:///E:/ai%E6%89%B9%E6%94%B9/homework_grader_system/data/uploads/paper_crops/paper_crop_%E5%9B%9B_17_p3_c1_1ebec8b977134fc4b3bc972f30d20a89.png

学生作答内容为乙炔制备实验，与题目要求的变压器横梁探究实验完全无关，无法基于当前Rubric进行有效判分。疑似IR数据与Rubric题目标题错配，需人工复核。

### 题号 四/18
- 状态：REJECTED_UNREADABLE
- 扣分：0.0
- 置信度：0.10
- 是否建议人工复核：是
- 复核原因：学生作答内容与Rubric题目不匹配
- 提取来源：四/18=student_tags
- 提取过滤：DROPPED_QUESTION_NUMBER_LINE
- 提取告警：ANSWER_TEXT_INFERRED_FROM_OCR_WITHOUT_STUDENT_TAGS
- crop 回看：file:///E:/ai%E6%89%B9%E6%94%B9/homework_grader_system/data/uploads/paper_crops/paper_crop_%E5%9B%9B_18_p4_c1_e367e3286a4e42648d79f1fe41d2a0bf.png；file:///E:/ai%E6%89%B9%E6%94%B9/homework_grader_system/data/uploads/paper_crops/paper_crop_%E5%9B%9B_18_p4_c2_0adf822815134c589472605e76993500.png

感知层提取的学生作答内容（正四面体烷分子式、二氯取代产物、乙烯基乙炔相关选择题）与Rubric提供的物理单摆试题（周期、摆长、质量、最大速度、磁场）完全不符，两者属于不同学科或不同试题，无法建立对应关系。根据最高纪律，拒绝批改并提交人工复核。

### 题号 四/19
- 状态：REJECTED_UNREADABLE
- 扣分：0.0
- 置信度：0.00
- 是否建议人工复核：是
- 复核原因：学生作答内容与题目考点完全不符
- 提取来源：四/19=student_tags
- crop 回看：file:///E:/ai%E6%89%B9%E6%94%B9/homework_grader_system/data/uploads/paper_crops/paper_crop_%E5%9B%9B_19_p4_c1_6db93e9d490d44289d2dbc92ceb7ab9f.png；file:///E:/ai%E6%89%B9%E6%94%B9/homework_grader_system/data/uploads/paper_crops/paper_crop_%E5%9B%9B_19_p4_c2_fb430e1987734aa9a8025718f95e707a.png

感知层提取的学生作答内容为有机化学第19题，而与Rubric提供的物理电磁感应第19题完全不符，无法进行有效评分。可能是数据错配或提取错误，需要人工审查。
