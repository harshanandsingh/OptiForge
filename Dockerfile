FROM ubuntu:22.04
ARG DEBIAN_FRONTEND=noninteractive

# 1. Install standard tools + explicit LLVM version (Ubuntu 22.04 uses 14 by default)
RUN apt-get update && apt-get install -y \
    clang-14 \
    lldb-14 \
    lld-14 \
    clang \
    gcc \
    g++ \
    llvm-14 \
    build-essential \
    && apt-get clean

# 2. Create symlinks so 'llvm-cov' works without needing '-14'
# We check if they exist first to avoid errors if base package already did it
RUN ln -s /usr/bin/clang-14 /usr/bin/clang-explicit || true && \
    ln -s /usr/bin/clang++-14 /usr/bin/clang++-explicit || true && \
    ln -s /usr/bin/llvm-cov-14 /usr/bin/llvm-cov || true && \
    ln -s /usr/bin/llvm-profdata-14 /usr/bin/llvm-profdata || true

WORKDIR /app