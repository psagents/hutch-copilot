# hutch-mcp: Python and the MCP SDK; what the tools wrap (/cds envs, caget, DAQ release) is bound from the node.
# Rocky 9, not Debian: the /cds caget and pcds envs are rhel9 builds (Debian lacks their libncurses).
FROM rockylinux/rockylinux:9

# which and python3-ldap: the hutch's own startup scripts (EpicsHostArch, netconfig) call them.
RUN dnf -y install python3.12 python3.12-pip which python3-ldap && dnf clean all

COPY . /opt/hutch-copilot
# Editable, so the server finds bin/hutch-* beside src/ in the checkout rather than in site-packages.
RUN python3.12 -m pip install --no-cache-dir -e '/opt/hutch-copilot[server]'

ENV MCP_TRANSPORT=stdio
ENTRYPOINT ["python3.12", "-m", "hutch_copilot.server"]
