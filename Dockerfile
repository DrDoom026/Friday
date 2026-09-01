FROM python:3.12-slim

# PART 6 system tools shell out to a small, fixed set of binaries. Each one is
# here for one named tool, not as a general-purpose toolbox:
#   git             git.status, git.branches, git.clone
#   iproute2        net.interfaces, net.routes (`ip -json`)
#   iputils-ping    net.ping's ICMP mode; its TCP mode needs no binary
#   ca-certificates cloning over https
# There is deliberately no docker CLI: the docker.* tools speak the engine's
# HTTP API over its unix socket, so nothing here has to match the host's libc.
RUN apt-get update \
    && apt-get install --no-install-recommends -y \
        ca-certificates \
        git \
        iproute2 \
        iputils-ping \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
