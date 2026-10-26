ARG BASE_IMAGE=ghcr.io/mullvad/mullvadvpn-app-build-android@sha256:a4085bc5e3efd16a48bd252b13977562f0bfdf2d8bb2d18580dfc97f9f6deffc
FROM ${BASE_IMAGE}
ARG BASE_IMAGE
LABEL org.opencontainers.image.source="https://github.com/zerox80/mullvad-android-builder" \
      org.opencontainers.image.description="Commit-based Mullvad Android release builder"
ENV BUILDER_BASE_IMAGE=${BASE_IMAGE} \
    CARGO_TARGET_DIR=/cargo-target/target \
    CARGO_BUILD_JOBS=4 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1
COPY container/builder.py /opt/mullvad-builder/builder.py
WORKDIR /build
ENTRYPOINT ["python3", "/opt/mullvad-builder/builder.py"]
CMD ["--help"]
