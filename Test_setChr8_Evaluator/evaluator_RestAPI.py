'''RESTful ORCA Evaluator Utilizing Predictor REST API'''

import os
import sys
import json
from requests.exceptions import RequestException, HTTPError

import config
from data_loader import load_and_validate_data
from evaluator_content_handler import negotiate_formats, get_predictions, deserialize_response
import evaluator_metrics_calculator
import msgpack


def run_evaluator(predictor_ip, predictor_port, output_dir):
    """
    Preprocesses the data, sends request, receives response,
    saves the response, and triggers metric calculation.
    """
    if not os.path.exists(output_dir):
        os.makedirs(output_dir, exist_ok=True)
        print(f"Output directory '{output_dir}' did not exist. Created it successfully!")

    # Load and validate evaluator input data (fetches hg38 sequences from coordinates)
    data_dict = load_and_validate_data()

    # Number of sequences we expect predictions for
    total_sequences = len(data_dict["sequences"])

    predictor_url = f"http://{predictor_ip}:{predictor_port}"
    response_payload = None
    is_success_response = False
    resp_fmt = None              # negotiated response MIME type
    resp_content_type = ""       # actual Content-Type of the response (empty until/if 200)

    try:
        # ---- 1) Negotiate wire formats with predictor (/formats) ----
        req_fmt, resp_fmt = negotiate_formats(predictor_url)

        # ---- 2) Send prediction request (/predict) ----
        response = get_predictions(predictor_url, data_dict, req_fmt, resp_fmt)

        # raise_for_status() did NOT raise -> 200 OK
        is_success_response = True
        resp_content_type = response.headers.get("Content-Type", "").lower()
        print("Predictor returned 200 OK.")

        # ---- 3) Decode response in negotiated format ----
        response_payload = deserialize_response(response, resp_fmt)

    except HTTPError as http_err:
        # HTTP 4xx/5xx: treat as an error response from the predictor (always JSON).
        print(f"Predictor returned HTTP {http_err.response.status_code}. Processing error payload...")
        is_success_response = False
        try:
            response_payload = deserialize_response(http_err.response, "application/json")
        except ValueError as decode_err:
            print(f"Could not decode the error response body: {decode_err}", file=sys.stderr)
            response_payload = {
                "predictor_name": "UnknownPredictor_ErrorResponse",
                "error": [{
                    "server_error": (
                        f"Failed to decode error response (Status {http_err.response.status_code}). "
                        f"Body (truncated): {http_err.response.text[:500]}..."
                    )
                }]
            }

    if response_payload is None:
        print("FATAL: No response payload received or processed.", file=sys.stderr)
        response_payload = {
            "predictor_name": "UnknownPredictor_NoResponse",
            "error": [{
                "evaluator_error": "No response payload could be processed after request."
            }]
        }

    # ---- 4) Save the raw predictions / error payload ----
    predictor_name = response_payload.get("predictor_name", "UnknownPredictor").replace(" ", "_")
    saved_predictions_path = os.path.join(
        output_dir, f"{config.output_filename_base}_from_{predictor_name}.json"
    )
    saved_predictions_path_msgpack = os.path.join(
        output_dir, f"{config.output_filename_base}_from_{predictor_name}.msgpack"
    )

    # msgpack-numpy payloads carry real ndarrays -> must be saved as .msgpack, not JSON.
    if "application/msgpack-numpy" in resp_content_type:
        print("Saving Predictor response as .msgpack (contains numpy arrays)")
        try:
            with open(saved_predictions_path_msgpack, 'wb') as f:
                msgpack.dump(response_payload, f, use_bin_type=True)
            print(f"Raw predictions saved to {saved_predictions_path_msgpack}")
        except IOError as e:
            print(f"FATAL: Could not save predictions to {saved_predictions_path_msgpack}. {e}", file=sys.stderr)
            return
    else:
        try:
            with open(saved_predictions_path, 'w', encoding='utf-8') as f:
                json.dump(response_payload, f, ensure_ascii=False, indent=4)
            print(f"Raw predictions saved to {saved_predictions_path}")
        except IOError as e:
            print(f"FATAL: Could not save predictions to {saved_predictions_path}. {e}", file=sys.stderr)
            return

    # ---- 5) Compute metrics only on a 200 OK AND only if counts are complete ----
    if not is_success_response:
        print("Skipping metrics calculation because the Predictor did not return a 200 OK status.")
        return

    # All-or-nothing gate: every sent sequence must come back, per task.
    all_lengths_match = True
    for i, task in enumerate(response_payload.get("prediction_tasks", []), start=1):
        preds = task.get("predictions", {})
        if isinstance(preds, dict) and "error" in preds:
            print(f"Task {i} ('{task.get('name')}') returned an error -- skipping length check.")
            all_lengths_match = False
            continue
        n_preds = len(preds) if hasattr(preds, "__len__") else 0
        if n_preds != total_sequences:
            print(
                f"Warning: Task {i} ('{task.get('name')}') has {n_preds} predictions, "
                f"but {total_sequences} sequences were sent to the Predictor."
            )
            all_lengths_match = False

    if all_lengths_match:
        evaluator_metrics_calculator.calculate_and_save_metrics(response_payload, output_dir)
    else:
        print("Skipping metric calculation because not all sequences got predictions.")


if __name__ == '__main__':
    if len(sys.argv) != 4:
        print(
            "Invalid arguments! Usage:\n"
            "  python evaluator_RestAPI.py <predictor_ip_address> <predictor_port> <mounted_output_directory>"
        )
        sys.exit(1)

    predictor_ip = sys.argv[1]
    predictor_port = int(sys.argv[2])
    output_dir_arg = sys.argv[3]

    try:
        run_evaluator(predictor_ip, predictor_port, output_dir_arg)
        print("Evaluation complete.")
        sys.exit(0)

    except (FileNotFoundError, ValueError) as e:
        print(f"FATAL ERROR (Data): {e}", file=sys.stderr)
        sys.exit(1)

    except RequestException as e:
        print(
            f"FATAL ERROR (Network): Could not connect to predictor at "
            f"http://{predictor_ip}:{predictor_port}. {e}",
            file=sys.stderr
        )
        sys.exit(1)

    except Exception as e:
        print(f"An unexpected fatal error occurred: {e}", file=sys.stderr)
        import traceback
        traceback.print_exc()
        sys.exit(1)
