# D2S-FLOW 提示词复现件

论文未公开完整提示词，以下内容依据正文图 2–6 和 Appendix C 可见字段重建，不能视为作者原始 prompt。

## AGDF：候选文档与锁定

输入器件型号及目标参数。先从完整知识库返回候选数据手册，并用编号列出厂家、封装、关键特征和差异；等待用户选择。选择后，将后续查询范围锁定到该文档，直至用户明确更换。

## HDER：章节预测

根据常见数据手册结构，预测目标参数最可能出现的主章节与补充章节。输出 JSON：

```json
{
  "Parameter": "<规范化参数名>",
  "Primary Chapter": "<最可能章节>",
  "Supplementary Chapters": ["<补充章节>"]
}
```

优先在预测章节内检索，主章节给出数值，补充章节给出条件、等级或曲线信息。

## HNEN：名称归一

若器件型号、参数名或章节名没有命中，结合检索片段修正拼写并统一厂家异名。只输出规范化名称和等价异名，再使用规范名重试检索。

## 分类与模板选择

根据器件描述和引脚结构，只能从 `Diode/MOSFET/JFET/BJT/Others` 中选择一个类别。随后选择固定模板：

- Diode: `.model D1 D(IS CJO VJ M)`
- BJT: `.model Q1 NPN(BF TF CJC CJE)`
- MOSFET: `.model M1 NMOS(VTO CGSO CGDO KP)`
- JFET: `.model J1 NJF(VTO CGS CGD BETA)`

逐项返回数值、单位、min/typ/max、测试条件、证据位置和来源类型（数据手册直接值/推导值/经验值）。不要静默填充缺失值。
