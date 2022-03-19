.EXPORT_ALL_VARIABLES:

# POETRY_VERSION = 1.1.11
POETRY ?= poetry
POETRY_INSTALLER_URL ?= https://install.python-poetry.org

# COMPOSE_FILE ?= docker/docker-compose.yaml
# COMPOSE_PROJECT_NAME ?= eventsourcing-grpc
COMPOSE_ENV_FILE ?= docker/.env

SSL_HOSTNAME ?= $(shell hostname)

-include $(COMPOSE_ENV_FILE)

.PHONY: install-poetry
install-poetry:
	curl -sSL $(POETRY_INSTALLER_URL) | python3
	$(POETRY) --version

.PHONY: install-packages
install-packages:
	$(POETRY) install -vv $(opts)

.PHONY: install-pre-commit-hooks
install-pre-commit-hooks:
ifeq ($(opts),)
	$(POETRY) run pre-commit install
endif

.PHONY: uninstall-pre-commit-hooks
uninstall-pre-commit-hooks:
ifeq ($(opts),)
	$(POETRY) run pre-commit uninstall
endif

.PHONY: install
install: install-poetry install-packages

.PHONY: lock-packages
lock-packages:
	$(POETRY) lock -vv --no-update

.PHONY: update-packages
update-packages:
	$(POETRY) update -vv

.PHONY: lint-black
lint-black:
	$(POETRY) run black --check --diff .

.PHONY: lint-flake8
lint-flake8:
	$(POETRY) run flake8

.PHONY: lint-isort
lint-isort:
	$(POETRY) run isort --check-only --diff .

.PHONY: lint-mypy
lint-mypy:
	$(POETRY) run mypy

.PHONY: lint-python
lint-python: lint-black lint-flake8 lint-isort lint-mypy

.PHONY: lint
lint: lint-python

.PHONY: fmt-black
fmt-black:
	$(POETRY) run black .

.PHONY: fmt-isort
fmt-isort:
	$(POETRY) run isort .

.PHONY: fmt
fmt: fmt-black fmt-isort

.PHONY: test
test:
	$(POETRY) run python -m pytest $(opts) $(call tests,.)

.PHONY: build
build:
	rm -r ./dist/
	$(POETRY) build
# 	$(POETRY) build -f sdist    # build source distribution only

.PHONY: publish
publish:
	$(POETRY) publish

.PHONY: grpc-stubs
grpc-stubs:
	python -m grpc_tools.protoc \
	  --proto_path=./protos \
	  --python_out=. \
	  --grpc_python_out=. \
	  --mypy_out=. \
	  protos/eventsourcing_grpc/protos/application.proto

.PHONY: docker-pull
docker-pull:
	@docker-compose pull

.PHONY: docker-build
docker-build:
	@docker-compose build

.PHONY: docker-up
docker-up:
	@docker-compose up -d
	@docker-compose ps

.PHONY: docker-stop
docker-stop:
	@docker-compose stop

.PHONY: docker-down
docker-down:
	@docker-compose down -v --remove-orphans

.PHONY: docker-logs
docker-logs:
	@docker-compose logs --follow --tail="all"

.PHONY: docker-ps
docker-ps:
	docker-compose ps

.PHONY: ssl-cert
ssl-cert:
	mkdir -p ssl
	openssl req -newkey rsa:2048 -nodes -keyout ssl/server.key -x509 -days 365 \
	-out ssl/server.crt -subj "/C=GB/ST=London/L=London/O=IT/CN=$(SSL_HOSTNAME)"
