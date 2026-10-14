#!/usr/bin/env bash
# scan -> summary / report / export-html を一気に実行する。
# 配布バンドル（binary / assets 同梱）でも、repo（dist/ ・.venv）でも、PATH 上の
# copilot-usage でも動く。Agent / 自動化からの定期実行を想定し、引数なしでも完走する。
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# bundle layout : binary / assets / この script が同一ディレクトリに同梱される。
# repo layout   : binary は ../dist、assets は ../assets にある。
if [ -x "${SCRIPT_DIR}/copilot-usage" ] || [ -d "${SCRIPT_DIR}/assets" ]; then
  BASE_DIR="${SCRIPT_DIR}"
else
  BASE_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
fi

# ---- 既定値（環境変数 or 引数で上書き可能） ----
OUT_DIR="${COPILOT_USAGE_OUT:-./output}"
DB=""
CLI_LOG_ROOT="${COPILOT_USAGE_CLI_LOG_ROOT:-${HOME}/.copilot/session-state}"
CHAT_STORAGE_ROOT="${COPILOT_USAGE_CHAT_STORAGE_ROOT:-}"
DATE_FROM="${COPILOT_USAGE_FROM:-}"
DATE_TO="${COPILOT_USAGE_TO:-}"
USD_JPY_RATE="${COPILOT_USAGE_RATE:-160}"
MODE="${COPILOT_USAGE_MODE:-both}"   # summary | html | markdown | json | both | all
DETAIL="${COPILOT_USAGE_DETAIL:-0}"  # markdown/json report に detail 明細を含めるか
FULL_SCAN="0"

usage() {
  cat <<'EOF'
Usage: run_pipeline.sh [options]

  --db PATH                 SQLite DB path (default: <out>/copilot-usage.db)
  --out DIR                 Output directory (default: ./output)
  --cli-log-root PATH       Copilot CLI session-state root (default: ~/.copilot/session-state)
  --chat-storage-root PATH  VS Code workspaceStorage root (default: OS standard)
  --from YYYY-MM-DD         Summary/report start date (default: this month start)
  --to YYYY-MM-DD           Summary/report end date (default: this month end)
  --rate N                  USD to JPY rate for report / HTML (default: 160)
  --mode MODE               What to produce after scan (default: both)
                            summary | html | markdown | json | both | all
                              both = summary + html ; all = summary + html + markdown + json
  --detail                  Include per-run detail rows in markdown/json report (default: off)
  --full                    Full rescan (reset scan-derived data)
  -h, --help                Show this help

Environment overrides: COPILOT_USAGE_BIN, COPILOT_USAGE_OUT,
COPILOT_USAGE_CLI_LOG_ROOT, COPILOT_USAGE_CHAT_STORAGE_ROOT,
COPILOT_USAGE_FROM, COPILOT_USAGE_TO, COPILOT_USAGE_RATE, COPILOT_USAGE_MODE,
COPILOT_USAGE_DETAIL
EOF
}

while [ $# -gt 0 ]; do
  case "$1" in
    --db) DB="$2"; shift 2 ;;
    --out) OUT_DIR="$2"; shift 2 ;;
    --cli-log-root) CLI_LOG_ROOT="$2"; shift 2 ;;
    --chat-storage-root) CHAT_STORAGE_ROOT="$2"; shift 2 ;;
    --from) DATE_FROM="$2"; shift 2 ;;
    --to) DATE_TO="$2"; shift 2 ;;
    --rate) USD_JPY_RATE="$2"; shift 2 ;;
    --mode) MODE="$2"; shift 2 ;;
    --detail) DETAIL="1"; shift ;;
    --full) FULL_SCAN="1"; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "unknown option: $1" >&2; usage; exit 2 ;;
  esac
done

# ---- copilot-usage 実行体を解決する（同梱 binary 優先） ----
if [ -n "${COPILOT_USAGE_BIN:-}" ]; then
  BIN="${COPILOT_USAGE_BIN}"
elif [ -x "${SCRIPT_DIR}/copilot-usage" ]; then
  BIN="${SCRIPT_DIR}/copilot-usage"
elif [ -x "${BASE_DIR}/dist/copilot-usage" ]; then
  BIN="${BASE_DIR}/dist/copilot-usage"
elif [ -x "${BASE_DIR}/.venv/bin/copilot-usage" ]; then
  BIN="${BASE_DIR}/.venv/bin/copilot-usage"
elif command -v copilot-usage >/dev/null 2>&1; then
  BIN="copilot-usage"
else
  echo "copilot-usage executable not found." >&2
  echo "Build it (scripts/build_executables.sh) or set COPILOT_USAGE_BIN." >&2
  exit 1
fi

# ---- 既定 DB / chat storage / 期間を解決する ----
mkdir -p "${OUT_DIR}"
[ -n "${DB}" ] || DB="${OUT_DIR}/copilot-usage.db"

if [ -z "${CHAT_STORAGE_ROOT}" ]; then
  case "$(uname -s)" in
    Darwin) CHAT_STORAGE_ROOT="${HOME}/Library/Application Support/Code/User/workspaceStorage" ;;
    *)      CHAT_STORAGE_ROOT="${HOME}/.config/Code/User/workspaceStorage" ;;
  esac
fi

if [ -z "${DATE_FROM}" ] || [ -z "${DATE_TO}" ]; then
  if date -v1d >/dev/null 2>&1; then            # BSD / macOS date
    [ -n "${DATE_FROM}" ] || DATE_FROM="$(date -v1d +%Y-%m-%d)"
    [ -n "${DATE_TO}" ]   || DATE_TO="$(date -v1d -v+1m -v-1d +%Y-%m-%d)"
  else                                          # GNU date
    [ -n "${DATE_FROM}" ] || DATE_FROM="$(date +%Y-%m-01)"
    [ -n "${DATE_TO}" ]   || DATE_TO="$(date -d "$(date +%Y-%m-01) +1 month -1 day" +%Y-%m-%d)"
  fi
fi

echo "binary           : ${BIN}"
echo "db               : ${DB}"
echo "cli-log-root     : ${CLI_LOG_ROOT}"
echo "chat-storage-root: ${CHAT_STORAGE_ROOT}"
echo "period           : ${DATE_FROM} .. ${DATE_TO} (rate ${USD_JPY_RATE})"
echo "mode             : ${MODE}"
echo ""



# ---- 3. scan（存在する log root のみ渡す） ----
SCAN_ARGS=(scan --db "${DB}")
[ -d "${CLI_LOG_ROOT}" ] && SCAN_ARGS+=(--cli-log-root "${CLI_LOG_ROOT}")
[ -d "${CHAT_STORAGE_ROOT}" ] && SCAN_ARGS+=(--chat-storage-root "${CHAT_STORAGE_ROOT}")
[ "${FULL_SCAN}" = "1" ] && SCAN_ARGS+=(--full)
if [ ${#SCAN_ARGS[@]} -eq 3 ]; then
  echo "no log root found (cli-log-root / chat-storage-root). nothing to scan." >&2
  exit 1
fi
echo "[1/2] scan"
"${BIN}" "${SCAN_ARGS[@]}"

# ---- 4. summary / report(markdown|json) / export-html ----
echo "[2/2] report (${MODE})"
if [ "${MODE}" = "summary" ] || [ "${MODE}" = "both" ] || [ "${MODE}" = "all" ]; then
  SUMMARY_OUT="${OUT_DIR}/summary.json"
  "${BIN}" summary --db "${DB}" --from "${DATE_FROM}" --to "${DATE_TO}" --format json | tee "${SUMMARY_OUT}"
  echo ""
  echo "summary written: ${SUMMARY_OUT}"
fi
if [ "${MODE}" = "markdown" ] || [ "${MODE}" = "all" ]; then
  MD_OUT="${OUT_DIR}/copilot-usage-report.md"
  MD_ARGS=(report --db "${DB}" --from "${DATE_FROM}" --to "${DATE_TO}" \
    --usd-jpy-rate "${USD_JPY_RATE}" --format markdown --output "${MD_OUT}")
  [ "${DETAIL}" = "1" ] && MD_ARGS+=(--detail)
  "${BIN}" "${MD_ARGS[@]}"
  echo "markdown report written: ${MD_OUT}"
fi
if [ "${MODE}" = "json" ] || [ "${MODE}" = "all" ]; then
  JSON_OUT="${OUT_DIR}/copilot-usage-report.json"
  JSON_ARGS=(report --db "${DB}" --from "${DATE_FROM}" --to "${DATE_TO}" \
    --usd-jpy-rate "${USD_JPY_RATE}" --format json --output "${JSON_OUT}")
  [ "${DETAIL}" = "1" ] && JSON_ARGS+=(--detail)
  "${BIN}" "${JSON_ARGS[@]}"
  echo "json report written: ${JSON_OUT}"
fi
if [ "${MODE}" = "html" ] || [ "${MODE}" = "both" ] || [ "${MODE}" = "all" ]; then
  REPORT_OUT="${OUT_DIR}/copilot-usage-report.html"
  "${BIN}" export-html --db "${DB}" --from "${DATE_FROM}" --to "${DATE_TO}" \
    --output "${REPORT_OUT}" --usd-jpy-rate "${USD_JPY_RATE}"
fi

echo ""
echo "done."
