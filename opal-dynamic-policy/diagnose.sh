# 开启网关的调试日志（临时）
kubectl set env deploy/envoy-eg -n envoy-gateway-system RUST_LOG=debug

# 重启网关
kubectl rollout restart deploy/envoy-eg -n envoy-gateway-system

# 触发一个请求
curl -H "Authorization: Bearer $ADMIN_JWT" \
  http://localhost:8080/api/v1/policies/templates

# 查看详细日志
kubectl logs -n envoy-gateway-system deploy/envoy-eg --tail=10 | grep -i "ext_auth"

