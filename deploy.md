cd /var/py/agent
git pull
docker build -f Dockerfile.agent -t openclaw-agent:latest .
/var/py/agent/.venv/bin/uvicorn main:app --host 0.0.0.0 --port 8008