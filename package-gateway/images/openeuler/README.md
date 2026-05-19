# openEuler / EulerOS Gateway image build

This directory builds the Gateway runtime images from a company-provided
EulerOS base image and approved package artifacts.

The build script follows the frontend build pattern:

1. Download the EulerOS Docker rootfs archive on the build machine.
2. Import it as `euleros:latest`.
3. Download approved binary/source artifacts on the build machine.
4. Prepare the executable files in a temporary build context.
5. Let Dockerfiles only `COPY` prepared artifacts into the EulerOS image.
6. Save image tar files for offline deployment.

## Images

| Image | Input artifact |
|---|---|
| `docker.io/alpine/kubectl:1.34.1` | `kubernetes-client-linux-{arch}.tar.gz` |
| `docker.io/envoyproxy/gateway:v1.7.2` | `envoy-gateway_v1.7.2_linux_{arch}.tar.gz` |
| `docker.io/envoyproxy/envoy:v1.36.5` | `envoy-1.36.5-linux-aarch_64` or `envoy-1.36.5-linux-x86_64` |
| `gateway-cert-manager:v1` | `Python-3.11.4.tar.xz`, local `gateway-cert-manager` source, and Python dependencies |

## Usage

Build arm64 images:

```bash
cd package-gateway/images/openeuler
bash build-gateway.sh
```

Build amd64 images by providing an amd64 EulerOS Docker archive:

```bash
ARCH=amd64 \
EULEROS_DOCKER_DOWNLOAD_URL=<amd64-euleros-docker-tar-xz-url> \
bash build-gateway.sh
```

Override the Python package index for `gateway-cert-manager`:

```bash
PIP_INDEX_URL=https://<internal-pypi>/simple/ \
PIP_TRUSTED_HOST=<internal-pypi-host> \
bash build-gateway.sh
```

Override the Python source package if needed:

```bash
PYTHON_SOURCE_DOWNLOAD_URL=https://cmc.cloudartifact.szv.dragon.tools.huawei.com/artifactory/opensource_general/Python/3.11.4/package/Python-3.11.4.tar.xz \
bash build-gateway.sh
```

`gateway-cert-manager` builds Python 3.11.4 from source inside the EulerOS
image so the runtime Python ABI matches the final base image. The build machine
only downloads the approved `Python-3.11.4.tar.xz` artifact into the Docker
build context.

By default, image tar files are saved under:

```text
package-gateway/images/{arm64|amd64}/
```

By default, the Helm chart package is saved under:

```text
temp_chart_package/
```

Override output directories when the script is called by a higher-level package
build:

```bash
IMAGE_OUTPUT_DIR=${PROCESS_ROOT}/temp_image_package \
CHART_OUTPUT_DIR=${PROCESS_ROOT}/temp_chart_package \
bash build-gateway.sh
```

The default image names and tags intentionally match `package-gateway/charts/aidp-gateway/values.yaml`, so the chart can be installed without image override values after loading these tar files.

Expected tar names:

```text
docker.io_alpine_kubectl_1.34.1.tar
docker.io_envoyproxy_gateway_v1.7.2.tar
docker.io_envoyproxy_envoy_v1.36.5.tar
docker.io_library_gateway-cert-manager_v1.tar
```

## Helm values

The chart defaults already match the generated images:

```yaml
gateway-helm:
  deployment:
    envoyGateway:
      image:
        repository: docker.io/envoyproxy/gateway
        tag: v1.7.2

proxy:
  image: docker.io/envoyproxy/envoy:v1.36.5

cleanup:
  image:
    repository: docker.io/alpine/kubectl
    tag: 1.34.1

certificateManager:
  image:
    repository: gateway-cert-manager
    tag: v1
```
