from datetime import datetime
from time import sleep

import grpc
from grpc._channel import _InactiveRpcError

from eventsourcing_grpc.application_pb2 import Empty
from eventsourcing_grpc.application_pb2_grpc import ApplicationStub


class ApplicationClient(object):
    def __init__(self, address):
        self.address = address
        self.channel = None
        # self.json_encoder = ObjectJSONEncoder()
        # self.json_decoder = ObjectJSONDecoder()

    def connect(self, timeout=5):
        """
        Connect to client to server at given address.

        Calls ping() until it gets a response, or timeout is reached.
        """
        self.close()
        self.channel = grpc.insecure_channel(self.address)
        self.stub = ApplicationStub(self.channel)

        timer_started = datetime.now()
        while True:
            # Ping until get a response.
            try:
                print(f"Connecting to application server: {self.address}")
                request = Empty()
                reply: Empty = self.stub.Ping(request)
                print("Reply:", type(reply))
            except _InactiveRpcError:
                print(f"Inactive RPC error from: {self.address}")

                if timeout is not None:
                    timer_duration = (datetime.now() - timer_started).total_seconds()
                    if timer_duration > timeout:
                        raise Exception(
                            "Timed out trying to connect to %s" % self.address
                        )
                    sleep(1)
                else:
                    print(f"Connecting to application server: {self.address}")
                    sleep(1)
                    continue
            else:
                break

    # def __enter__(self):
    #     return self
    #
    # def __exit__(self, exc_type, exc_val, exc_tb):
    #     self.close()
    #
    def close(self):
        """
        Closes the client's GPRC channel.
        """
        if self.channel is not None:
            self.channel.close()

    def ping(self, timeout=5):
        """
        Sends a Ping request to the server.
        """
        request = Empty()
        response = self.stub.Ping(request, timeout=timeout)

    # def follow(self, upstream_name, upstream_address):
    #     request = FollowRequest(
    #         upstream_name=upstream_name, upstream_address=upstream_address
    #     )
    #     response = self.stub.Follow(request, timeout=5,)

    # def prompt(self, upstream_name):
    #     """
    #     Prompts downstream server with upstream name, so that downstream
    #     process and promptly pull new notifications from upstream process.
    #     """
    #     request = PromptRequest(upstream_name=upstream_name)
    #     response = self.stub.Prompt(request, timeout=5)
    #
    # def get_notifications(self, section_id):
    #     """
    #     Gets a section of event notifications from server.
    #     """
    #     request = NotificationsRequest(section_id=section_id)
    #     notifications_reply = self.stub.GetNotifications(request, timeout=5)
    #     assert isinstance(notifications_reply, NotificationsReply)
    #     return notifications_reply.section
    #
    # def lead(self, application_name, address):
    #     """
    #     Requests a process to connect and then send prompts to given address.
    #     """
    #     request = LeadRequest(
    #         downstream_name=application_name, downstream_address=address
    #     )
    #     response = self.stub.Lead(request, timeout=5)
    #
    # def call_application(self, method_name, *args, **kwargs):
    #     """
    #     Calls named method on server's application with given args.
    #     """
    #     request = CallRequest(
    #         method_name=method_name,
    #         args=self.json_encoder.encode(args),
    #         kwargs=self.json_encoder.encode(kwargs),
    #     )
    #     response = self.stub.CallApplicationMethod(request, timeout=5)
    #     return self.json_decoder.decode(response.data)
