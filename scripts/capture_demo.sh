#!/bin/bash
# ถ่ายภาพหน้าจอ + สร้าง GIF จากข้อมูลเดโม (ไม่ใช้ DB จริง) ต้องมี Chrome และ ffmpeg
# ใช้: CHROME="/path/to/Chrome" ./capture_demo.sh OUTDIR
set -e
OUT="$(mkdir -p "${1:-docs}" && cd "${1:-docs}" && pwd)"
cd "$(dirname "$0")"
CHROME="${CHROME:-/Applications/Google Chrome.app/Contents/MacOS/Google Chrome}"
export AI_TEAM_DB=/tmp/ai-team-demo.db AI_TEAM_PORT=8791
python3 dash.py >/dev/null 2>&1 & SRV=$!
trap 'kill $SRV 2>/dev/null' EXIT
sleep 1.5
shot() { "$CHROME" --headless=new --disable-gpu --hide-scrollbars --window-size=1400,900 --screenshot="$1" --virtual-time-budget=3500 "$2" >/dev/null 2>&1; }
JOB="Review%20lease%20clause%204.2%20%28vote%29"
F=/tmp/ai-team-frames; rm -rf $F; mkdir -p $F
python3 demo_seed.py base >/dev/null
shot $F/f1.png "http://localhost:8791/#view=studio&rtab=insp"
python3 demo_seed.py working >/dev/null
shot $F/f2.png "http://localhost:8791/#view=design&rtab=eval"
python3 demo_seed.py partial >/dev/null
shot $F/f3.png "http://localhost:8791/#view=design&rtab=eval"
python3 demo_seed.py done >/dev/null
shot $F/f4.png "http://localhost:8791/#view=jobs&rtab=eval&job=$JOB"
shot $F/f5.png "http://localhost:8791/#modal=seq&job=$JOB"
shot $F/f6.png "http://localhost:8791/#view=design&rtab=eval"
cp $F/f1.png "$OUT/ui-studio.png"; cp $F/f2.png "$OUT/ui-design-working.png"; cp $F/f4.png "$OUT/ui-jobs.png"; cp $F/f5.png "$OUT/ui-flow.png"; cp $F/f6.png "$OUT/ui-design.png"
ffmpeg -y -loglevel error -framerate 0.6 -i $F/f%d.png -vf "scale=1000:-1:flags=lanczos,split[s0][s1];[s0]palettegen=max_colors=128[p];[s1][p]paletteuse=dither=bayer" "$OUT/demo.gif"
ls -l "$OUT"
