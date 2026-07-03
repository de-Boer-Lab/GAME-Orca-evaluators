'''Configuration Script for Evaluator Name, Input File, and Preferred Data Format'''

import os
import json
from datetime import datetime

# ============================================================================
# PER-CHROMOSOME SETTINGS
# ============================================================================
evaluator_base = "orca_chr8_test"
input_file     = "chr8_sequence_coordinates.json"
# Human-readable description written to the 'description' column of the summary CSV.
EVALUATOR_DESCRIPTION = "Orca chr8 test set (H1-ESC) Hi-C"
# ============================================================================

# --- Directory Settings ---
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))

if os.path.exists("/.singularity.d"):
    # Running inside the container
    EVALUATOR_DATA_DIR = "/evaluator_data"

    # Read build timestamp from Apptainer's auto-generated labels to version the name.
    try:
        with open('/.singularity.d/labels.json', 'r') as f:
            labels = json.load(f)
        raw_build_date = labels.get('org.label-schema.build-date', '')

        # Example format: "Friday_28_November_2025_18:6:29_PST"
        parts = raw_build_date.split('_')
        date_str = f"{parts[1]}_{parts[2]}_{parts[3]}_{parts[4]}"
        dt = datetime.strptime(date_str, "%d_%B_%Y_%H:%M:%S")
        build_timestamp = dt.strftime("%Y%m%d-%H%M%S")
        timezone_label = parts[5] if len(parts) > 5 else "UNK"
        EVALUATOR_NAME = f"{evaluator_base}_{build_timestamp}_{timezone_label}"
    except Exception as e:
        print(f"Warning: Could not parse build timestamp from labels.json: {e}")
        EVALUATOR_NAME = f"{evaluator_base}_unknown"
else:
    # Running outside the container (dev mode)
    EVALUATOR_DATA_DIR = os.path.join(SCRIPT_DIR, "evaluator_data")
    EVALUATOR_NAME = f"{evaluator_base}_dev"

EVALUATOR_INPUT_PATH = os.path.join(EVALUATOR_DATA_DIR, input_file)
output_filename_base = f'{EVALUATOR_NAME}_predictions_{input_file.replace(".json", "")}'

print(f"Using input file: {EVALUATOR_INPUT_PATH}")

# --- API Communication Settings ---
REQUEST_FORMAT = "application/msgpack".lower()
RESPONSE_FORMAT = "application/msgpack-numpy".lower()

# HTTP request retry
MAX_RETRIES = 50
RETRY_INTERVAL = 30  # Seconds to wait between each retry attempt
