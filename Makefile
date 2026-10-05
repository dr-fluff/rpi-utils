
run:
	uv run uvicorn app.main:app --host 127.0.0.1 --port 8002

install-service:
	sudo ./install.sh