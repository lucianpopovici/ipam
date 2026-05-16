# Use Ubuntu as the base image
FROM ubuntu:24.04

# Avoid prompts from apt
ENV DEBIAN_FRONTEND=noninteractive
ENV PYTHONUNBUFFERED=1

# Install system dependencies (incl. WeasyPrint native libs)
RUN apt-get update && apt-get install -y \
    python3 \
    python3-pip \
    python3-venv \
    curl \
    git \
    libcairo2 \
    libpango-1.0-0 \
    libpangoft2-1.0-0 \
    libgdk-pixbuf-2.0-0 \
    libffi-dev \
    shared-mime-info \
    && rm -rf /var/lib/apt/lists/*

# Set working directory
WORKDIR /app

# Copy requirements files first for better caching
COPY requirements.txt requirements-test.txt ./

# Create a virtual environment and install dependencies
RUN python3 -m venv /venv
ENV PATH="/venv/bin:$PATH"
RUN pip install --no-cache-dir -r requirements.txt -r requirements-test.txt

# Install Playwright browsers and their system dependencies
RUN playwright install chromium --with-deps

# Copy the rest of the application code
COPY . .

# Default command to run tests
CMD ["pytest"]
