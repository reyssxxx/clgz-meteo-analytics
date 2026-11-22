#!/usr/bin/env bash
# Деплой в Yandex Cloud: образ → Container Registry → ВМ с Container Optimized Image.
# Повторный запуск только обновляет образ на существующей ВМ.
#
# Нужно: yc (настроен через `yc init`), запущенный Docker, jq.
# Создаёт в текущем каталоге yc: реестр, сервисный аккаунт, сеть с подсетью, ВМ (2 vCPU 50%, 4 ГБ).
set -euo pipefail
cd "$(dirname "$0")/.."
export PATH="$HOME/yandex-cloud/bin:$PATH"

ZONE="${ZONE:-ru-central1-a}"
NAME="${NAME:-meteo}"
FOLDER_ID="$(yc config get folder-id)"
REG="$NAME-registry" SA="$NAME-sa" NET="$NAME-net" SUBNET="$NAME-subnet-a" VM="$NAME-app"

exists() { yc "$@" >/dev/null 2>&1; }
step() { printf '\n==> %s\n' "$*"; }

step "Container Registry"
exists container registry get --name "$REG" || yc container registry create --name "$REG" >/dev/null
REG_ID="$(yc container registry get --name "$REG" --format json | jq -r .id)"
yc container registry configure-docker >/dev/null

step "Сборка и публикация образа (linux/amd64)"
IMAGE="cr.yandex/$REG_ID/$NAME:$(date +%Y%m%d-%H%M%S)"
docker build --platform linux/amd64 --provenance=false --sbom=false -t "$IMAGE" .
docker push "$IMAGE"

step "Сервисный аккаунт для скачивания образа"
if ! exists iam service-account get --name "$SA"; then
  yc iam service-account create --name "$SA" >/dev/null
  SA_ID="$(yc iam service-account get --name "$SA" --format json | jq -r .id)"
  yc resource-manager folder add-access-binding "$FOLDER_ID" \
    --role container-registry.images.puller --subject "serviceAccount:$SA_ID" >/dev/null
fi

step "Сеть"
exists vpc network get --name "$NET" || yc vpc network create --name "$NET" >/dev/null
exists vpc subnet get --name "$SUBNET" || yc vpc subnet create --name "$SUBNET" --network-name "$NET" \
  --zone "$ZONE" --range 10.10.0.0/24 >/dev/null

step "Виртуальная машина"
# Вход сотрудника: логин и пароль берутся из окружения, в репозиторий не попадают.
: "${CLGZ_EMPLOYEE_USER:?задайте CLGZ_EMPLOYEE_USER}" "${CLGZ_EMPLOYEE_PASSWORD:?задайте CLGZ_EMPLOYEE_PASSWORD}"
ENV="PORT=8080,CLGZ_EMPLOYEE_USER=$CLGZ_EMPLOYEE_USER,CLGZ_EMPLOYEE_PASSWORD=$CLGZ_EMPLOYEE_PASSWORD"
if exists compute instance get --name "$VM"; then
  yc compute instance update-container --name "$VM" --container-image "$IMAGE" --container-env "$ENV" >/dev/null
else
  yc compute instance create-with-container --name "$VM" --zone "$ZONE" \
    --platform standard-v3 --cores 2 --core-fraction 50 --memory 4GB \
    --create-boot-disk size=20 \
    --network-interface "subnet-name=$SUBNET,nat-ip-version=ipv4" \
    --service-account-name "$SA" \
    --container-name "$NAME" --container-image "$IMAGE" \
    --container-env "$ENV" --container-restart-policy Always >/dev/null
fi

IP="$(yc compute instance get --name "$VM" --format json | jq -r '.network_interfaces[0].primary_v4_address.one_to_one_nat.address')"
step "Готово: http://$IP:8080  (после создания ВМ контейнер стартует 1–2 минуты)"
