# hutch-mcp: Python and the MCP SDK; what the tools wrap (/cds envs, caget, DAQ release) is bound from the node.
# Rocky 9, not Debian: the /cds caget and pcds envs are rhel9 builds (Debian lacks their libncurses).
FROM rockylinux/rockylinux:9

RUN dnf -y install python3.12 python3.12-pip && dnf clean all

COPY server/requirements.txt /tmp/requirements.txt
RUN python3.12 -m pip install --no-cache-dir -r /tmp/requirements.txt

COPY . /opt/hutch-copilot

ENV MCP_TRANSPORT=stdio
ENTRYPOINT ["python3.12", "/opt/hutch-copilot/server/hutch_mcp.py"]
