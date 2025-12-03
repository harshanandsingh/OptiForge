FROM ubuntu:22.04
ARG DEBIAN_FRONTEND=noninteractive

# 1. Install standard tools + LLVM 19 (to match host)
RUN apt-get update && \
    apt-get install -y software-properties-common wget && \
    wget -O - https://apt.llvm.org/llvm-snapshot.gpg.key | apt-key add - && \
    add-apt-repository "deb http://apt.llvm.org/jammy/ llvm-toolchain-jammy-19 main" && \
    apt-get update && apt-get install -y \
    clang-19 \
    clang++-19 \
    lldb-19 \
    lld-19 \
    clang \
    gcc \
    g++ \
    llvm-19 \
    llvm-19-dev \
    build-essential \
    && apt-get clean


    RUN ln -s /usr/bin/clang-19 /usr/bin/clang-explicit || true && \
    ln -s /usr/bin/clang++-19 /usr/bin/clang++-explicit || true && \
    ln -s /usr/bin/llvm-cov-19 /usr/bin/llvm-cov || true && \
    ln -s /usr/bin/llvm-profdata-19 /usr/bin/llvm-profdata || true && \
    ln -s /usr/bin/opt-19 /usr/bin/opt || true

RUN mkdir -p /opt/llvm-passes
COPY LLVMProject/OpcodeCounter/build/libOpcodeCounter.so /opt/llvm-passes/

WORKDIR /app