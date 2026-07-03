#tweak this file to make the .json coordinates file for other chromosomes
import json
# Parameters
chrom = "chr8"
chrom_length = 145138636  # GRCh38 length
segment_size = 1_000_000

# Build sequence_coordinates as a flat dict
sequence_coordinates = {}
for i, start in enumerate(range(0, chrom_length - segment_size, segment_size), 1):
    key = f"seq{i}"
    sequence_coordinates[key] = [chrom, start]

# Assemble the full request
request_payload = {
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
    "sequence_coordinates": sequence_coordinates
}

# Write to JSON file
with open("./chr8_sequence_coordinates.json", "w") as f:
    json.dump(request_payload, f, indent=2)

print(f"Wrote {len(sequence_coordinates)} sequence segments to chr8_sequence_coordinates.json")