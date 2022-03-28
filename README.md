# Welcome to Eventsourcing gRPC

This package provides gRPC server and client classes that support running an
event-sourced application as a gRPC server. A gRPC client can be used to interact
with the application server. Application command and query methods can be called
remotely, and notifications selected remotely.

The package also provides a runner class, with the same interface as the Runner
classes in the core eventsourcing library, which can run a system of event-sourced
applications as a set of gRPC servers. Leader and follower connections, for prompting
and pulling event notifications, are implemented with gRPC clients.

This package also provides an example of running an event-sourced application in
gRPC server in a Docker container.

This package also provides an example of running a system of applications
in gRPC servers in Docker containers with docker-compose.

This package also provides an example of running a system of applications
in gRPC servers in Docker containers with Kubernetes, with clients and
servers authenticated with SSL/TLS PKI certificates.

## Installation

Use pip to install the [stable distribution](https://pypi.org/project/eventsourcing-grpc/)
from the Python Package Index.

    $ pip install eventsourcing_grpc

It is recommended to install Python packages into a Python virtual environment.


## gRPC client and server

Define an event sourced application.

```python
from eventsourcing_grpc.example import Orders
```

Configure with environment variables.

```python

env_orders = {
    "ORDERS_GRPC_SERVER_ADDRESS": "localhost:50051",
}

```

Start a server.

```python

from eventsourcing_grpc.application_server import start_server

server = start_server(app_class=Orders, env=env_orders)

```

Connect to server.

```python
from eventsourcing_grpc.application_client import connect

client = connect(Orders, env_orders, max_attempts=10)

```

Get application proxy.

```python

app = client.app

```

Call command and query methods on application using client's application proxy.

```python
# Create order.
order_id = app.create_new_order()

# Get order.
order = app.get_order(order_id)
assert order["id"] == order_id
assert order["is_reserved"] == False
assert order["is_paid"] == False

```

Get application's event notifications.

```python
notifications = app.notification_log.select(start=1, limit=10)
assert len(notifications) == 1
assert notifications[0].id == 1
assert notifications[0].originator_id == order_id
assert notifications[0].originator_version == 1
assert notifications[0].topic == "eventsourcing_grpc.example:Order.Created"
```

Close client.

```python
client.close()
```

Stop server.
```python
server.stop()
```

```python
from time import sleep

# sleep(3)
```

## gRPC system runner

Define an event-sourced system.

```python
from eventsourcing_grpc.example import system
```

Configure using environment variables.

```python
system_env = {
    "ORDERS_GRPC_SERVER_ADDRESS": "localhost:50051",
    "RESERVATIONS_GRPC_SERVER_ADDRESS": "localhost:50052",
    "PAYMENTS_GRPC_SERVER_ADDRESS": "localhost:50053",
}
```

Run system using gRPC.

```python

from eventsourcing_grpc.runner import GrpcRunner

runner = GrpcRunner(system=system, env=system_env)
runner.start(with_subprocesses=True)

if runner.has_errored.is_set():
    raise AssertionError("Couldn't start runner")

# Create an order.
orders = runner.get(Orders)
order1_id = orders.create_new_order()

# Wait for the processing to happen.
for _ in range(100):
    sleep(0.1)
    if orders.is_order_paid(order1_id):
        break
    elif runner.has_errored.is_set():
        raise AssertionError("Runner error")
else:
    raise AssertionError("Timeout waiting for order to be paid")

# Get the notifications.
notifications = orders.notification_log.select(start=1, limit=10)
assert len(notifications) == 3
assert notifications[0].id == 1
assert notifications[0].originator_id == order1_id
assert notifications[0].originator_version == 1
assert notifications[0].topic == "eventsourcing_grpc.example:Order.Created"
assert notifications[1].id == 2
assert notifications[1].originator_id == order1_id
assert notifications[1].originator_version == 2
assert notifications[1].topic == "eventsourcing_grpc.example:Order.Reserved"
assert notifications[2].id == 3
assert notifications[2].originator_id == order1_id
assert notifications[2].originator_version == 3
assert notifications[2].topic == "eventsourcing_grpc.example:Order.Paid"

# Stop runner.
sleep(1)
runner.stop()

```

## Developers

After cloning the eventsourcing-grpc repository, set up a virtual
environment and install dependencies by running the following command in the
root folder.

    $ make install

The ``make install`` command uses the build tool Poetry to create a dedicated
Python virtual environment for this project, and installs popular development
dependencies such as Black, isort and pytest.

Add tests in `./tests`. Add code in `./eventsourcing_grpc`.

Generate SSL certificates and private keys for testing.

    $ make ssl

Run tests.

    $ make test

Check the formatting of the code.

    $ make lint

Reformat the code.

    $ make fmt

Add dependencies in `pyproject.toml` and then update installed packages.

    $ make update-packages

If you edit the .proto files, regenerate the project's protos package.

    $ make grpc-stubs
