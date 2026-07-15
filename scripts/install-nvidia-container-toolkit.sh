#!/usr/bin/env bash
set -euo pipefail

if [[ ${EUID} -ne 0 ]]; then
  echo "Run this script with sudo." >&2
  exit 1
fi

keyring=/usr/share/keyrings/nvidia-container-toolkit-keyring.gpg
repository=/etc/apt/sources.list.d/nvidia-container-toolkit.list

curl -fsSL https://nvidia.github.io/libnvidia-container/gpgkey \
  | gpg --dearmor --yes -o "$keyring"
curl -fsSL https://nvidia.github.io/libnvidia-container/stable/deb/nvidia-container-toolkit.list \
  | sed "s#deb https://#deb [signed-by=$keyring] https://#g" \
  > "$repository"

apt-get update
apt-get install -y nvidia-container-toolkit
nvidia-ctk runtime configure --runtime=docker
systemctl restart docker

echo "NVIDIA Container Toolkit installed and Docker runtime configured."
