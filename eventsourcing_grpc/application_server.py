from concurrent.futures import ThreadPoolExecutor

import grpc
from eventsourcing.application import Application
from grpc._server import _Context as Context

from eventsourcing_grpc.protos.application_pb2 import (
    Empty,
    MethodReply,
    MethodRequest,
    Notification,
    NotificationsReply,
    NotificationsRequest,
)
from eventsourcing_grpc.protos.application_pb2_grpc import (
    ApplicationServicer,
    add_ApplicationServicer_to_server,
)


class ApplicationServer(ApplicationServicer):
    def __init__(self, application: Application, address: str) -> None:
        self.application = application
        self.transcoder = application.construct_transcoder()
        self.address = address

    def start(self) -> None:
        """
        Starts gRPC server.
        """
        self.executor = ThreadPoolExecutor(max_workers=10)
        self.server = grpc.server(self.executor)
        # logging.info(self.application_class)
        add_ApplicationServicer_to_server(self, self.server)
        self.server.add_insecure_port(self.address)
        self.server.start()

    def stop(self, grace: int = 1) -> None:
        print("Stopping application server")
        self.server.stop(grace=grace)

    def __del__(self) -> None:
        self.stop()

    def Ping(self, request: Empty, context: Context) -> Empty:
        return Empty()

    def CallApplicationMethod(
        self, request: MethodRequest, context: Context
    ) -> MethodReply:
        method_name = request.method_name
        args = self.transcoder.decode(request.args)
        kwargs = self.transcoder.decode(request.kwargs)
        method = getattr(self.application, method_name)
        response = method(*args, **kwargs)
        reply = MethodReply()
        reply.data = self.transcoder.encode(response)
        return reply

    def GetNotifications(
        self, request: NotificationsRequest, context: Context
    ) -> NotificationsReply:
        start = int(request.start)
        limit = int(request.limit)
        topics = request.topics
        notifications = self.application.notification_log.select(
            start=start, limit=limit, topics=topics
        )
        return NotificationsReply(
            notifications=[
                Notification(
                    id=str(n.id),
                    originator_id=n.originator_id.hex,
                    originator_version=str(n.originator_version),
                    topic=n.topic,
                    state=n.state,
                )
                for n in notifications
            ]
        )
