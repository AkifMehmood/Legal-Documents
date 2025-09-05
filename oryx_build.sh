#!/bin/sh
# Update package list
apt-get update

# Install PostgreSQL client libraries and headers
apt-get install -y libpq-dev python3-dev

# Optional: install PostgreSQL client tools
apt-get install -y postgresql-client
