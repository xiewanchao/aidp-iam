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
| `agentinfra-kubectl:1.34.1-openeuler` | `kubernetes-client-linux-{arch}.tar.gz` |
| `agentinfra-envoy-gateway:v1.7.2-openeuler` | `envoy-gateway_v1.7.2_linux_{arch}.tar.gz` |
| `agentinfra-envoy:v1.36.5-openeuler` | `envoy-1.36.5-linux-aarch_64` or `envoy-1.36.5-linux-x86_64` |
| `gateway-cert-manager:v1-openeuler` | `Python-3.11.4.tar.xz`, local `gateway-cert-manager` source, and Python dependencies |

## Usage

Build arm64 images:

```bash
cd package-gateway/images/openeuler
bash build-gateway-images.sh
```

Build amd64 images by providing an amd64 EulerOS Docker archive:

```bash
ARCH=amd64 \
EULEROS_DOCKER_DOWNLOAD_URL=<amd64-euleros-docker-tar-xz-url> \
bash build-gateway-images.sh
```

Override the Python package index for `gateway-cert-manager`:

```bash
PIP_INDEX_URL=https://<internal-pypi>/simple/ \
PIP_TRUSTED_HOST=<internal-pypi-host> \
bash build-gateway-images.sh
```

Override the Python source package if needed:

```bash
PYTHON_SOURCE_DOWNLOAD_URL=https://cmc.cloudartifact.szv.dragon.tools.huawei.com/artifactory/opensource_general/Python/3.11.4/package/Python-3.11.4.tar.xz \
bash build-gateway-images.sh
```

`gateway-cert-manager` builds Python 3.11.4 from source inside the EulerOS
image so the runtime Python ABI matches the final base image. The build machine
only downloads the approved `Python-3.11.4.tar.xz` artifact into the Docker
build context.

By default, image tar files are saved under:

```text
package-gateway/images/{arm64|amd64}/
```

## Helm values

Install the chart with the generated image names:

```yaml
gateway-helm:
  deployment:
    envoyGateway:
      image:
        repository: agentinfra-envoy-gateway
        tag: v1.7.2-openeuler

proxy:
  image: agentinfra-envoy:v1.36.5-openeuler

cleanup:
  image:
    repository: agentinfra-kubectl
    tag: 1.34.1-openeuler

certificateManager:
  image:
    repository: gateway-cert-manager
    tag: v1-openeuler
```
