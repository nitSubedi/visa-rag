#!/usr/bin/env bash
# Build "Visa Research.app" and a .dmg for Apple Silicon Macs.
#
#   packaging/build_macos.sh [chat.gguf] [embed.gguf]
#
# The model files default to the ones Ollama already has (qwen3:4b-instruct and
# nomic-embed-text) - the same files the evals measured. llama.cpp is pinned so the app
# does not change under us. Output: dist/Visa-Research-<version>-macos-arm64.dmg
set -euo pipefail
cd "$(dirname "$0")/.."

LLAMA_TAG="b11321"
VERSION="0.1.0"
RT="build/runtime"

ollama_blob() {  # $1 = library/model/tag manifest path under ~/.ollama
  python3 - "$1" <<'PY'
import json, os, sys
m = json.load(open(os.path.expanduser(f"~/.ollama/models/manifests/registry.ollama.ai/{sys.argv[1]}")))
d = next(l["digest"] for l in m["layers"] if l["mediaType"].endswith(".model"))
print(os.path.expanduser("~/.ollama/models/blobs/" + d.replace(":", "-")))
PY
}
CHAT="${1:-$(ollama_blob library/qwen3/4b-instruct)}"
EMBED="${2:-$(ollama_blob library/nomic-embed-text/latest)}"

rm -rf "$RT" build/pyinstaller dist
mkdir -p "$RT/bin" "$RT/models"
echo "• llama.cpp $LLAMA_TAG"
curl -sL "https://github.com/ggml-org/llama.cpp/releases/download/$LLAMA_TAG/llama-$LLAMA_TAG-bin-macos-arm64.tar.gz" | tar xz -C build
cp -R "build/llama-$LLAMA_TAG/"* "$RT/bin/"
echo "• models"
cp "$CHAT" "$RT/models/chat.gguf"     # real copies: symlinks do not survive bundling
cp "$EMBED" "$RT/models/embed.gguf"

echo "• app"
uv run --with pyinstaller pyinstaller --noconfirm --clean \
  --workpath build/pyinstaller --distpath dist packaging/visa.spec

APP="dist/Visa Research.app"
# Apple Silicon will not run unsigned code at all. Ad-hoc signing (no Apple account)
# satisfies that; Gatekeeper still asks once: right-click > Open.
echo "• sign (ad hoc)"
codesign --force --deep --sign - "$APP"
codesign --verify --deep --strict "$APP"

echo "• dmg"
STAGE="build/dmg"
rm -rf "$STAGE" && mkdir -p "$STAGE"
mv "$APP" "$STAGE/"   # moved, not copied: the app is ~2.7 GB
ln -s /Applications "$STAGE/Applications"
DMG="dist/Visa-Research-$VERSION-macos-arm64.dmg"
hdiutil create -volname "Visa Research" -srcfolder "$STAGE" -ov -format UDZO "$DMG" >/dev/null
mv "$STAGE/Visa Research.app" "$APP"
ls -lh "$DMG"
