#!/usr/bin/env python3
"""Auditable pilot reproduction of the D2S-FLOW paper on local samples.

The paper does not release code, prompts, splits, or its data. This script
therefore reproduces the *workflow* and equations while keeping every
paper-specified step and every local assumption visible in machine-readable
artifacts. The reference SPICE file is read only after model generation.
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CONFIG = ROOT / "config" / "samples.json"
RUNS = ROOT / "runs"


def dump(path: Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def split_sections(text: str):
    matches = list(re.finditer(r"(?m)^#{1,4}\s+(.+?)\s*$", text))
    out = []
    for i, m in enumerate(matches):
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        out.append({"title": m.group(1), "text": text[m.start():end]})
    return out or [{"title": "full document", "text": text}]


def tokens(s: str):
    return set(re.findall(r"[a-z0-9]+", s.lower()))


def agdf(sample, all_samples):
    # Paper: global retrieval presents candidate documents, then the user locks one.
    q = sample["device"].lower().replace("-", "")
    candidates = []
    for other in all_samples:
        name = Path(other["markdown"]).stem.lower().replace("-", "")
        score = 1.0 if q in name else len(tokens(q) & tokens(name)) / max(1, len(tokens(q)))
        candidates.append({"document": other["markdown"], "score": score})
    candidates.sort(key=lambda x: x["score"], reverse=True)
    return {
        "paper_method": "AGDF: retrieve globally, display candidates, user selects one target, then constrain subsequent retrieval to it.",
        "query": sample["device"],
        "candidates": candidates,
        "selection_policy": "pilot manifest stands in for the paper's user click",
        "locked_document": sample["markdown"]
    }


def hder(sample, text):
    # Paper: infer primary/supplementary sections, retrieve within them; top-8 is experimental setup.
    query_terms = tokens(" ".join(sample["queries"] + sum(sample["aliases"].values(), [])))
    primary_titles = ("electrical characteristics", "characteristics")
    supplementary_titles = ("quick reference", "limiting values", "features")
    ranked = []
    for sec in split_sections(text):
        st = tokens(sec["title"] + " " + sec["text"])
        lexical = len(query_terms & st) / max(1, len(query_terms))
        title_l = sec["title"].lower()
        prior = 1.0 if any(x in title_l for x in primary_titles) else 0.35 if any(x in title_l for x in supplementary_titles) else 0.0
        ranked.append({"title": sec["title"], "score": round(lexical + prior, 4), "preview": re.sub(r"\s+", " ", sec["text"])[:400]})
    ranked.sort(key=lambda x: x["score"], reverse=True)
    return {
        "paper_method": "HDER: predict parameter-rich primary and supplementary chapters and organize retrieved evidence hierarchically.",
        "primary_prediction": "Electrical Characteristics / Characteristics",
        "supplementary_prediction": ["Quick reference data", "Limiting values"],
        "retrieval_limit": 8,
        "ranked_sections": ranked[:8]
    }


def hnen(sample, text):
    # Paper uses an LLM for typo and cross-vendor term normalization.
    result = {}
    low = text.lower()
    for canonical, variants in sample["aliases"].items():
        hits = [v for v in variants if v.lower() in low]
        result[canonical] = {"variants": variants, "observed_in_document": hits}
    return {
        "paper_method": "HNEN: normalize device/parameter/section names and retry retrieval after a miss.",
        "implementation": "declared synonym map plus case-insensitive document hit check; edit-distance correction is not needed for these exact part numbers",
        "normalization": result
    }


def make_parameters(sample):
    o = sample["observations"]
    md = Path(sample["markdown"]).read_text(encoding="utf-8", errors="ignore").lower()
    evidence_checks = {}
    for key, row in o.items():
        if row["source"] == "datasheet":
            evidence_checks[key] = {
                "query": row["evidence"],
                "found_in_locked_document": row["evidence"].lower() in md
            }
        else:
            evidence_checks[key] = {"query": row["evidence"], "found_in_locked_document": None, "reason": "paper-derived default"}
    template_fields = {
        "Diode": ["IS", "CJO", "VJ", "M"],
        "BJT": ["BF", "TF", "CJC", "CJE"],
        "MOSFET": ["VTO", "CGSO", "CGDO", "KP"],
        "JFET": ["VTO", "CGS", "CGD", "BETA"]
    }
    if sample["type"] in ("BJT", "JFET"):
        params = {key: dict(o[key]) for key in template_fields[sample["type"]]}
    elif sample["type"] == "Diode":
        params = {k: dict(o[k]) for k in template_fields["Diode"]}
    else:
        if "VGS_TH_TYP" in o:
            vto = o["VGS_TH_TYP"]["value"]
            vto_reason = "datasheet typical VGS(th) mapped to VTO"
            ciss = o["CISS_TYP"]["value"]
            crss = o["CRSS_TYP"]["value"]
            rds = o["RDSON_TYP"]["value"]
        else:
            vto = (o["VGS_TH_MIN"]["value"] + o["VGS_TH_MAX"]["value"]) / 2
            vto_reason = "midpoint of datasheet min/max VGS(th); no typical value is given"
            ciss = o["CISS_MAX"]["value"]
            crss = o["CRSS_MAX"]["value"]
            rds = o["RDSON_MAX"]["value"]
        vgs = o["RDSON_VGS"]["value"]
        # Level-1 triode-region approximation: Rds(on) ~= 1/[KP*(VGS-VTO)].
        kp = 1.0 / (rds * (vgs - vto))
        params = {
            "VTO": {"value": vto, "source": "datasheet_mapping", "derivation": vto_reason},
            "CGDO": {"value": crss, "source": "derived", "derivation": "CGDO ≈ Crss at the stated bias"},
            "CGSO": {"value": ciss - crss, "source": "derived", "derivation": "CGSO ≈ Ciss - Crss at the same bias"},
            "KP": {"value": kp, "source": "derived", "derivation": "KP = 1/[RDS(on)*(VGS-VTO)] using a Level-1 first-order approximation"}
        }
    return {
        "paper_method": "Classify exactly as Diode/MOSFET/JFET/BJT/Others, select the matching fixed template, retrieve its required parameters, and fill it after condition selection.",
        "device_type": sample["type"],
        "template": {
            "Diode": ".model D1 D(IS CJO VJ M)",
            "BJT": ".model Q1 NPN(BF TF CJC CJE)",
            "MOSFET": ".model M1 NMOS(VTO CGSO CGDO KP)",
            "JFET": ".model J1 NJF(VTO CGS CGD BETA)"
        }.get(sample["type"], "unsupported"),
        "observations": o,
        "evidence_checks": evidence_checks,
        "template_parameters": params,
        "assumption_warning": "The paper marks MOSFET empirical values but does not disclose its conversion rules. The equations above are declared pilot assumptions, not claims from the paper."
    }


def model_text(sample, extraction):
    safe = re.sub(r"[^A-Za-z0-9_]", "_", sample["device"])
    p = {k: v["value"] for k, v in extraction["template_parameters"].items()}
    if sample["type"] == "Diode":
        return (f"* D2S-FLOW pilot model generated from datasheet evidence\n"
                f".SUBCKT D2S_{safe} A K\nD1 A K D2S_{safe}_D\n"
                f".MODEL D2S_{safe}_D D(IS={p['IS']:.12g} CJO={p['CJO']:.12g} VJ={p['VJ']:.12g} M={p['M']:.12g})\n.ENDS D2S_{safe}\n")
    if sample["type"] == "MOSFET":
        return (f"* D2S-FLOW pilot model generated from datasheet evidence\n"
                f".SUBCKT D2S_{safe} D G S\nM1 D G S S D2S_{safe}_NMOS W=1 L=1\n"
                f".MODEL D2S_{safe}_NMOS NMOS(LEVEL=1 VTO={p['VTO']:.12g} CGSO={p['CGSO']:.12g} CGDO={p['CGDO']:.12g} KP={p['KP']:.12g})\n.ENDS D2S_{safe}\n")
    if sample["type"] == "BJT":
        return (f"* D2S-FLOW pilot model generated from datasheet evidence\n"
                f".SUBCKT D2S_{safe} C B E\nQ1 C B E D2S_{safe}_NPN\n"
                f".MODEL D2S_{safe}_NPN NPN(BF={p['BF']:.12g} TF={p['TF']:.12g} CJC={p['CJC']:.12g} CJE={p['CJE']:.12g})\n.ENDS D2S_{safe}\n")
    if sample["type"] == "JFET":
        return (f"* D2S-FLOW pilot model generated from datasheet evidence\n"
                f".SUBCKT D2S_{safe} D G S\nJ1 D G S D2S_{safe}_NJF\n"
                f".MODEL D2S_{safe}_NJF NJF(VTO={p['VTO']:.12g} CGS={p['CGS']:.12g} CGD={p['CGD']:.12g} BETA={p['BETA']:.12g})\n.ENDS D2S_{safe}\n")
    raise ValueError(f"No SPICE template for device type {sample['type']}")


def syntax_check(model_path: Path, sample):
    ng = shutil.which("ngspice")
    if not ng:
        return {"status": "not_run", "reason": "ngspice not found"}
    safe = re.sub(r"[^A-Za-z0-9_]", "_", sample["device"])
    if sample["type"] == "Diode":
        body = f"V1 a 0 1\nX1 a 0 D2S_{safe}\n"
    elif sample["type"] == "JFET":
        body = f"VD d 0 1\nVG g 0 -0.5\nX1 d g 0 D2S_{safe}\n"
    elif sample["type"] == "BJT":
        body = f"VC c 0 5\nVB b 0 0.7\nX1 c b 0 D2S_{safe}\n"
    else:
        body = f"VD d 0 1\nVG g 0 3\nX1 d g 0 D2S_{safe}\n"
    net = f'D2S syntax check\n.include "{model_path}"\n{body}.op\n.end\n'
    with tempfile.NamedTemporaryFile("w", suffix=".cir", delete=False) as f:
        f.write(net)
        npath = f.name
    cp = subprocess.run([ng, "-b", npath], text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    Path(npath).unlink(missing_ok=True)
    return {"status": "pass" if cp.returncode == 0 else "fail", "returncode": cp.returncode, "tail": cp.stdout[-1200:]}


def analytical_validation(sample, extraction):
    p = {k: v["value"] for k, v in extraction["template_parameters"].items()}
    if sample["type"] == "Diode":
        vr, r = 10.0, 1000.0
        cj = p["CJO"] / ((1 + vr / p["VJ"]) ** p["M"])
        fc = 1 / (2 * math.pi * r * cj)
        return {"paper_circuit": "RC high-pass filter", "equations": ["Cj=CJO/(1+VR/VJ)^M", "fc=1/(2*pi*R*Cj)"], "VR_V": vr, "R_ohm": r, "Cj_F": cj, "fc_Hz": fc}
    if sample["type"] == "BJT":
        bf, tf = p["BF"], p["TF"]
        return {"paper_circuit": "common-emitter amplifier", "equations": ["gain_dB=20*log10(BF)", "fT=1/(2*pi*TF)"], "gain_dB": 20 * math.log10(bf), "fT_Hz": 1 / (2 * math.pi * tf)}
    if sample["type"] == "JFET":
        vgs, rd, rs = -0.5, 1000.0, 100.0
        id_ = p["BETA"] * (vgs - p["VTO"]) ** 2
        gm = 2 * p["BETA"] * (vgs - p["VTO"])
        gain = gm * rd / (1 + gm * rs)
        fc = 1 / (2 * math.pi * rd * (p["CGS"] + p["CGD"]))
        return {"paper_circuit": "common-source JFET amplifier", "equations": ["ID=BETA*(VGS-VTO)^2", "gm=2*BETA*(VGS-VTO)", "Av=-gm*RD/(1+gm*RS)", "f3dB≈1/[2*pi*RD*(CGS+CGD)]"], "VGS_V": vgs, "RD_ohm": rd, "RS_ohm": rs, "ID_A": id_, "gm_S": gm, "gain_V_per_V": gain, "f3dB_Hz": fc, "caveat": "Small-signal estimate based on datasheet-derived template parameters."}
    vgs, rd = 3.0, 1000.0
    id_ = p["KP"] / 2 * max(0, vgs - p["VTO"]) ** 2
    gm = math.sqrt(2 * p["KP"] * id_) if id_ else 0.0
    gain = gm * rd
    fc = 1 / (2 * math.pi * rd * (p["CGDO"] + p["CGSO"]))
    return {"paper_circuit": "common-source amplifier", "equations": ["ID=KP/2*(VGS-VTO)^2", "gm=sqrt(2*KP*ID)", "|Av|=gm*RD", "f3dB=1/[2*pi*RD*(CGDO+CGSO)]"], "VGS_V": vgs, "RD_ohm": rd, "ID_A": id_, "gm_S": gm, "gain_V_per_V": gain, "f3dB_Hz": fc, "caveat": "This reproduces Appendix E's analytical check. It is an internal-consistency check, not independent proof of physical accuracy."}


def frequency_sweep(path: Path, sample, analytical):
    """Reproduce Appendix E's 1 Hz–1 GHz, 500-point logarithmic sweep."""
    freqs = [10 ** (i * 9 / 499) for i in range(500)]
    if sample["type"] == "Diode":
        fc, a0 = analytical["fc_Hz"], 1.0
    elif sample["type"] == "BJT":
        fc, a0 = analytical["fT_Hz"], 10 ** (analytical["gain_dB"] / 20)
    else:
        fc, a0 = analytical["f3dB_Hz"], analytical["gain_V_per_V"]
    rows = []
    for f in freqs:
        x = f / fc
        mag = x / math.sqrt(1 + x*x) if sample["type"] == "Diode" else a0 / math.sqrt(1 + x*x)
        rows.append((f, mag, 20 * math.log10(max(mag, 1e-300))))
    target = -3.0102999566 if sample["type"] == "Diode" else 20 * math.log10(max(a0, 1e-300)) - 3.0102999566
    closest = min(rows, key=lambda r: abs(r[2] - target))
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["frequency_Hz", "magnitude", "magnitude_dB"])
        w.writerows(rows)
    return {"method": "Appendix E: logarithmic frequency response from 1 Hz to 1 GHz with 500 points (Python numerical equivalent of the paper's MATLAB sweep)",
            "csv": str(path), "theoretical_cutoff_Hz": fc, "nearest_grid_cutoff_Hz": closest[0],
            "relative_grid_error": (closest[0] - fc) / fc}


def parse_reference(path: Path):
    text = path.read_text(encoding="utf-8", errors="ignore")
    out = {}
    for key in ("IS", "CJO", "VJ", "M", "VTO", "KP", "CGSO", "CGDO"):
        ms = re.findall(rf"(?i)(?<![A-Z]){key}\s*=\s*([+\-0-9.eE]+)", text)
        if ms:
            try: out[key] = [float(x) for x in ms]
            except ValueError: pass
    return {"path": str(path), "parameters_found": out, "note": "Read only after generation; vendor models may be Level-3/subcircuit models and are not directly equivalent to the paper's four-parameter Level-1 template."}


def compare_reference(extraction, reference):
    generated = {k.upper(): v["value"] for k, v in extraction["template_parameters"].items()}
    rows = []
    for key, g in generated.items():
        vals = reference["parameters_found"].get(key)
        if not vals:
            continue
        r = vals[0]
        rows.append({"parameter": key, "generated": g, "reference_first_match": r,
                     "relative_difference": None if r == 0 else (g - r) / abs(r)})
    return {
        "comparability_warning": "This is diagnostic only. A four-parameter Level-1 model and a vendor Level-3/subcircuit model are structurally different.",
        "rows": rows
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default=str(CONFIG))
    ap.add_argument("--output", default=str(RUNS))
    ap.add_argument("--llm", action="store_true", help="Use an OpenAI-compatible model to extract datasheet observations")
    ap.add_argument("--llm-model", default=None)
    ap.add_argument("--llm-base-url", default=None)
    args = ap.parse_args()
    cfg = json.loads(Path(args.config).read_text(encoding="utf-8"))
    output_root = Path(args.output).resolve()
    output_root.mkdir(parents=True, exist_ok=True)
    summary = []
    for sample in cfg["samples"]:
        out = output_root / sample["device"]
        out.mkdir(parents=True, exist_ok=True)
        markdown_path = (ROOT / sample["markdown"]).resolve()
        if not markdown_path.exists():
            raise FileNotFoundError(markdown_path)
        sample["markdown"] = str(markdown_path)
        reference_path = sample.get("reference_model")
        if reference_path:
            reference_path = Path(reference_path)
            if not reference_path.is_absolute():
                reference_path = (ROOT / reference_path).resolve()
        text = markdown_path.read_text(encoding="utf-8", errors="ignore")
        llm_stages = None
        if args.llm:
            from d2sflow.llm import run_paper_stages
            llm_stages = run_paper_stages(sample, cfg["samples"], text, model=args.llm_model, base_url=args.llm_base_url)
            dump(out / "llm_stages_raw.json", llm_stages)
            returned = llm_stages["extraction"].get("observations", {})
            for key, existing in sample["observations"].items():
                if existing.get("source") != "datasheet":
                    continue
                row = returned.get(key, {})
                if row.get("status") != "found" or row.get("value") is None:
                    raise ValueError(f"LLM did not extract required datasheet field {key} for {sample['device']}")
                if not row.get("evidence") or row["evidence"].lower() not in text.lower():
                    raise ValueError(f"LLM evidence for {key} was not found verbatim in the locked datasheet")
                sample["observations"][key] = {k: row[k] for k in ("value", "unit", "source", "evidence", "condition", "selection") if k in row}
        a = llm_stages["agdf"] if llm_stages else agdf(sample, cfg["samples"]); dump(out / "01_agdf.json", a)
        h = llm_stages["hder"] if llm_stages else hder(sample, text); dump(out / "02_hder.json", h)
        n = llm_stages["hnen"] if llm_stages else hnen(sample, text); dump(out / "03_hnen.json", n)
        e = make_parameters(sample); dump(out / "04_extracted_parameters.json", e)
        if llm_stages:
            e["llm_classification"] = llm_stages["classification"]
            e["generation_backend"] = "openai-compatible LLM with verbatim evidence validation"
            dump(out / "04_extracted_parameters.json", e)
        model_path = out / "05_generated_model.lib"
        model_path.write_text(model_text(sample, e), encoding="utf-8")
        reference = parse_reference(reference_path) if reference_path and reference_path.exists() else {
            "path": str(reference_path) if reference_path else None,
            "parameters_found": {},
            "note": "No reference model supplied. The demo intentionally excludes vendor model files."
        }
        analytical = analytical_validation(sample, e)
        sweep = frequency_sweep(out / "07_frequency_response.csv", sample, analytical)
        validation = {
            "paper_method": "Appendix E validates diode models with an RC high-pass circuit and MOSFET models with a common-source amplifier.",
            "syntax": syntax_check(model_path, sample),
            "analytical_reproduction": analytical,
            "frequency_sweep": sweep,
            "held_out_reference": reference,
            "held_out_reference_comparison": compare_reference(e, reference),
            "leakage_control": "reference_model was not read until after 05_generated_model.lib was written"
        }
        dump(out / "06_validation.json", validation)
        summary.append({"device": sample["device"], "type": sample["type"], "locked_document": a["locked_document"], "model": str(model_path), "syntax": validation["syntax"]["status"]})
    dump(output_root / "summary.json", summary)
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
