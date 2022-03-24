.EXPORT_ALL_VARIABLES:

# POETRY_VERSION = 1.1.11
POETRY ?= poetry
POETRY_INSTALLER_URL ?= https://install.python-poetry.org

CONTAINER_IMAGE_GRPC=docker.io/library/pyeventsourcing/eventsourcing-grpc:latest
CONTAINER_IMAGE_GRPC_EXAMPLE=docker.io/library/eventsourcing-grpc-example:latest

COMPOSE_FILE ?= docker/docker-compose.yaml
COMPOSE_PROJECT_NAME ?= eventsourcing_grpc

HOSTNAME ?= $(shell hostname)


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

.PHONY: grpc-stubs
grpc-stubs:
	python -m grpc_tools.protoc \
	  --proto_path=./protos \
	  --python_out=. \
	  --grpc_python_out=. \
	  --mypy_out=. \
	  protos/eventsourcing_grpc/protos/application.proto

.PHONY: build-pkg
build:
	rm -r ./dist/ || true
	$(POETRY) build
# 	$(POETRY) build -f sdist    # build source distribution only

.PHONY: publish
publish:
	$(POETRY) publish

.PHONY: ssl
ssl:
	make ssl-root
	make ssl-server
	HOSTNAME=orders make ssl-server
	HOSTNAME=reservations make ssl-server
	HOSTNAME=payments make ssl-server
	make rm-ssl-root

.PHONY: ssl-root
ssl-root:
	mkdir -p ssl/root
    # Create root key.
# 	openssl genrsa -des3 -out ssl/root/root.key 4096
	openssl genrsa -out ssl/root/root.key 4096   # not password protected
    # Create and self-sign root certificate.
	openssl req -x509 -new -nodes -key ssl/root/root.key \
        -sha256 -days 1024 -out ssl/root/root.crt \
        -subj "/C=GB/ST=London/L=London/O=IT/CN=root.local"

.PHONY: rm-ssl-root
rm-ssl-root:
	rm -rf ssl/root

.PHONY: ssl-server
ssl-server:
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

.PHONY: build-image
build-image:
	docker build -t $(CONTAINER_IMAGE_GRPC) -f ./docker/Dockerfile ./
# 	docker build -t $(CONTAINER_IMAGE_GRPC_EXAMPLE) ./

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

.PHONY: test-docker
test-docker:
	$(POETRY) run python -m unittest discover docker -k test_order
