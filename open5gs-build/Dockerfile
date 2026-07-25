FROM ubuntu:22.04

ENV DEBIAN_FRONTEND=noninteractive

RUN apt-get update && apt-get install -y \
    git cmake ninja-build build-essential flex bison \
    libsctp-dev libgnutls28-dev libgcrypt-dev \
    libssl-dev libidn11-dev libmongoc-dev libbson-dev \
    libnghttp2-dev libyaml-dev libmicrohttpd-dev \
    libcurl4-gnutls-dev libtins-dev libtalloc-dev \
    meson python3-pip iproute2 iptables curl wget && \
    rm -rf /var/lib/apt/lists/*

RUN git clone --branch v2.7.7 --depth 1 \
        https://github.com/open5gs/open5gs.git /open5gs

WORKDIR /open5gs

RUN meson build --prefix=/open5gs/install && \
    ninja -C build && \
    ninja -C build install

COPY open5gs-entrypoint.sh /open5gs-entrypoint.sh
RUN chmod +x /open5gs-entrypoint.sh

WORKDIR /open5gs
