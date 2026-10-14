# Haven MCP runtime image for Amazon Bedrock AgentCore (Container build).
#
# Built by CodeBuild (ARM64) from the repository root; the build context is
# filtered by .dockerignore so .venv, node_modules, tests and every .env* file
# stay out of the image. No AWS credentials are baked in: the runtime uses its
# AgentCore execution role.

FROM public.ecr.aws/docker/library/python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# Install Haven exactly as `pip install .` would locally: the pyproject owns
# the dependency pins and makes `haven`, `aws` and `simulator` importable
# without any sys.path manipulation.
COPY pyproject.toml ./
COPY src ./src
COPY aws ./aws
COPY simulator ./simulator
RUN pip install .

# AgentCore MCP contract: stateless Streamable HTTP on 0.0.0.0:8000 at /mcp.
EXPOSE 8000
CMD ["python", "aws/agentcore/runtime.py"]
