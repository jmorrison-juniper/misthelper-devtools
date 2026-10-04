# A small image for the self-test of the reusable container workflow.
FROM docker.io/library/busybox:1.38

ARG VERSION=unknown
LABEL org.opencontainers.image.version="${VERSION}"

# A RUN step proves that the emulated platforms can run a command.
RUN echo "${VERSION}" > /version.txt

CMD ["cat", "/version.txt"]
