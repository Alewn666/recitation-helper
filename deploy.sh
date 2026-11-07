#!/bin/sh
# 背诵助手 —— 部署到 NAS（工作区 → /vol1/1000/项目/背诵助手 → 重建容器）
set -e
SRC="$(cd "$(dirname "$0")" && pwd)"
DST="${RECITE_DST:-/vol1/1000/项目/背诵助手}"

# 自毁护栏：必须在工作区副本里跑
if [ "$SRC" = "$DST" ]; then
  echo "❌ 拒绝运行：当前目录就是部署目标目录（$DST）"; exit 1
fi

echo "==> 同步代码到 $DST"
mkdir -p "$DST/data" "$DST/docs"
# ⚠️ 题库要跟着部署走（容器挂的是 $DST/data → /data）：漏拷就会 500（本轮踩过）
cp "$SRC/data/deck.json" "$DST/data/deck.json"
cp "$SRC/app.py" "$SRC/requirements.txt" "$SRC/Dockerfile" "$SRC/deploy.sh" "$DST/"
rm -rf "$DST/templates" "$DST/static"
cp -r "$SRC/templates" "$SRC/static" "$DST/"
for f in test_engine.py ui_test.py; do [ -f "$SRC/$f" ] && cp "$SRC/$f" "$DST/"; done
[ -d "$SRC/docs" ] && cp -f "$SRC/docs/"*.md "$DST/docs/" 2>/dev/null || true
chown -R 1000:1001 "$DST"; chmod -R u+rwX,g+rwX "$DST"

echo "==> 构建镜像"
cd "$DST"
# ⚠️ 不要写成 `docker build … | tail`：管道会把失败退出码吃掉（踩过：构建被中断却继续用旧镜像重建）
docker build -t recite:latest .

echo "==> 重建容器"
docker stop recite >/dev/null 2>&1 || true
docker rm recite >/dev/null 2>&1 || true
docker run -d --name recite --restart unless-stopped   -p 8090:8090 -e TZ=Asia/Shanghai -e RECITE_DATA_DIR=/data   -v "$DST/data:/data" recite:latest

echo "==> 等待启动"
sleep 4
curl -sf http://127.0.0.1:8090/api/health && echo "  <- 健康检查通过"
echo ""
echo "完成。访问 http://<NAS-IP>:8090　数据目录: $DST/data/"
