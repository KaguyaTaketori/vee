#!/bin/bash
# Log all Python files in project to a single text file (excluding venv)

OUTPUT_FILE="vee_python_code.txt"

rm -f "$OUTPUT_FILE"

for f in $(find . -name "*.py" -not -path "./venv/*" | sort); do
  echo "========================================"
  echo "FILE: $f"
  echo "========================================"
  cat "$f"
  echo ""
done > "$OUTPUT_FILE"

echo "Done! Logged $(grep -c "^FILE:" "$OUTPUT_FILE") files, $(wc -l < "$OUTPUT_FILE") lines to $OUTPUT_FILE"
