# `evaluator_data/`

Holds the request file the Evaluator loads at runtime:

```
chr8_sequence_coordinates.json
```

This file is pre-generated and ships with the repo — you do not need to regenerate it to run the Evaluator. `config.py` points `input_file` at it, and the container expects it at `/evaluator_data/chr8_sequence_coordinates.json`.

## What the file contains

A single `interaction_matrix` request: one `conformation_chromatin` prediction task (cell type `H1`, `log` scale, `homo_sapiens`) plus a flat `sequence_coordinates` dict that tiles the chromosome into 1 Mb windows:

```json
"sequence_coordinates": {
  "seq1": ["chr8", 0],
  "seq2": ["chr8", 1000000]
}
```

Each `seqN` is a 1 Mb window start; `data_loader.py` fetches the corresponding hg38 sequence at runtime.

## Regenerating it (`make_json_files.py`)

Use `make_json_files.py` only when you want to rebuild the coordinates for a different chromosome or window size. It tiles the chromosome with `range(0, chrom_length − segment_size, segment_size)` and writes the request JSON.

To target another chromosome, edit the parameters at the top:

```python
chrom        = "chr8"
chrom_length = 145138636   # GRCh38 length
segment_size = 1_000_000
```

```bash
python make_json_files.py
```

Tile count is `floor((chrom_length − 1) / segment_size) + 1` — e.g. 145 (chr8), 138 (chr9), 133 (chr10) at 1 Mb.

