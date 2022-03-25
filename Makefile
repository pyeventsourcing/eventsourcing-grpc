.EXPORT_ALL_VARIABLES:

# POETRY_VERSION = 1.1.11
POETRY ?= poetry
POETRY_INSTALLER_URL ?= https://install.python-poetry.org

# CONTAINER_IMAGE_GRPC=docker.io/library/pyeventsourcing/eventsourcing-grpc:latest
CONTAINER_IMAGE_GRPC=eventsourcing-grpc:v1
# CONTAINER_IMAGE_GRPC_EXAMPLE=docker.io/library/eventsourcing-grpc-example:latest

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

.PHONY: python-dist
python-dist:
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
	make rm-ssl-root-key

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

.PHONY: rm-ssl-root-key
rm-ssl-root-key:
	rm -f ssl/root/root.key

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

.PHONY: container-image
container-image:
	docker build -t $(CONTAINER_IMAGE_GRPC) -f ./docker/Dockerfile ./

.PHONY: compose-pull
compose-pull:
	@docker-compose pull

.PHONY: compose-build
compose-build:
	@docker-compose build

.PHONY: compose-up
compose-up:
	@docker-compose up -d
	@docker-compose ps

.PHONY: compose-stop
compose-stop:
	@docker-compose stop

.PHONY: compose-down
compose-down:
	@docker-compose down -v --remove-orphans

.PHONY: compose-logs
compose-logs:
	@docker-compose logs --follow --tail="all"

.PHONY: compose-ps
compose-ps:
	docker-compose ps

.PHONY: test-docker
test-docker:
	$(POETRY) run python -m unittest discover docker -k test_order

.PHONY: minikube-container-image
minikube-container-image:
	@eval $$(minikube docker-env) && docker build -t $(CONTAINER_IMAGE_GRPC) -f ./docker/Dockerfile ./

.PHONY: kubernetes-secrets
kubernetes-secrets:

	kubectl delete secret root-ssl-secret --ignore-not-found
	kubectl create secret generic root-ssl-secret \
	--from-file=root.crt=ssl/root/root.crt

	kubectl delete secret orders-ssl-secret --ignore-not-found
	kubectl create secret tls orders-ssl-secret \
	--key ./ssl/orders/orders.key --cert ./ssl/orders/orders.crt

	kubectl delete secret reservations-ssl-secret --ignore-not-found
	kubectl create secret tls reservations-ssl-secret \
	--key ./ssl/reservations/reservations.key --cert ./ssl/reservations/reservations.crt

	kubectl delete secret payments-ssl-secret --ignore-not-found
	kubectl create secret tls payments-ssl-secret \
	--key ./ssl/payments/payments.key --cert ./ssl/payments/payments.crt

.PHONY: kubernetes-deployments
kubernetes-deployments:
	kubectl apply -f ./kubernetes/orders-deployment.yaml
	kubectl apply -f ./kubernetes/reservations-deployment.yaml
	kubectl apply -f ./kubernetes/payments-deployment.yaml

.PHONY: kubernetes-services
kubernetes-services:
	kubectl apply -f ./kubernetes/orders-service.yaml
	kubectl apply -f ./kubernetes/reservations-service.yaml
	kubectl apply -f ./kubernetes/payments-service.yaml

.PHONY: kubernetes-logs-orders
kubernetes-logs-orders:
	kubectl logs $$(kubectl get pods | grep -o "orders\S*" | tail -n1) --follow

.PHONY: kubernetes-logs-reservations
kubernetes-logs-reservations:
	kubectl logs $$(kubectl get pods | grep -o "reservations\S*" | tail -n1) --follow

.PHONY: kubernetes-logs-payments
kubernetes-logs-payments:
	kubectl logs $$(kubectl get pods | grep -o "payments\S*" | tail -n1) --follow

.PHONY: kubernetes-attach-orders
kubernetes-attach-orders:
	kubectl exec -it $(shell kubectl get pods | grep -o "orders\S*") --container orders -- /bin/bash


.PHONY: kubernetes-attach-reservations
kubernetes-attach-reservations:
	kubectl logs $$(kubectl get pods | grep -o "orders\S*" | tail -n1) --follow

.PHONY: kubernetes-attach-payments
kubernetes-attach-payments:
	kubectl logs $$(kubectl get pods | grep -o "orders\S*" | tail -n1) --follow

.PHONY: kubernetes-ingress
kubernetes-ingress:
	minikube addons enable ingress

