from unittest import TestCase

from eventsourcing_grpc.application_client import ApplicationClient
from eventsourcing_grpc.application_server import ApplicationServer


class TestApplicationServer(TestCase):
    def test_client(self) -> None:
        address = "localhost:50051"
        self.server = ApplicationServer(address=address)
        self.server.start()
        self.client = ApplicationClient(address=address)
        self.client.connect(timeout=1)
