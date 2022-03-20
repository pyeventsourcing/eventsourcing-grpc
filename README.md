# Welcome to Eventsourcing gRPC

This package supports running an application as a gRPC server. A gRPC client
can be used to interact with the application server. Application command
and query methods can be called remotely, and notifications selected remotely.

A system of applications can be run as a set of gRPC servers. Leader and
follower relations and are supported with gRPC clients.

A runner class is provided for simple orchestration, which starts gRPC servers
and sets up connections using gRPC clients.

Docker can also be used to run a gRPC server. Clients can interact with the
gRPC server running inside the container.

Docker-compose and Kubernetes can be used to orchestrate a system of applications.

A tier of Web servers can be used connect to the application servers, presenting
a Web interface to the applications.

## Installation

Use pip to install the [stable distribution](https://pypi.org/project/eventsourcing-grpc/)
from the Python Package Index.

    $ pip install eventsourcing_grpc

It is recommended to install Python packages into a Python virtual environment.


## Getting started

...

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

    $ make gen-certs

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
