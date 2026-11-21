FROM python:3.13-alpine

WORKDIR /app
COPY server.py index.html ./

# Settings saved from the page go to /config/config.json: mount a folder there to keep them.
ENV HOST=0.0.0.0 \
    PORT=8080 \
    CONFIG_DIR=/config
RUN mkdir /config
VOLUME /config
EXPOSE 8080

CMD ["python", "-u", "server.py"]
