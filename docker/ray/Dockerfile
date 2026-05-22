# syntax=docker/dockerfile:1
#
# Ray container image equivalent to rayproject/ray, but with an explicit
# Python *patch* version. Python is installed from conda-forge via Miniforge —
# the same mechanism the official Ray base-deps image uses — which lets us pin
# MAJOR.MINOR.PATCH instead of only the MAJOR.MINOR exposed by the official
# py3xx tags.

ARG BASE_IMAGE="ubuntu:22.04"
FROM ${BASE_IMAGE}

# Full Python version, e.g. 3.12.7. Must have a conda-forge build.
ARG PYTHON_VERSION=3.12.7
# Ray release to install from PyPI, e.g. 2.40.0. Required.
ARG RAY_VERSION
# Miniforge installer version — pinned to match the official Ray base image.
ARG MINIFORGE_VERSION=24.11.3-0
# Match the uid/gid the official rayproject/ray image uses.
ARG RAY_UID=1000
ARG RAY_GID=100

ENV DEBIAN_FRONTEND=noninteractive
ENV TZ=UTC

# System packages mirror the official Ray base-deps image.
RUN apt-get update -y \
    && apt-get install -y --no-install-recommends \
        sudo tzdata git wget rsync tmux screen netbase \
        openssh-client gnupg ca-certificates \
    && useradd -ms /bin/bash -d /home/ray ray --uid ${RAY_UID} --gid ${RAY_GID} \
    && usermod -aG sudo ray \
    && echo 'ray ALL=NOPASSWD: ALL' >> /etc/sudoers \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

USER ${RAY_UID}
ENV HOME=/home/ray
WORKDIR /home/ray
ENV PATH=/home/ray/anaconda3/bin:$PATH

# Install Miniforge, then an exact Python patch version from conda-forge.
RUN ARCH=$(uname -m) \
    && wget --quiet \
        "https://github.com/conda-forge/miniforge/releases/download/${MINIFORGE_VERSION}/Miniforge3-${MINIFORGE_VERSION}-Linux-${ARCH}.sh" \
        -O /tmp/miniforge.sh \
    && /bin/bash /tmp/miniforge.sh -b -u -p /home/ray/anaconda3 \
    && rm /tmp/miniforge.sh \
    && conda install -y -c conda-forge \
        "python=${PYTHON_VERSION}" \
        libgcc-ng \
        "libffi=3.4.6" \
    && conda clean -y --all

# Install Ray from PyPI. ray[all] mirrors the contents of rayproject/ray.
RUN test -n "${RAY_VERSION}" || { echo "ERROR: RAY_VERSION build-arg is required"; exit 1; } \
    && pip --no-cache-dir install "ray[all]==${RAY_VERSION}" \
    && pip freeze > /home/ray/pip-freeze.txt \
    && python -c "import ray, platform; print('ray', ray.__version__, '/ python', platform.python_version())"

WORKDIR /home/ray
