'''Calculate and save the final evaluation metrics (Orca, interaction_matrix / Hi-C)

This module computes Pearson r between the Predictor's predicted 250x250 contact
matrices and the measured Hi-C target matrices for each sent sequence, then reports
the mean across sequences as a single per-task value.

Unlike the MPRA evaluators (e.g. Gosai), there is no tabular measured-data file: the
"measured" target for each 1 Mb tile is reconstructed on the fly from the Orca
resources via `target_h1esc_1m`, in the same log-ratio space the predictor sends back.

All-or-nothing / NaN policy (matches the other GAME evaluators):
  * Predictor side (stern): every SENT sequence must return a finite, correctly
    shaped matrix. A missing seq_id, a wrong-shape matrix, or ANY NaN/inf in a
    returned matrix disqualifies the whole task -> value = "NaN".
  * Measured side (forgiving): no-data positions in the Hi-C target are masked out
    before correlating; a tile whose target is entirely no-data drops out of the
    mean (no penalty -- the gap is from the evaluator, not the predictor).
  * Zero variance after masking -> 0.0 ("ran but useless"), never NaN.

NOTE: Every evaluator does this slightly differently depending on how data is presented.
'''

import os
import sys
import json
import warnings
from datetime import datetime, timezone

import numpy as np
import pandas as pd
import torch
from scipy.stats import pearsonr

from config import EVALUATOR_NAME, EVALUATOR_INPUT_PATH, EVALUATOR_DESCRIPTION

# --- Load Orca resources once (target + model normalization constants) ---
ORCA_PATH = '/orca/'
sys.path.append(ORCA_PATH)
import orca_predict
orca_predict.load_resources(models=['1M'], use_cuda=torch.cuda.is_available())
from orca_predict import h1esc_1m, target_h1esc_1m

SEQ_LEN = 1_000_000
MATRIX_DIM = 250  # Orca 1M output is 250 x 250 at 4 kb resolution


def _compute_target_matrix(chrom, coord, model):
    """
    Reconstruct the measured Hi-C target for one 1 Mb tile as a 250x250 log-ratio
    matrix, in the same space the Orca model emits. Math preserved verbatim from the
    original Orca evaluator so predicted/measured stay comparable.

    Returns a (250, 250) float array that may contain NaNs (no-data positions).
    """
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", category=RuntimeWarning)
        target = target_h1esc_1m.get_feature_data(chrom, coord, coord + SEQ_LEN)[None, :, :]

        level = 4
        start = 0
        target_r = np.nanmean(
            np.nanmean(
                np.reshape(
                    target[:, start:start + MATRIX_DIM * level, start:start + MATRIX_DIM * level],
                    (target.shape[0], MATRIX_DIM, level, MATRIX_DIM, level)
                ),
                axis=4
            ),
            axis=2
        )
        level = 1
        target_np = np.log(
            (target_r + model.epss[level]) /
            (model.normmats[level] + model.epss[level])
        )[0, :, :]

    return target_np


def _calculate_task_correlation(single_task_data, seq_dict):
    """
    Compute the mean Pearson r for a single prediction task over all SENT sequences.

    Args:
        single_task_data (dict): one entry from the response 'prediction_tasks' list.
        seq_dict (dict): {seq_id: [chrom, coord]} -- the sequences the evaluator sent.

    Returns:
        dict with 'task_name', 'task_type', 'cell_type_actual', and 'pearson_r'
        (float incl. 0.0, or None when the task is disqualified).
    """
    print("\n--- Extracting prediction_task metadata ---")
    task_name = single_task_data.get("name")
    task_type_actual = single_task_data.get("type_actual")
    cell_type_actual = single_task_data.get("cell_type_actual")
    predictions_dict = single_task_data.get("predictions")
    scale_requested = single_task_data.get("scale_prediction_requested")
    scale_actual = single_task_data.get("scale_prediction_actual")

    pearson_r_value = None  # default: disqualified / uncomputable -> "NaN"

    def _result(r):
        return {
            'task_name': task_name,
            'task_type': task_type_actual,
            'cell_type_actual': cell_type_actual,
            'pearson_r': r,
        }

    # --- Task-level disqualifiers (predictor side) ---
    if not isinstance(predictions_dict, dict) or not predictions_dict:
        print(f"No usable 'predictions' for task '{task_name}' -> disqualified (NaN).")
        return _result(None)

    if "error" in predictions_dict:
        print(f"Predictor reported an error for task '{task_name}' -> disqualified (NaN).")
        return _result(None)

    # Only enforce scale if the evaluator actually requested one.
    if scale_requested is not None and scale_actual != scale_requested:
        print(f"Scale mismatch (requested '{scale_requested}', actual '{scale_actual}') "
              f"for task '{task_name}' -> disqualified (NaN).")
        return _result(None)

    model = h1esc_1m
    correlations = {}

    for key, coord_pair in seq_dict.items():
        chrom, coord = coord_pair[0], coord_pair[1]

        # ---- Stern check 1: a SENT sequence must have a prediction ----
        if key not in predictions_dict:
            print(f"Sequence '{key}' was sent but has no prediction -> disqualified (NaN).")
            return _result(None)

        pred_arr = np.asarray(predictions_dict[key], dtype=float)

        # ---- Stern check 2: shape must be exactly 250 x 250 ----
        if pred_arr.shape != (MATRIX_DIM, MATRIX_DIM):
            print(f"Sequence '{key}' prediction has shape {pred_arr.shape}, "
                  f"expected ({MATRIX_DIM}, {MATRIX_DIM}) -> disqualified (NaN).")
            return _result(None)

        # ---- Stern check 3: any NaN/inf in a returned matrix is fatal ----
        # (np.asarray(..., dtype=float) also turns JSON nulls into nan, caught here.)
        if not np.all(np.isfinite(pred_arr)):
            print(f"Sequence '{key}' prediction contains NaN/inf -> disqualified (NaN).")
            return _result(None)

        # ---- Measured side: reconstruct target, mask no-data positions ----
        target_np = _compute_target_matrix(chrom, coord, model)
        valid = np.isfinite(target_np)

        if valid.sum() < 2:
            # Target is (almost) entirely no-data -> our gap, drop tile from the mean.
            print(f"Sequence '{key}': measured target is all/near-all no-data -> dropped from mean.")
            continue

        p = pred_arr[valid]
        m = target_np[valid]

        # ---- Zero variance -> 0.0 ("ran but useless"), computed explicitly ----
        if p.std() == 0 or m.std() == 0:
            print(f"Sequence '{key}': zero variance after masking -> contributes 0.0.")
            correlations[key] = 0.0
            continue

        r = pearsonr(p, m)[0]
        correlations[key] = 0.0 if np.isnan(r) else float(r)
        print(f"{key} correlation: {correlations[key]}")

    # No tile had usable measured data -> nothing correlatable -> NaN (and no ZeroDivision).
    if not correlations:
        print(f"Task '{task_name}': no sequence had usable measured data -> NaN.")
        return _result(None)

    pearson_r_value = sum(correlations.values()) / len(correlations)
    print(f"\nTask '{task_name}' mean Pearson r over {len(correlations)} sequences: {pearson_r_value}")
    return _result(pearson_r_value)


def calculate_and_save_metrics(predictions_data, output_dir):
    """
    Compute per-task metrics from the in-memory response payload and append them to
    the single per-build summary CSV.

    NOTE: Orca takes the in-memory payload (not a saved-file path like Gosai) because
    msgpack-numpy responses carry real ndarrays that don't round-trip through JSON.
    """
    print("----- Starting Orca Evaluation (Pearson r) -----")
    print(f"Correlation metadata will be saved in {output_dir}")

    # Load the sent coordinates (seq_id -> [chrom, coord]).
    with open(EVALUATOR_INPUT_PATH, 'r') as f:
        input_data = json.load(f)
    seq_dict = input_data["sequence_coordinates"]

    predictor_name = predictions_data.get("predictor_name", "UnknownPredictor")
    predictor_name = predictor_name.replace(" ", "_").replace("/", "_")

    evaluation_metrics_filename = f"evaluation_summary_{EVALUATOR_NAME}.csv"
    evaluation_metrics_filepath = os.path.join(output_dir, evaluation_metrics_filename)

    all_task_correlation_results = []

    tasks = predictions_data.get("prediction_tasks", [])
    if not tasks or any(not t.get("predictions") for t in tasks):
        print("WARNING: 'prediction_tasks' missing, empty, or a task has empty predictions.")

    for task_index, single_task_data in enumerate(tasks):
        if not isinstance(single_task_data, dict):
            print(f"WARNING: Task item at index {task_index} is not a dictionary. Skipping!")
            continue

        requested_cell_type = single_task_data.get("cell_type_requested")
        print(f"\nProcessing task {task_index + 1} "
              f"(requested cell type: {requested_cell_type})")

        prediction_task_data_nopredictions = [
            {k: v for k, v in single_task_data.items() if k != "predictions"}
        ]

        task_correlation_dict = _calculate_task_correlation(single_task_data, seq_dict)

        pearson_r_value = task_correlation_dict.get('pearson_r')
        val_str = "NaN" if pearson_r_value is None else str(pearson_r_value)

        timestamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S.%f")

        all_task_correlation_results.append({
            'evaluator_name': EVALUATOR_NAME,
            'description': EVALUATOR_DESCRIPTION,
            'predictor_name': predictor_name,
            'time_stamp': timestamp,
            'metric': 'pearson_r',
            'value': val_str,
            'prediction_task(s)_data': prediction_task_data_nopredictions,
        })

    if all_task_correlation_results:
        summary_df = pd.DataFrame(all_task_correlation_results)
        csv_file_exists = os.path.isfile(evaluation_metrics_filepath)
        try:
            summary_df.to_csv(
                evaluation_metrics_filepath, mode='a',
                sep='\t', header=(not csv_file_exists), index=False
            )
            if csv_file_exists:
                print("Appended to existing summary CSV file")
            else:
                print("Created a new summary CSV file")
            print(f"Saved correlation summary to {evaluation_metrics_filepath}!")
        except IOError:
            print("\nNo correlation results were saved!", file=sys.stderr)
    else:
        print("No task results to save.")
