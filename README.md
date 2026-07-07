# GAME-Orca-evaluator

Orca-based **Evaluators** for the [GAME (Genomic API for Model Evaluation)](https://github.com/de-Boer-Lab) framework.

Each Evaluator tiles a human chromosome into **1 Mb windows**, fetches the hg38 sequence for every window at Evaluator runtime, and asks a Predictor for an `interaction_matrix` readout (3D chromatin conformation). Each predicted **250 × 250 H1-ESC contact matrix** is then scored against a measured Hi-C target with **Pearson *r***, and the mean *r* across windows is reported as a single per-task value.

The three Evaluators here are very similar and only differ in the data they load in the format `chrN_sequence_coordinates.json`.

| Folder | Role | Chromosome | 1 Mb tiles |
| --- | --- | --- | --- |
| `Test_setChr8_Evaluator/` | Orca **test** set | chr8 | 145 |
| `Test_setChr9_Evaluator/` | Orca **test** set | chr9 | 138 |
| `Validation_setChr10_Evaluator/` | **validation** set | chr10 | 133 |

> All three instances run identical code. The only per-instance difference is the contents of `evaluator_data/` (the coordinates JSON) plus three string values in `config.py`. See [Per-chromosome configuration](#per-chromosome-configuration).

## Important Links

- Main GAME Repository: [de-Boer-Lab/Genomic-API-for-Model-Evaluation](https://github.com/de-Boer-Lab/Genomic-API-for-Model-Evaluation)
- GAME Documentation: [ReadTheDocs](https://genomic-api-for-model-evaluation-documentation.readthedocs.io)
- Pre-built Evaluator container images: Hugging Face — [Chr8 test set](https://huggingface.co/datasets/deBoerLab/Orca_Chr8_testSet_GAME), [Chr9 test set](https://huggingface.co/datasets/deBoerLab/Orca_Chr9_testSet_GAME), [Chr10 validation set](https://huggingface.co/datasets/deBoerLab/Orca_Chr10_validationSet_GAME)
- List of all [GAME Modules](https://github.com/de-Boer-Lab/GAME_modules)

## Quick start

```bash
apptainer run --nv \
    -B /path/to/Test_setChr8_Evaluator/evaluator_data:/evaluator_data \
    -B /path/to/predictions:/predictions \
    orca-testSet-chr8_evaluator.sif <predictor_ip> <predictor_port> /predictions
```

`--nv` exposes the host GPU (the evaluator recomputes Hi-C targets via Orca); `/evaluator_data` must contain the `chrN_sequence_coordinates.json` named in `config.py`; the third argument is the in-container output path. See [Building and running](#building-and-running) for the build step and dev-mode usage.

---

## How it works

```
evaluator_RestAPI.py  (entrypoint, orchestrates the run)
   │
   ├─ data_loader.py            load + validate JSON, fetch hg38 seq per coordinate (seqstr)
   ├─ evaluator_content_handler.py   negotiate format, POST /predict, deserialize
   └─ evaluator_metrics_calculator.py  rebuild Hi-C target matrix, Pearson r, write summary CSV
```

1. **The request.** `evaluator_data/chrN_sequence_coordinates.json` holds a flat `sequence_coordinates` dict of `seqN: [chrom, start]` (one entry per 1 Mb window), wrapped in a request asking for `readout: interaction_matrix` on a single `conformation_chromatin` task (cell type `H1`, `log` scale, `homo_sapiens`).
2. **Load + fetch sequences.** `data_loader.py` parses the JSON (rejecting duplicate keys), then converts each `[chrom, coord]` into a 1 Mb hg38 sequence via `seqstr` (`[hg38]chr:coord-coord+1000000 +`), asserting each is exactly 1,000,000 bp.
3. **Talk to the Predictor.** `evaluator_content_handler.py` negotiates wire formats against the Predictor's `/formats` endpoint, POSTs the payload to `/predict`, and deserializes the response. It prefers **msgpack / msgpack-numpy** and falls back to JSON.
4. **Score.** `evaluator_metrics_calculator.py` loads the Orca 1M resources once, reconstructs the **measured** Hi-C target for each tile on the fly (`target_h1esc_1m`) in the same log-ratio space the model emits, and computes Pearson *r* against the predicted matrix. The mean over tiles is appended to a per-build summary CSV.

### Why msgpack-numpy

- The preferred response format is `application/msgpack-numpy` as it can increase speed of transmission between Evaluator and Predictor
- Raw responses are saved as `.msgpack` when they contain numpy arrays transmitted via msgpack, and `.json` otherwise.

### Why a GPU is required

The measured Hi-C target for each tile is recomputed at runtime from the Orca resources, which is why the container needs `--nv` and loads `orca_predict` with CUDA when available.

### Scoring / NaN policy

- Every *sent* sequence must come back with a finite, correctly shaped (250 × 250) matrix. A missing `seq_id`, a wrong shape, or **any** NaN/inf disqualifies the whole task → value `"NaN"`. A task-level error or scale mismatch (when a scale was requested) also disqualifies.
- No-data positions in the measured Hi-C target are masked before correlating. A tile whose target is entirely no-data is dropped from the mean (no penalty — the gap is on the Evaluator, not the Predictor).
- **Zero variance after masking → `0.0`** ("ran but useless"), never NaN.
- A 200 OK is required, and a length gate confirms every sent sequence got a prediction before metrics run.

---

## Repository layout

Each Evaluator folder is self-contained but follow identical structure:

```
Test_setChr8_Evaluator/
├── config.py                      # per-chromosome settings + container/dev paths + versioning
├── data_loader.py                 # JSON load/validate + seqstr sequence retrieval
├── evaluator_content_handler.py   # format negotiation, POST /predict w/ retry, deserialize
├── evaluator_metrics_calculator.py# rebuild Hi-C target, Pearson r, write summary CSV
├── evaluator_RestAPI.py           # entrypoint / orchestration + payload saving
├── orca-testSet-chr8_evaluator.def# Apptainer/Singularity build recipe
└── evaluator_data/
    ├── chr8_sequence_coordinates.json
    └── README.md                  # how the coordinates JSON is (re)generated
```

| File | Responsibility |
| --- | --- |
| `config.py` | Evaluator name/description, input file, container-vs-dev paths, build-timestamp versioning, request/response formats, retry settings. |
| `data_loader.py` | Duplicate-key-safe JSON parsing; converts coordinates to 1 Mb hg38 sequences via `seqstr`. |
| `evaluator_content_handler.py` | `/formats` negotiation, `/predict` POST with retry loop, msgpack/msgpack-numpy/JSON (de)serialization. |
| `evaluator_metrics_calculator.py` | Reconstructs measured Hi-C targets, computes mean Pearson *r* per task, appends to summary CSV. |
| `evaluator_RestAPI.py` | CLI entrypoint; wires everything together, saves raw payload, gates metric calculation. |
| `*.def` | Apptainer recipe (build base, dependencies, run/start scripts, mounts). |

The coordinates JSON in each `evaluator_data/` is pre-generated and ships with the repo. See the README inside `evaluator_data/` if you need to regenerate it for another chromosome.


## Building and running

Instructions on how to rebuild from scratch are coming soon. 

---

## Request / response schema

**Request** (what the evaluator sends):

```json
{
  "readout": "interaction_matrix",
  "prediction_tasks": [
    {
      "name": "orca_chrom8_eval",
      "type": "conformation_chromatin",
      "cell_type": "H1",
      "scale": "log",
      "species": "homo_sapiens"
    }
  ],
  "sequence_coordinates": {
    "seq1": "ATAGAC...",
    "seq2": "ATGCAT...
  }
}
```

**Response** (what the Evaluator expects back): a `predictor_name`, a `prediction_tasks` list whose entries carry a `predictions` dict keyed by the same `seqN` ids, each mapping to a 250 × 250 matrix. Per-tile metadata (`type_actual`, `cell_type_actual`, `scale_prediction_actual`, …) is read back for reporting and validation.

---

## Output

For each run the Evaluator writes to the mounted output directory:

- **Raw payload** — `{evaluator_name}_predictions_{input_stem}_from_{predictor_name}.{msgpack|json}`
- **Summary CSV** (tab-separated, appended across runs) — `evaluation_summary_{evaluator_name}.csv`, with columns:
  `evaluator_name`, `description`, `predictor_name`, `time_stamp`, `metric` (`pearson_r`), `value` (mean *r* or `"NaN"`), and `prediction_task(s)_data` (task metadata minus the predictions).

---

*Author: Rui Guo, with edits from Ishika Luthra and Satyam Priyadarshi. `game_schema_version` 1.0.*
