# Welcome to Eventsourcing gRPC

Code for running an application as a gRPC process.

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

Run tests.

    $ make test

Check the formatting of the code.

    $ make lint

Reformat the code.

    $ make fmt

Add dependencies in `pyproject.toml` and then update installed packages.

    $ make update-packages
