# D2S-FLOW Reproduction

An independent, auditable implementation of the workflow described in **“D2S-FLOW: Automated Parameter Extraction from Datasheets for SPICE Model Generation Using Large Language Models.”** It demonstrates datasheet-focused retrieval, hierarchical section selection, heterogeneous name normalization, fixed-template SPICE generation, and circuit-oriented validation on three semiconductor examples.

This repository is a research reproduction. It is not the authors' original implementation and does not claim their reported benchmark scores. The paper does not provide its source code, prompts, dataset, exact random splits, or all parameter-conversion rules. The implementation records where the paper specifies behavior and where this reproduction makes an explicit engineering choice.

## Quick start

The deterministic demonstration uses only Python 3.10+ standard-library modules. Install `ngspice` to also check model loading; without it, that stage is reported as skipped.

```bash
python3 run.py
```

The generated artifacts are written to `runs/`. Each device has stage records, a generated `.lib`, validation JSON, and a 500-point frequency response CSV. The included datasheet excerpts are small factual demo fixtures; the original vendor PDFs and vendor model files are not redistributed.

To connect a local OpenAI-compatible Qwen server:

```bash
export D2S_LLM_BASE_URL=http://localhost:11434/v1
export D2S_LLM_MODEL=qwen3:8b
export D2S_LLM_API_KEY=ollama
python3 run.py --llm
```

`--llm` calls the model at the paper stages: AGDF candidate ranking, HDER section prediction, HNEN name normalization, device classification, and parameter extraction. The configured sample is the user selection that locks the target document after AGDF candidate display. Raw stage outputs are saved as `llm_stages_raw.json`. Every extracted evidence phrase is checked against the locked source text before it can populate the template. A missing field or unsupported evidence stops generation instead of silently inventing a value. Compatible servers may also be selected with `--llm-base-url` and `--llm-model`.

## Reproduced workflow

| Stage | Paper basis | This implementation |
|---|---|---|
| Datasheet text | Section 4.1 converts PDFs to Markdown while preserving tables | Accepts MinerU/TextIn-compatible Markdown; demo includes concise local excerpts |
| AGDF | Section 3.2A, Figure 2: retrieve candidate documents, let the user select one, then focus retrieval on that document | Candidate ranking and a locked document are recorded in `01_agdf.json`; the demo manifest supplies the selection |
| HDER | Section 3.2B, Figure 3: predict primary and supplementary parameter sections | Ranks sections and keeps the top 8 in `02_hder.json` |
| HNEN | Section 3.2C, Figures 4–5: normalize device, parameter, and section names | Applies declared aliases and records document hits in `03_hnen.json` |
| Type/template | Section 3.3, Figure 6, Table III: classify device, select a type template, extract values and conditions, fill the template | All four Table III templates (diode, BJT, MOSFET, JFET) are implemented; the current local demo covers diode and MOSFET |
| Validation | Section 4.3 and Appendix E: diode RC high-pass response; MOSFET common-source amplifier; frequency sweep from 1 Hz to 1 GHz over 500 logarithmic points | Computes the stated analytical response, writes 500 frequency points, and optionally checks the generated subcircuit with ngspice |

The reconstructed prompts in `prompts/reconstructed_prompts_zh.md` are based on visible paper figures and appendix descriptions. They are not claimed to be the authors' original prompts. Additional method-to-code notes are in `config/paper_method_map.json`.

## Demo samples and assumptions

The demo selects 1N4148 (diode), 2N7002 (MOSFET), and PSMN1R3-30YL (MOSFET). It covers the diode and MOSFET templates discussed in the paper and includes a cross-vendor power MOSFET as a stress case.

The paper's diode Appendix E.1 supplies `IS=25 nA`, `VJ=0.7 V`, and `M=0.5` for the 1N4x48 family. The demo combines those paper values with the datasheet capacitance. For MOSFETs, the paper lists `VTO`, `CGSO`, `CGDO`, and `KP`, and marks some parameters empirical, but does not disclose conversion rules. This reproduction therefore makes its mappings explicit in each JSON result:

- `VTO` comes from the typical threshold voltage, or the midpoint of min/max if the datasheet has no typical value.
- `CGDO` is approximated as `Crss`; `CGSO` is approximated as `Ciss - Crss` at the same bias.
- `KP` is estimated from `RDS(on)` using a first-order Level-1 approximation.

These assumptions make the pipeline executable. They are too simple to represent voltage-dependent capacitance and parasitic networks of detailed vendor models. The output is a low-order baseline, not a production model.

## Use your own datasheet

1. Convert a PDF to Markdown with MinerU or an equivalent table-preserving converter.
2. Add a sample entry to `config/samples.json`: device name, type, Markdown path, query/alias lists, and extracted observations with value, unit, source, condition, and min/typ/max selection.
3. Keep direct datasheet observations distinct from derived values and paper defaults. The `evidence` phrase for a direct observation must occur in the Markdown.
4. Run `python3 run.py --config path/to/your_config.json --output path/to/output`.

For model-driven extraction, add the observation keys to the config and run with `--llm`. Inspect `llm_extraction_raw.json` and evidence checks before using generated models. The small demo fixtures are deliberately not presented as an independently annotated benchmark dataset.

## Repository layout

```text
config/                    Sample definitions and paper-method map
examples/<device>/         Short datasheet excerpts for offline demo
prompts/                   Reconstructed prompts, labeled as reconstructions
src/d2sflow/               Pipeline and OpenAI-compatible local model client
run.py                     Checkout entry point
runs/                      Generated stage outputs (created at runtime)
```

## Scope and reproducibility limits

- The paper reports Qwen-3-8B, Qwen-text-embedding-v2, hybrid retrieval, keyword weight 0.3, similarity threshold 0.2, and top-8 retrieval. This code implements transparent lexical section ranking and top-8; exact embeddings and the RAGFlow/Elasticsearch score calibration require those paper services and versions.
- The paper's 80 datasheets, 80 questions, and three random evaluation sets are not released with this code. Its EM/F1/EC and 20/20 scores cannot be recomputed from this repository.
- Python computes the analytical frequency response rather than running the paper's unpublished MATLAB scripts. ngspice verifies that generated SPICE syntax loads; that check alone does not establish electrical accuracy.
- Reference vendor models are not bundled. You can add a local reference path to a sample configuration for a post-generation parameter comparison. Level-1 templates and detailed vendor subcircuits are structurally different, so raw parameter differences are diagnostic only.

## Citation

Please cite the original paper when using this method. See [`CITATION.cff`](CITATION.cff) for the current bibliographic metadata available in the supplied PDF. Confirm publication details against the final publisher record before formal citation; the supplied manuscript contains a draft IEEE header placeholder.
