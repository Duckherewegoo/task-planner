#!/usr/bin/env bash
set -e

# 1. 起数据库
docker compose up -d

# 2. 等 mongo 就绪
until docker exec task_mongodb mongosh --eval "db.runCommand('ping')" >/dev/null 2>&1; do
    sleep 1
done
echo "✅ MongoDB 就绪"

# 3. 起 app
echo "🚀 启动 task-planner"
python -m task_planner.main.ui.app
