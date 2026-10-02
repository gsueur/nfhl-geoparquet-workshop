#!/usr/bin/env bash
# Copy the data/ tree to the parquetry bucket as the workshop checkpoints:
# https://parquetry.geomermaids.com/nfhl-workshop/ (checkpoints.base_url, web BASE).
# Every stage keeps its relative path, so base_url + '/silver/state=MA/...' just works.
# Requires rclone with a 'parquetry' remote. ZIPs stay local. CORS is open on the bucket.
# --filter, not --include + --exclude: rclone does not order a mix of the two, and
# exFAT AppleDouble files ("._*") once went up that way.
set -euo pipefail
cd "$(dirname "$0")/.."
DEST="${DEST:-parquetry:parquetry/nfhl-workshop}"
F=(--filter "- ._*" --filter "- *.part" --filter "- *.log" --filter "- *.wal"
   --filter "- .DS_Store" --filter "- downloads/**" --filter "- control/*.before_*.duckdb")
rclone copy data "$DEST" "${F[@]}" --filter "+ *.parquet" --filter "- *" \
  --header-upload "Cache-Control: public, max-age=300" \
  --header-upload "Content-Type: application/vnd.apache.parquet" \
  --transfers 8 --s3-chunk-size 64M --stats 60s --stats-one-line
rclone copy data "$DEST" "${F[@]}" --filter "- *.parquet" \
  --header-upload "Cache-Control: public, max-age=300" \
  --s3-chunk-size 64M --s3-upload-concurrency 8 --stats 60s --stats-one-line
