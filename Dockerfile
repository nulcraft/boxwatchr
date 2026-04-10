FROM debian:trixie-slim

# Install system dependencies, the rspamd repository, rspamd itself,
# Python, pip, and supervisord all in one layer to keep the image small.
# We clean up the apt cache at the end to reduce image size.
ENV DEBIAN_FRONTEND=noninteractive

RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    wget \
    gnupg \
    lsb-release \
    python3 \
    python3-pip \
    redis-server \
    supervisor \
    tzdata \
    unbound \
    && mkdir -p /etc/apt/keyrings \
    && wget -O- https://rspamd.com/apt-stable/gpg.key | gpg --dearmor | tee /etc/apt/keyrings/rspamd.gpg > /dev/null \
    && echo "deb [signed-by=/etc/apt/keyrings/rspamd.gpg] https://rspamd.com/apt-stable/ $(lsb_release -cs) main" | tee /etc/apt/sources.list.d/rspamd.list \
    && apt-get update \
    && apt-get install -y --no-install-recommends rspamd \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

# Copy the supervisord config into the location supervisord expects.
COPY docker/supervisord.conf /etc/supervisor/conf.d/supervisord.conf

# Copy the unbound config.
COPY docker/unbound.conf /etc/unbound/unbound.conf

# Copy the startup script and make it executable.
COPY docker/docker-entrypoint.sh /docker-entrypoint.sh
RUN chmod +x /docker-entrypoint.sh

# Bake in rspamd local.d overrides: disable unused workers and modules.
# User-provided overrides in /app/config/rspamd/local.d/ are applied on top at runtime.
COPY docker/rspamd/ /etc/rspamd/local.d/

# Set the working directory for the application.
WORKDIR /app

# Copy requirements first so Docker can cache the pip install layer.
# This means pip only re-runs when requirements.txt actually changes.
COPY requirements.txt .

# Install Python dependencies.
RUN pip install --break-system-packages --no-cache-dir -r requirements.txt --root-user-action=ignore

# Copy the rest of the application code.
COPY boxwatchr/ ./boxwatchr/
COPY main.py .
COPY VERSION .

# The .env file and rules.yaml are NOT copied into the image.
# They are mounted as a volume at runtime so users can edit them
# without rebuilding the container.

# Expose the rspamd web UI port and the boxwatchr web dashboard port.
# WEB_PORT defaults to 8143 and can be overridden at runtime.
EXPOSE 11334
EXPOSE 8143

# Run the startup script which configures rspamd and launches supervisord.
CMD ["/docker-entrypoint.sh"]