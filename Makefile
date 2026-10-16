.PHONY: install run test migrate seed docker clean

VENV := .venv
PY := $(VENV)/bin/python
PIP := $(VENV)/bin/pip

install:
	python3 -m venv $(VENV)
	$(PIP) install --index-url https://pypi.org/simple -r requirements.txt

migrate:
	$(PY) manage.py migrate

run: migrate
	$(PY) manage.py runserver

test:
	$(PY) manage.py test

seed: migrate
	$(PY) manage.py seed

docker:
	docker compose up --build

clean:
	rm -f db.sqlite3
	find . -name __pycache__ -type d -prune -exec rm -rf {} +
