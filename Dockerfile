FROM python:3.12.7-slim

ENV RUNNER="runner"
ENV WORKDIR="/workdir/"

RUN mkdir -p "${WORKDIR}"

WORKDIR "${WORKDIR}"
COPY requirements.txt "${WORKDIR}"
RUN ( getent passwd "${RUNNER}" || adduser --disabled-password "${RUNNER}" ) \
&& pip install --no-cache-dir -r "${WORKDIR}requirements.txt"

HEALTHCHECK NONE

USER "${RUNNER}"
ENTRYPOINT ["python", "./sync.py"]
COPY sync.py "${WORKDIR}"
