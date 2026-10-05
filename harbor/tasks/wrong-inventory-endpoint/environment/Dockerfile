FROM python:3.13-slim

# The sandbox contains ONLY the demo platform — no agent code, no model keys.
# All agent ops enter via `python -m demo_platform.ops_cli.cli` (sre-ops).
WORKDIR /opt/sentinelsre

COPY payload/demo_platform ./demo_platform
COPY payload/configs ./configs
COPY payload/knowledge ./knowledge

RUN pip install --no-cache-dir -r demo_platform/requirements.txt

ENV SRE_PLATFORM_ROOT=/opt/sentinelsre \
    SRE_RUNTIME_DIR=/opt/sentinelsre/runtime \
    PYTHONPATH=/opt/sentinelsre

# Bake the fault + hidden verifier ground truth into the image. The platform
# is not running at build time, so inject() writes config + ground truth only.
RUN python -m demo_platform.scenarios.wrong_inventory_endpoint inject && \
    mkdir -p /opt/sentinelsre/runtime /opt/sentinelsre/reports

# Services are started by the agent (sre-ops platform up) or solve.sh.
