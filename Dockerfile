FROM python:3.12-alpine AS base
FROM base AS builder
RUN apk update && apk add --no-cache git
COPY requirements.txt /requirements.txt
RUN pip install --user -r /requirements.txt

FROM base
# copy only the dependencies installation from the 1st stage image
COPY --from=builder /root/.local /root/.local
RUN mkdir /app
COPY *.py /app
WORKDIR /app

# update PATH environment variable
ENV PATH=/root/.local/bin:$PATH

CMD ["python3", "bot.py"]