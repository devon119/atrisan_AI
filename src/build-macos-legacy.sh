#!/bin/bash
# ABOUT
# Build script for Artisan macOS legacy builds (macOS 10.13+, x86_64)
# Uses PyQt5 + Qt 5.15 via qtpy compatibility layer.
#
# Prerequisites (on the build Mac, macOS 10.13+ with Xcode):
#   brew install python@3.12
#   pip install -r requirements-mac-legacy.txt
#   export QT_API=pyqt5
#   export MACOSX_DEPLOYMENT_TARGET=10.13
#
# LICENSE
# GNU General Public License (GPL) v2 or later

set -e
echo "Python version:"
python3 -V

echo "QT_API=$QT_API"
if [ -z "$QT_API" ]; then
    export QT_API=pyqt5
    echo "QT_API not set, defaulting to pyqt5"
fi

if [ "$QT_API" != "pyqt5" ]; then
    echo "ERROR: QT_API must be 'pyqt5' for legacy builds (got: $QT_API)"
    exit 1
fi

export MACOSX_DEPLOYMENT_TARGET=10.13
echo "MACOSX_DEPLOYMENT_TARGET=$MACOSX_DEPLOYMENT_TARGET"

echo "************* build derived files **************"
./build-derived.sh macos
if [ $? -ne 0 ]; then echo "Failed in build-derived.sh"; exit $?; else echo "** Finished build-derived.sh"; fi

# remove useless .c file from Python site-packages from local build setups
rm -f ${PYTHONPATH}/site-packages/fontTools/misc/bezierTools.c 2>/dev/null || true

rm -rf build dist
sleep .3

echo "************* pyinstaller (legacy) **************"
pyinstaller -y --log-level=INFO artisan-mac-legacy.spec

# size check
version=$(python3 -c "import artisanlib; print(artisanlib.__version__)")
basename="artisan-mac-legacy-$version"
echo "basename: $basename"
min_size=200000000
for suffix in ".dmg"; do
    filename="$basename$suffix"
    size=$(($(du -k "$filename" | cut -f1) * 1024))
    echo "$filename size: $size bytes"
    if [ "$size" -lt "$min_size" ]; then
        echo "$filename is smaller than minimum $min_size bytes"
        exit 1
    else
        echo "**** Success: $filename is larger than minimum $min_size bytes"
    fi
done
