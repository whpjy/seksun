FROM ubuntu:24.04 AS builder

ENV DEBIAN_FRONTEND=noninteractive

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        build-essential \
        cmake \
        ninja-build \
        libocct-data-exchange-dev \
        libocct-modeling-algorithms-dev \
        libocct-ocaf-dev \
        libocct-visualization-dev \
    && rm -rf /var/lib/apt/lists/*

RUN apt-get update \
    && apt-get install -y --no-install-recommends libtbb-dev \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /src
COPY CMakeLists.txt ./
COPY src ./src

RUN cmake -S . -B build -G Ninja -DCMAKE_BUILD_TYPE=Release \
    && cmake --build build --parallel \
    && cmake --install build --prefix /opt/occt-analyzer

FROM ubuntu:24.04 AS runtime

ENV DEBIAN_FRONTEND=noninteractive

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        libocct-data-exchange-7.6t64 \
        libocct-modeling-algorithms-7.6t64 \
    && rm -rf /var/lib/apt/lists/*

COPY --from=builder /opt/occt-analyzer/bin/occt-analyzer /usr/local/bin/occt-analyzer

RUN useradd --system --uid 10001 --create-home analyzer \
    && mkdir -p /data/input /data/output \
    && chown -R analyzer:analyzer /data/output

USER analyzer
WORKDIR /data

ENTRYPOINT ["/usr/local/bin/occt-analyzer"]
CMD ["/data/input/test.stp", "/data/output/result.json"]
