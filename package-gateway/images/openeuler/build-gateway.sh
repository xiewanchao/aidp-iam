#!/bin/bash
# Copyright Huawei Technologies Co., Ltd. 2026-2026. All rights reserved.
set -ex

declare CURRENT_PATH=$(cd "`dirname $0`"; pwd)
PACKAGE_GATEWAY_ROOT=$(realpath "${CURRENT_PATH}/../..")
PROCESS_ROOT=$(realpath "${PACKAGE_GATEWAY_ROOT}/..")

ARCH="${ARCH:-arm64}"
BASE_IMAGE_NAME="${BASE_IMAGE_NAME:-euleros:latest}"

if [ "${ARCH}" != "arm64" ] && [ "${ARCH}" != "amd64" ]; then
    echo "ARCH must be arm64 or amd64"
    exit 1
fi

# Default EulerOS base image URL follows the existing frontend build script
# and is arm64/aarch64. For amd64 builds, pass EULEROS_DOCKER_DOWNLOAD_URL
# from the build environment.
if [ -z "${EULEROS_DOCKER_DOWNLOAD_URL:-}" ]; then
    if [ "${ARCH}" = "arm64" ]; then
        EULEROS_DOCKER_DOWNLOAD_URL="https://cmc-nkg-artifactory.cmc.tools.huawei.com/artifactory/cmc-software-release/EulerOS%20Server/EulerOSServerV200R012C00ARM/2026.04.09.211233/Software/aarch64/DockerStack/EulerOS_Server_V200R012C00SPC531B030-docker.aarch64.tar.xz"
    else
        echo "EULEROS_DOCKER_DOWNLOAD_URL is required for amd64 builds"
        exit 1
    fi
fi

KUBERNETES_PACKAGE_BASE_URL="${KUBERNETES_PACKAGE_BASE_URL:-https://cmc.cloudartifact.szv.dragon.tools.huawei.com/artifactory/opensource_general/kubernetes/v1.34.1/package}"
ENVOY_GATEWAY_PACKAGE_BASE_URL="${ENVOY_GATEWAY_PACKAGE_BASE_URL:-https://cmc.cloudartifact.szv.dragon.tools.huawei.com/artifactory/opensource_general/Envoy-Gateway/v1.7.2/package}"
ENVOY_PACKAGE_BASE_URL="${ENVOY_PACKAGE_BASE_URL:-https://cmc.cloudartifact.szv.dragon.tools.huawei.com/artifactory/opensource_general/envoy/v1.36.5/package}"
PYTHON_PACKAGE_BASE_URL="${PYTHON_PACKAGE_BASE_URL:-https://cmc.cloudartifact.szv.dragon.tools.huawei.com/artifactory/opensource_general/Python/3.11.4/package}"

KUBECTL_DOWNLOAD_URL="${KUBECTL_DOWNLOAD_URL:-${KUBERNETES_PACKAGE_BASE_URL}/kubernetes-client-linux-${ARCH}.tar.gz}"
ENVOY_GATEWAY_DOWNLOAD_URL="${ENVOY_GATEWAY_DOWNLOAD_URL:-${ENVOY_GATEWAY_PACKAGE_BASE_URL}/envoy-gateway_v1.7.2_linux_${ARCH}.tar.gz}"
PYTHON_VERSION="${PYTHON_VERSION:-3.11.4}"
PYTHON_SOURCE_NAME="${PYTHON_SOURCE_NAME:-Python-${PYTHON_VERSION}.tar.xz}"
PYTHON_SOURCE_DOWNLOAD_URL="${PYTHON_SOURCE_DOWNLOAD_URL:-${PYTHON_PACKAGE_BASE_URL}/${PYTHON_SOURCE_NAME}}"

if [ "${ARCH}" = "arm64" ]; then
    ENVOY_BINARY_NAME="envoy-1.36.5-linux-aarch_64"
else
    ENVOY_BINARY_NAME="envoy-1.36.5-linux-x86_64"
fi
ENVOY_DOWNLOAD_URL="${ENVOY_DOWNLOAD_URL:-${ENVOY_PACKAGE_BASE_URL}/${ENVOY_BINARY_NAME}}"

KUBECTL_IMAGE_NAME="${KUBECTL_IMAGE_NAME:-docker.io/alpine/kubectl}"
KUBECTL_IMAGE_TAG="${KUBECTL_IMAGE_TAG:-1.34.1}"

ENVOY_GATEWAY_IMAGE_NAME="${ENVOY_GATEWAY_IMAGE_NAME:-docker.io/envoyproxy/gateway}"
ENVOY_GATEWAY_IMAGE_TAG="${ENVOY_GATEWAY_IMAGE_TAG:-v1.7.2}"

ENVOY_IMAGE_NAME="${ENVOY_IMAGE_NAME:-docker.io/envoyproxy/envoy}"
ENVOY_IMAGE_TAG="${ENVOY_IMAGE_TAG:-v1.36.5}"

GATEWAY_MANAGER_IMAGE_NAME="${GATEWAY_MANAGER_IMAGE_NAME:-gateway-manager}"
GATEWAY_MANAGER_IMAGE_TAG="${GATEWAY_MANAGER_IMAGE_TAG:-v1}"

PIP_INDEX_URL="${PIP_INDEX_URL:-https://mirrors.aliyun.com/pypi/simple/}"
PIP_TRUSTED_HOST="${PIP_TRUSTED_HOST:-mirrors.aliyun.com}"

BUILD_ROOT="${CURRENT_PATH}/.build/${ARCH}"
IMAGE_OUTPUT_DIR="${IMAGE_OUTPUT_DIR:-${PACKAGE_GATEWAY_ROOT}/images/${ARCH}}"
CHART_OUTPUT_DIR="${CHART_OUTPUT_DIR:-${PROCESS_ROOT}/temp_chart_package}"

mkdir -p "${BUILD_ROOT}"
mkdir -p "${IMAGE_OUTPUT_DIR}"
mkdir -p "${CHART_OUTPUT_DIR}"

function download_file() {
    local url="$1"
    local output="$2"
    wget -q --no-check-certificate -O "${output}" "${url}" || {
        echo "download failed: ${url}"
        exit 1
    }
}

function reset_context() {
    local context_dir="$1"
    rm -rf "${context_dir}"
    mkdir -p "${context_dir}"
}

function copy_found_binary() {
    local search_dir="$1"
    local binary_name="$2"
    local output="$3"
    local found
    found=$(find "${search_dir}" -type f -name "${binary_name}" | head -n 1)
    if [ -z "${found}" ]; then
        echo "binary ${binary_name} not found under ${search_dir}"
        exit 1
    fi
    cp "${found}" "${output}"
    chmod 0755 "${output}"
}

function image_tar_name() {
    local image_name="$1"
    local image_tag="$2"
    local normalized_name="${image_name}"
    if [[ "${normalized_name}" != */* ]]; then
        normalized_name="docker.io/library/${normalized_name}"
    fi
    echo "${normalized_name}_${image_tag}.tar" | sed 's#[/:]#_#g'
}

function prepare_docker_base_image() {
    rm -f "${BUILD_ROOT}/euleros.tar.xz" "${BUILD_ROOT}/euleros.tar"
    download_file "${EULEROS_DOCKER_DOWNLOAD_URL}" "${BUILD_ROOT}/euleros.tar.xz"
    xz -dc "${BUILD_ROOT}/euleros.tar.xz" > "${BUILD_ROOT}/euleros.tar"
    docker import "${BUILD_ROOT}/euleros.tar" "${BASE_IMAGE_NAME}"
    rm -f "${BUILD_ROOT}/euleros.tar.xz" "${BUILD_ROOT}/euleros.tar"
}

function prepare_kubectl_context() {
    local context_dir="${BUILD_ROOT}/kubectl-context"
    local extract_dir="${BUILD_ROOT}/kubectl-extract"
    reset_context "${context_dir}"
    rm -rf "${extract_dir}"
    mkdir -p "${extract_dir}"

    download_file "${KUBECTL_DOWNLOAD_URL}" "${BUILD_ROOT}/kubernetes-client.tar.gz"
    tar -xzf "${BUILD_ROOT}/kubernetes-client.tar.gz" -C "${extract_dir}"
    copy_found_binary "${extract_dir}" "kubectl" "${context_dir}/kubectl"
    rm -rf "${extract_dir}" "${BUILD_ROOT}/kubernetes-client.tar.gz"
}

function prepare_envoy_gateway_context() {
    local context_dir="${BUILD_ROOT}/envoy-gateway-context"
    local extract_dir="${BUILD_ROOT}/envoy-gateway-extract"
    reset_context "${context_dir}"
    rm -rf "${extract_dir}"
    mkdir -p "${extract_dir}"

    download_file "${ENVOY_GATEWAY_DOWNLOAD_URL}" "${BUILD_ROOT}/envoy-gateway.tar.gz"
    tar -xzf "${BUILD_ROOT}/envoy-gateway.tar.gz" -C "${extract_dir}"
    copy_found_binary "${extract_dir}" "envoy-gateway" "${context_dir}/envoy-gateway"
    rm -rf "${extract_dir}" "${BUILD_ROOT}/envoy-gateway.tar.gz"
}

function prepare_envoy_context() {
    local context_dir="${BUILD_ROOT}/envoy-context"
    reset_context "${context_dir}"

    download_file "${ENVOY_DOWNLOAD_URL}" "${context_dir}/envoy"
    chmod 0755 "${context_dir}/envoy"
}

function prepare_gateway_manager_context() {
    local context_dir="${BUILD_ROOT}/gateway-manager-context"
    reset_context "${context_dir}"

    download_file "${PYTHON_SOURCE_DOWNLOAD_URL}" "${context_dir}/${PYTHON_SOURCE_NAME}"
    cp "${PACKAGE_GATEWAY_ROOT}/images/gateway-manager/requirements.txt" "${context_dir}/requirements.txt"
    cp -R "${PACKAGE_GATEWAY_ROOT}/images/gateway-manager/app" "${context_dir}/app"
}

function build_and_save_image() {
    local dockerfile="$1"
    local context_dir="$2"
    local image_name="$3"
    local image_tag="$4"
    shift 4

    docker build -f "${dockerfile}" \
        --build-arg "BASE_IMAGE_NAME=${BASE_IMAGE_NAME}" \
        "$@" \
        -t "${image_name}:${image_tag}" \
        "${context_dir}"
    docker save -o "${IMAGE_OUTPUT_DIR}/$(image_tar_name "${image_name}" "${image_tag}")" "${image_name}:${image_tag}"
}

function package_gateway_chart() {
    helm package "${PACKAGE_GATEWAY_ROOT}/charts/aidp-gateway" -d "${CHART_OUTPUT_DIR}"
}

prepare_docker_base_image

prepare_kubectl_context
build_and_save_image \
    "${CURRENT_PATH}/Dockerfile.kubectl" \
    "${BUILD_ROOT}/kubectl-context" \
    "${KUBECTL_IMAGE_NAME}" \
    "${KUBECTL_IMAGE_TAG}"

prepare_envoy_gateway_context
build_and_save_image \
    "${CURRENT_PATH}/Dockerfile.envoy-gateway" \
    "${BUILD_ROOT}/envoy-gateway-context" \
    "${ENVOY_GATEWAY_IMAGE_NAME}" \
    "${ENVOY_GATEWAY_IMAGE_TAG}"

prepare_envoy_context
build_and_save_image \
    "${CURRENT_PATH}/Dockerfile.envoy" \
    "${BUILD_ROOT}/envoy-context" \
    "${ENVOY_IMAGE_NAME}" \
    "${ENVOY_IMAGE_TAG}"

prepare_gateway_manager_context
build_and_save_image \
    "${CURRENT_PATH}/Dockerfile.gateway-manager" \
    "${BUILD_ROOT}/gateway-manager-context" \
    "${GATEWAY_MANAGER_IMAGE_NAME}" \
    "${GATEWAY_MANAGER_IMAGE_TAG}" \
    --build-arg "PYTHON_VERSION=${PYTHON_VERSION}" \
    --build-arg "PYTHON_SOURCE_NAME=${PYTHON_SOURCE_NAME}" \
    --build-arg "PIP_INDEX_URL=${PIP_INDEX_URL}" \
    --build-arg "PIP_TRUSTED_HOST=${PIP_TRUSTED_HOST}"

package_gateway_chart

echo "Gateway openEuler images are saved under ${IMAGE_OUTPUT_DIR}"
echo "Gateway chart is saved under ${CHART_OUTPUT_DIR}"
