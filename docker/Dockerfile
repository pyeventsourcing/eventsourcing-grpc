FROM python:3.10

WORKDIR /app

# Install eventsourcing_grpc.
COPY ../dist dist
RUN pip install dist/*.whl
RUN rm -r dist
ENV PYTHONUNBUFFERED = 1

# Run application server.
CMD ["eventsourcing_grpc_server"]
