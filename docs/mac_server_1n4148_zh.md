# Mac 上用服务器大模型运行 1N4148

以下命令以 macOS 终端（zsh）、服务器提供 OpenAI 兼容的 `/v1/chat/completions` 接口为前提。这个仓库读取 **MinerU 生成的 Markdown**，因此先确认 1N4148 的 PDF 已转换为 Markdown。模型名称以服务器的 `/v1/models` 返回值为准。

## 1. 下载代码并确认运行环境

```bash
cd ~/Desktop
git clone https://github.com/yeoh192/D2S-FLOW-reproduction.git
cd D2S-FLOW-reproduction
python3 --version
```

要求 Python 3.10 或更新版本。主流程只用标准库。可选的 ngspice 用于加载验证；若未安装，`06_validation.json` 中该项显示 `not_run`。已安装时可用 `ngspice --version` 确认。

## 2. 先做无模型演示

```bash
python3 run.py --sample 1N4148 --output runs/1N4148-offline
cat runs/1N4148-offline/1N4148/05_generated_model.lib
```

这一步读取仓库自带的短摘录，作用是检查本机 Python、路径和文件输出。它不会调用服务器。

## 3. 连接服务器模型

若服务器上的 OpenAI 兼容服务只监听 `127.0.0.1:8000`，在一个**单独的 Mac 终端窗口**建立 SSH 转发，并保持该窗口打开：

```bash
ssh -N -L 18000:127.0.0.1:8000 '用户名@服务器地址'
```

在运行代码的终端中设置接口：

```bash
export D2S_LLM_BASE_URL='http://127.0.0.1:18000/v1'
export D2S_LLM_API_KEY='EMPTY'
curl -fsS "$D2S_LLM_BASE_URL/models" -H "Authorization: Bearer $D2S_LLM_API_KEY" | python3 -m json.tool
```

从返回的 `data[].id` 选择模型名，并设置：

```bash
export D2S_LLM_MODEL='<上一步返回的模型ID>'
```

若服务有自己的 API Key，请把 `D2S_LLM_API_KEY` 设置成实际值。若 Mac 可以直接访问服务器接口，可以不建 SSH 转发，直接将 `D2S_LLM_BASE_URL` 设为服务器的 `/v1` 地址。

先发一个与源码相同格式的最小请求，确认模型能输出 JSON：

```bash
PYTHONPATH=src python3 - <<'PY'
from d2sflow.llm import chat_json
print(chat_json('Return a JSON object only.', 'Return {"ok": true}.'))
PY
```

如果服务器对 `response_format` 返回 HTTP 400，可以执行 `export D2S_LLM_RESPONSE_FORMAT=off` 后重试。代码仍要求模型的最终内容是可解析的 JSON。

## 4. 使用完整的 1N4148 数据手册转换结果

将下方第一个路径换成 Mac 上真实的 MinerU Markdown 路径。`--reference-model` 可选，只在生成模型后读取厂家模型用于对比；没有该文件时删去该参数行即可。

```bash
python3 run.py \
  --sample 1N4148 \
  --markdown '/Users/192y/电气/JSON（MinerU）/1N4148_数据手册/1N4148_数据手册.md' \
  --reference-model '/Users/192y/电气/器件手册与模型/1N4148/1n4148.lib' \
  --llm \
  --output runs/1N4148-server
```

这次模型依次参与 AGDF 候选排序、HDER 章节预测、HNEN 名称归一、器件分类和参数抽取。1N4148 的 `CJO` 从手册抽取；论文 Appendix E.1 为同系列示例提供的 `IS=25 nA`、`VJ=0.7 V`、`M=0.5` 作为显式默认值。代码将模型输出的电容单位换算为 F，并检查证据短语确实出现在锁定的 Markdown 中。

## 5. 查看结果

```bash
python3 -m json.tool runs/1N4148-server/1N4148/llm_stages_raw.json | less
python3 -m json.tool runs/1N4148-server/1N4148/04_extracted_parameters.json | less
cat runs/1N4148-server/1N4148/05_generated_model.lib
python3 -m json.tool runs/1N4148-server/1N4148/06_validation.json | less
```

重点检查三项：

1. `04_extracted_parameters.json` 中 `CJO` 的原始数值、原始单位、换算后的 F 值，以及 `f=1 MHz, VR=0 V` 条件；若选择最大值，预期为 `4e-12 F`。
2. `05_generated_model.lib` 应出现 `.MODEL ... D(IS=2.5e-08 CJO=4e-12 VJ=0.7 M=0.5)`（浮点格式可能略有差异）。
3. 若装有 ngspice，`06_validation.json` 的 `syntax.status` 应为 `pass`。解析式计算的截止频率约为 `155.56 MHz`，这只验证论文 Appendix E 的简化模型计算一致性。

若程序提示 `LLM evidence ... was not found verbatim`，先看 `llm_stages_raw.json` 的 `extraction`：模型可能改写了原文或选错了表格行。此时不能跳过证据检查直接接受数值。若接口连接失败，先检查 SSH 转发窗口、`D2S_LLM_BASE_URL`、模型 ID 和 API Key。
