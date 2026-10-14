#!/usr/bin/env bash
# CLI / GUI の standalone executable を PyInstaller で dist/ に生成する。
# repo root から、または任意の作業ディレクトリから実行できる。
set -euo pipefail

# repo root を script 位置から解決する。
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
cd "${REPO_ROOT}"

# venv python を優先し、なければ python3 を使う。
if [ -x "${REPO_ROOT}/.venv/bin/python" ]; then
  PYTHON="${REPO_ROOT}/.venv/bin/python"
else
  PYTHON="${PYTHON:-python3}"
fi

echo "Using Python: ${PYTHON}"
"${PYTHON}" -m PyInstaller --version >/dev/null 2>&1 || {
  echo "PyInstaller not found. Install with: ${PYTHON} -m pip install -e '.[build]'" >&2
  exit 1
}

# 毎回クリーンビルドする（--noconfirm で dist 上書き）。
"${PYTHON}" -m PyInstaller --noconfirm --clean packaging/copilot-usage.spec
"${PYTHON}" -m PyInstaller --noconfirm --clean packaging/copilot-usage-gui.spec

# ---- 自己完結 CLI バンドルを dist/copilot-usage-cli/ に組み立てる ----
# binary + 使い方 README + assets + pipeline script を 1 ディレクトリにまとめ、
# 利用者が選んだ場所へ directory ごと配置できるようにする。
BUNDLE_DIR="${REPO_ROOT}/dist/copilot-usage-cli"
echo ""
echo "Assembling CLI bundle: dist/copilot-usage-cli/"
rm -rf "${BUNDLE_DIR}"
mkdir -p "${BUNDLE_DIR}"
cp "${REPO_ROOT}/dist/copilot-usage" "${BUNDLE_DIR}/copilot-usage"
cp "${REPO_ROOT}/scripts/run_pipeline.sh" "${BUNDLE_DIR}/run_pipeline.sh"
cp "${REPO_ROOT}/scripts/run_pipeline.ps1" "${BUNDLE_DIR}/run_pipeline.ps1"
cp "${REPO_ROOT}/packaging/public/cli_bundle_README.md" "${BUNDLE_DIR}/README.md"
chmod +x "${BUNDLE_DIR}/copilot-usage" "${BUNDLE_DIR}/run_pipeline.sh"

echo ""
echo "Built executables:"
echo "  dist/copilot-usage      (CLI binary)"
if [ "$(uname -s)" = "Darwin" ]; then
  echo "  dist/Coding AI Usage Visualizer.app  (GUI application)"
else
  cp packaging/assets/usage-icon.png dist/usage-icon.png
  LAUNCHER="dist/io.github.tech-research-4827.CodingAIUsageVisualizer.desktop"
  "${PYTHON}" packaging/desktop_launcher.py --executable dist/copilot-usage-gui --icon dist/usage-icon.png --output "${LAUNCHER}"
  echo "  dist/copilot-usage-gui and ${LAUNCHER}  (GUI and launcher)"
fi
echo "Built CLI bundle (copy this whole directory to deploy):"
ls -1 "${BUNDLE_DIR}" | sed 's/^/  dist\/copilot-usage-cli\//'
