.EXPORT_ALL_VARIABLES:

# POETRY_VERSION = 1.1.11
POETRY ?= poetry
POETRY_INSTALLER_URL ?= https://install.python-poetry.org

# COMPOSE_FILE ?= docker/docker-compose.yaml
# COMPOSE_PROJECT_NAME ?= eventsourcing-grpc
COMPOSE_ENV_FILE ?= docker/.env

HOSTNAME ?= $(shell hostname)

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

.PHONY: test-docker
test-docker:
	$(POETRY) run python -m unittest discover docker -k test_order

.PHONY: build
build:
	rm -r ./dist/ || true
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

.PHONY: gen-root-cert
gen-root-cert:
	mkdir -p ssl/root
    # Create root key.
# 	openssl genrsa -des3 -out ssl/root.key 4096
	openssl genrsa -out ssl/root/root.key 4096   # not password protected
    # Create and self-sign root certificate.
	openssl req -x509 -new -nodes -key ssl/root/root.key \
        -sha256 -days 1024 -out ssl/root/root.crt \
        -subj "/C=GB/ST=London/L=London/O=IT/CN=root.local"

.PHONY: gen-server-cert
gen-server-cert:
	mkdir -p ssl/$(HOSTNAME)
    # Create server key.
	openssl genrsa -out ssl/$(HOSTNAME)/$(HOSTNAME).key 2048
    # Create certificate signing request.
	openssl req -new -sha256 -key ssl/$(HOSTNAME)/$(HOSTNAME).key \
        -subj "/C=GB/ST=London/L=London/O=IT/CN=$(HOSTNAME)" \
        -out ssl/$(HOSTNAME)/$(HOSTNAME).csr
    # Verify CSR.
	openssl req -in ssl/$(HOSTNAME)/$(HOSTNAME).csr -noout -text
    # Create certificate.
	openssl x509 -req -in ssl/$(HOSTNAME)/$(HOSTNAME).csr \
        -CA ssl/root/root.crt -CAkey ssl/root/root.key \
        -CAcreateserial -out ssl/$(HOSTNAME)/$(HOSTNAME).crt -days 500 -sha256
    # Verify certificate.
	openssl x509 -in ssl/$(HOSTNAME)/$(HOSTNAME).crt -text -noout
    # Copy root cert.
	cp ssl/root/root.crt ssl/$(HOSTNAME)/root.crt

.PHONY: gen-certs
gen-certs:
	make gen-root-cert
	make gen-server-cert
	HOSTNAME=orders make gen-server-cert
	HOSTNAME=reservations make gen-server-cert
	HOSTNAME=payments make gen-server-cert
