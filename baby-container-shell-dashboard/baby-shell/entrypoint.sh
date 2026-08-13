#!/usr/bin/env bash
set -euo pipefail

if ! touch /tmp/.baby-shell-write-test; then
    echo "The baby-container scratch mount at /tmp is not writable." >&2
    exit 1
fi
rm -f /tmp/.baby-shell-write-test

echo "baby-container OpenSSH test service starting on port 2222"
echo "normal user: tester"
echo "root user: root"
echo "password for both users: atakit-test"

exec /usr/sbin/sshd -D -e -f /etc/ssh/sshd_config
