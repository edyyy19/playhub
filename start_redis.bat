@echo off
echo 🐳 Starting Redis container...
docker-compose up -d redis

echo ⏳ Waiting for Redis to be ready...
:wait_loop
docker-compose exec redis redis-cli ping >nul 2>&1
if %errorlevel% neq 0 (
    timeout /t 1 >nul
    goto wait_loop
)

echo ✅ Redis is ready!
echo 🔴 Redis running on localhost:6379
echo.
echo To stop Redis: docker-compose down
echo To check logs: docker-compose logs redis
echo To connect: docker-compose exec redis redis-cli
