
run:
	uv run uvicorn app.main:app --host 127.0.0.1 --port 8002

dev:
	uv run uvicorn app.main:app --host 127.0.0.1 --port 8002 --reload --reload-dir app --reload-include '*.py' --reload-include '*.html' --reload-include '*.js' --reload-include '*.css'

install-service:
	sudo ./install.sh